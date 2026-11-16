"""Tests for the ContainerlabHost, NodeMapping, InterfaceMapping, Lab, and LabNode models."""

import uuid
from decimal import Decimal
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from jinja2 import TemplateError
from nautobot.apps.change_logging import web_request_context
from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.apps.testing import ModelTestCases, TestCase
from nautobot.dcim.models import DeviceType, Location, Manufacturer, Platform
from nautobot.extras.models import DynamicGroup, ObjectChange, Secret, SecretsGroupAssociation, Status

from containerlab_orchestrator import config_sources
from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.config_sources import GOLDEN_CONFIG_MESSAGE
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_http_secrets_group,
    create_interface_mappings,
    create_lab_devices,
    create_lab_nodes,
    create_labs,
    create_node_mappings,
)

MISSING_USERNAME_MESSAGE = "The Secrets Group must have an HTTP(S) username secret."
MISSING_PASSWORD_MESSAGE = "The Secrets Group must have an HTTP(S) password secret."
BOTH_TARGETS_MESSAGE = "Set a Device Type or a Platform, but not both."
NO_TARGET_MESSAGE = "Set a Device Type or a Platform."
DUPLICATE_LAB_MESSAGE = "A lab with this name already exists on this host."
PLANNED_NOT_AVAILABLE_MESSAGE = "The Planned status is not available for labs. Ask an administrator to add it."


class ContainerlabHostModelTestCase(ModelTestCases.BaseModelTestCase):
    """Run the generic Nautobot model tests on ContainerlabHost."""

    model = ContainerlabHost

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()

    def test_get_docs_url(self):
        """Skip the documentation check, because issue #25 adds the host documentation page."""
        self.skipTest("The documentation page for this model comes in #25.")

    def test_str_returns_name(self):
        host = ContainerlabHost.objects.get(name="clab01")
        self.assertEqual(str(host), "clab01")

    def test_verify_ssl_defaults_to_true(self):
        host = ContainerlabHost(name="clab-default", remote_url="https://clab-default:8080")
        self.assertTrue(host.verify_ssl)


class ContainerlabHostCleanTestCase(TestCase):
    """Test the validation of a host and its Secrets Group."""

    def build_host(self, secrets_group, name="clab01", remote_url="https://clab01:8080"):
        """Return an unsaved host with the given Secrets Group."""
        return ContainerlabHost(name=name, remote_url=remote_url, secrets_group=secrets_group)

    def assert_secrets_group_error(self, host, message):
        """Assert that full_clean() reports the message on the secrets_group field."""
        with self.assertRaises(ValidationError) as context:
            host.full_clean()
        self.assertIn(message, context.exception.message_dict["secrets_group"])

    def test_clean_accepts_group_with_http_username_and_password(self):
        host = self.build_host(create_http_secrets_group("Complete"))
        host.full_clean()

    def test_clean_rejects_group_without_http_username(self):
        host = self.build_host(create_http_secrets_group("No username", username=False))
        self.assert_secrets_group_error(host, MISSING_USERNAME_MESSAGE)

    def test_clean_rejects_group_without_http_password(self):
        host = self.build_host(create_http_secrets_group("No password", password=False))
        self.assert_secrets_group_error(host, MISSING_PASSWORD_MESSAGE)

    def test_clean_rejects_group_without_any_http_secret(self):
        host = self.build_host(create_http_secrets_group("Empty", username=False, password=False))
        with self.assertRaises(ValidationError) as context:
            host.full_clean()
        self.assertEqual(
            context.exception.message_dict["secrets_group"],
            ["The Secrets Group must have an HTTP(S) username secret and an HTTP(S) password secret."],
        )

    def test_clean_rejects_group_with_username_of_other_access_type(self):
        secrets_group = create_http_secrets_group("SSH username", username=False)
        SecretsGroupAssociation.objects.create(
            secrets_group=secrets_group,
            secret=Secret.objects.create(
                name="SSH username secret",
                provider="environment-variable",
                parameters={"variable": "CLAB_SSH_USERNAME"},
            ),
            access_type=SecretsGroupAccessTypeChoices.TYPE_SSH,
            secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
        )
        self.assert_secrets_group_error(self.build_host(secrets_group), MISSING_USERNAME_MESSAGE)

    def test_clean_without_secrets_group_reports_required_field(self):
        host = self.build_host(secrets_group=None)
        with self.assertRaises(ValidationError) as context:
            host.full_clean()
        self.assertEqual(context.exception.message_dict["secrets_group"], ["This field cannot be null."])

    def test_clean_with_unknown_secrets_group_raises_validation_error(self):
        host = ContainerlabHost(name="clab01", remote_url="https://clab01:8080", secrets_group_id=uuid.uuid4())
        with self.assertRaises(ValidationError) as context:
            host.full_clean()
        self.assertEqual(len(context.exception.message_dict["secrets_group"]), 1)

    def test_duplicate_name_fails_validation(self):
        secrets_group = create_http_secrets_group("Shared")
        self.build_host(secrets_group).validated_save()
        duplicate = self.build_host(secrets_group, remote_url="https://clab02:8080")
        with self.assertRaises(ValidationError) as context:
            duplicate.full_clean()
        self.assertIn("name", context.exception.message_dict)

    def test_duplicate_url_is_valid(self):
        secrets_group = create_http_secrets_group("Shared")
        self.build_host(secrets_group).validated_save()
        self.build_host(secrets_group, name="clab02").full_clean()

    def test_url_accepts_short_host_name(self):
        host = self.build_host(create_http_secrets_group("Short"), remote_url="http://clab01:8080")
        host.full_clean()

    def test_url_rejects_scheme_other_than_http_and_https(self):
        host = self.build_host(create_http_secrets_group("SSH scheme"), remote_url="ssh://clab01:8080")
        with self.assertRaises(ValidationError) as context:
            host.full_clean()
        self.assertIn("remote_url", context.exception.message_dict)

    def test_url_rejects_malformed_ipv6_host_and_port_out_of_range(self):
        secrets_group = create_http_secrets_group("Bad URL")
        for url in ("https://[::1", "https://clab01:99999"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError) as context:
                    self.build_host(secrets_group, remote_url=url).full_clean()
                self.assertEqual(context.exception.message_dict["remote_url"], ["Enter a valid URL."])

    def test_url_rejects_user_name_and_password(self):
        secrets_group = create_http_secrets_group("User info")
        for url in ("https://admin:secret@clab01:8080", "https://admin@clab01:8080"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError) as context:
                    self.build_host(secrets_group, remote_url=url).full_clean()
                self.assertEqual(
                    context.exception.message_dict["remote_url"],
                    ["Do not put a username or a password in the URL. Use the Secrets Group."],
                )


class ContainerlabHostDeleteTestCase(TestCase):
    """Test the protection of a Secrets Group that a host uses."""

    def test_delete_secrets_group_in_use_raises_protected_error(self):
        secrets_group = create_http_secrets_group("In use")
        ContainerlabHost.objects.create(name="clab01", remote_url="https://clab01:8080", secrets_group=secrets_group)
        with self.assertRaises(ProtectedError):
            secrets_group.delete()


class NodeMappingModelTestCase(ModelTestCases.BaseModelTestCase):
    """Run the generic Nautobot model tests on NodeMapping."""

    model = NodeMapping

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()

    def test_get_docs_url(self):
        """Skip the documentation check, because issue #25 adds the node mapping documentation page."""
        self.skipTest("The documentation page for this model comes in #25.")

    def test_natural_key_and_composite_key_round_trip_for_device_type_and_platform(self):
        for mapping in (
            NodeMapping.objects.get(device_type__model="DCS-7280"),
            NodeMapping.objects.get(platform__name="linux"),
        ):
            with self.subTest(mapping=str(mapping)):
                self.assertEqual(NodeMapping.objects.get_by_natural_key(*mapping.natural_key()), mapping)
                self.assertEqual(NodeMapping.objects.get(composite_key=mapping.composite_key), mapping)

    def test_str_shows_device_type_or_platform(self):
        self.assertEqual(str(NodeMapping.objects.get(device_type__model="DCS-7280")), "Device Type: Arista DCS-7280")
        self.assertEqual(str(NodeMapping.objects.get(platform__name="linux")), "Platform: linux")

    def test_str_without_target_gives_generic_name(self):
        self.assertEqual(str(NodeMapping(kind="ceos", image="ceos:4.32.0F")), "Node mapping")

    def test_env_help_text_says_values_are_not_secret(self):
        self.assertIn("not secret", NodeMapping._meta.get_field("env").help_text)

    def test_overrides_help_text_warns_about_host_keys(self):
        help_text = NodeMapping._meta.get_field("overrides").help_text
        self.assertIn("WARNING", help_text)
        self.assertIn("binds", help_text)


class NodeMappingValidationTestCase(TestCase):
    """Base class with the data and the helpers for the node mapping validation tests."""

    @classmethod
    def setUpTestData(cls):
        cls.device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.create(name="Arista"), model="DCS-7280"
        )
        cls.platform = Platform.objects.create(name="linux")

    def build_mapping(self, **fields):
        """Return an unsaved, valid Device Type mapping with the given fields changed."""
        values = {"device_type": self.device_type, "kind": "ceos", "image": "ceos:4.32.0F"}
        values.update(fields)
        return NodeMapping(**values)

    def assert_field_error(self, mapping, field_name, message=None):
        """Assert that full_clean() reports an error on the field, with the message if one is given."""
        with self.assertRaises(ValidationError) as context:
            mapping.full_clean()
        self.assertIn(field_name, context.exception.message_dict)
        if message is not None:
            self.assertIn(message, context.exception.message_dict[field_name])


class NodeMappingCleanTestCase(NodeMappingValidationTestCase):
    """Test the target, JSON, and node field rules of a node mapping."""

    def test_valid_mapping_passes_validation(self):
        self.build_mapping().full_clean()
        self.build_mapping(device_type=None, platform=self.platform).full_clean()

    def test_mapping_with_both_targets_fails_validation(self):
        mapping = self.build_mapping(platform=self.platform)
        self.assert_field_error(mapping, "__all__", BOTH_TARGETS_MESSAGE)

    def test_mapping_with_no_target_fails_validation(self):
        mapping = self.build_mapping(device_type=None)
        self.assert_field_error(mapping, "__all__", NO_TARGET_MESSAGE)

    def test_duplicate_device_type_fails_validation(self):
        self.build_mapping().validated_save()
        self.assert_field_error(self.build_mapping(image="ceos:4.33.0F"), "device_type")

    def test_duplicate_platform_fails_validation(self):
        self.build_mapping(device_type=None, platform=self.platform).validated_save()
        self.assert_field_error(self.build_mapping(device_type=None, platform=self.platform), "platform")

    def test_env_and_overrides_accept_empty_object(self):
        self.build_mapping(env={}, overrides={}).full_clean()

    def test_env_and_overrides_reject_value_that_is_not_object(self):
        for field_name in ("env", "overrides"):
            for value in ([], "", (), [1], "text", 5):
                with self.subTest(field_name=field_name, value=value):
                    self.assert_field_error(
                        self.build_mapping(**{field_name: value}),
                        field_name,
                        'Enter a JSON object, for example {"key": "value"}.',
                    )

    def test_env_rejects_value_that_is_not_string(self):
        for value in (1, ["1"], {"B": "1"}):
            with self.subTest(value=value):
                self.assert_field_error(
                    self.build_mapping(env={"A": value}), "env", "Each key and each value must be a string."
                )

    def test_kind_and_image_are_checked(self):
        self.assert_field_error(self.build_mapping(kind="Ceos"), "kind")
        self.assert_field_error(self.build_mapping(kind=""), "kind")
        self.assert_field_error(self.build_mapping(image="alpine latest"), "image")
        self.assert_field_error(self.build_mapping(image=""), "image")

    def test_cpu_accepts_positive_decimal(self):
        for value in (Decimal("0.5"), 2):
            with self.subTest(value=value):
                self.build_mapping(cpu=value).full_clean()

    def test_cpu_rejects_zero_negative_and_three_decimal_places(self):
        for value in (0, -1, Decimal("0.001")):
            with self.subTest(value=value):
                self.assert_field_error(self.build_mapping(cpu=value), "cpu")

    def test_memory_accepts_empty_value_and_number_with_unit(self):
        for value in ("", "512mb", "2 GB"):
            with self.subTest(value=value):
                self.build_mapping(memory=value).full_clean()

    def test_memory_rejects_number_without_unit(self):
        self.assert_field_error(
            self.build_mapping(memory="512"), "memory", "Enter a number and a unit, for example 512mb or 1Gb."
        )


class NodeMappingConfigSourceTestCase(NodeMappingValidationTestCase):
    """Test the config source and config template rules of a node mapping."""

    def test_config_source_defaults_to_lab_default(self):
        self.assertEqual(self.build_mapping().config_source, "")

    def test_config_source_rejects_unknown_value(self):
        self.assert_field_error(self.build_mapping(config_source="ansible"), "config_source")

    def test_jinja2_source_without_template_fails_validation(self):
        self.assert_field_error(
            self.build_mapping(config_source=NodeMappingConfigSourceChoices.JINJA2),
            "config_template",
            "Enter a template when the config source is Jinja2.",
        )

    def test_template_with_syntax_error_fails_validation_with_any_source(self):
        for config_source in (NodeMappingConfigSourceChoices.JINJA2, NodeMappingConfigSourceChoices.NONE):
            with self.subTest(config_source=config_source):
                with self.assertRaises(ValidationError) as context:
                    self.build_mapping(config_source=config_source, config_template="{% if %}").full_clean()
                self.assertTrue(
                    context.exception.message_dict["config_template"][0].startswith("Template syntax error on line")
                )

    def test_valid_template_with_none_source_passes_validation(self):
        self.build_mapping(
            config_source=NodeMappingConfigSourceChoices.NONE, config_template="hostname {{ device.name }}"
        ).full_clean()

    def test_template_error_that_is_not_syntax_error_fails_validation(self):
        mapping = self.build_mapping(config_template="hostname {{ device.name }}")
        with mock.patch("containerlab_orchestrator.models.validate_jinja2", side_effect=TemplateError("bad filter")):
            self.assert_field_error(mapping, "config_template", "Template error: bad filter")


class NodeMappingDeleteTestCase(TestCase):
    """Test that a mapping goes away with its Device Type or its Platform."""

    def test_delete_device_type_deletes_mapping(self):
        device_type = DeviceType.objects.create(manufacturer=Manufacturer.objects.create(name="Arista"), model="X")
        NodeMapping.objects.create(device_type=device_type, kind="ceos", image="ceos:4.32.0F")
        device_type.delete()
        self.assertFalse(NodeMapping.objects.exists())

    def test_delete_platform_deletes_mapping(self):
        platform = Platform.objects.create(name="linux")
        NodeMapping.objects.create(platform=platform, kind="linux", image="alpine:latest")
        platform.delete()
        self.assertFalse(NodeMapping.objects.exists())

    def test_delete_without_deferred_constraint_checks_deletes_mapping(self):
        """Delete a Device Type and a Platform the way that MySQL does, with no deferred constraint checks."""
        device_type = DeviceType.objects.create(manufacturer=Manufacturer.objects.create(name="Nokia"), model="Y")
        platform = Platform.objects.create(name="eos")
        NodeMapping.objects.create(device_type=device_type, kind="ceos", image="ceos:4.32.0F")
        NodeMapping.objects.create(platform=platform, kind="ceos", image="ceos:4.32.0F")
        with mock.patch.object(connection.features, "can_defer_constraint_checks", False):
            device_type.delete()
            platform.delete()
        self.assertFalse(NodeMapping.objects.exists())


class InterfaceMappingModelTestCase(ModelTestCases.BaseModelTestCase):
    """Run the generic Nautobot model tests on InterfaceMapping."""

    model = InterfaceMapping

    @classmethod
    def setUpTestData(cls):
        create_interface_mappings(create_node_mappings())

    def test_get_docs_url(self):
        """Skip the documentation check, because issue #25 adds the interface mapping documentation page."""
        self.skipTest("The documentation page for this model comes in #25.")

    def test_natural_key_and_composite_key_round_trip_for_device_type_and_platform_rows(self):
        for row in (
            InterfaceMapping.objects.get(physical_name="GigabitEthernet0/1"),
            InterfaceMapping.objects.get(physical_name="ens3"),
        ):
            with self.subTest(row=str(row)):
                self.assertEqual(InterfaceMapping.objects.get_by_natural_key(*row.natural_key()), row)
                self.assertEqual(InterfaceMapping.objects.get(composite_key=row.composite_key), row)

    def test_rows_use_natural_interface_order(self):
        rows = InterfaceMapping.objects.filter(node_mapping__device_type__model="DCS-7280")
        self.assertEqual(
            [row.physical_name for row in rows],
            ["GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/10"],
        )

    def test_str_shows_physical_and_container_name(self):
        row = InterfaceMapping.objects.get(physical_name="GigabitEthernet0/1")
        self.assertEqual(str(row), "GigabitEthernet0/1 → eth1")

    def test_to_objectchange_relates_change_to_node_mapping(self):
        row = InterfaceMapping.objects.get(physical_name="GigabitEthernet0/1")
        self.assertEqual(row.to_objectchange("update").related_object, row.node_mapping)

    def test_to_objectchange_keeps_related_object_that_caller_gives(self):
        row = InterfaceMapping.objects.get(physical_name="GigabitEthernet0/1")
        self.assertIsNone(row.to_objectchange("update", related_object=None).related_object)

    def test_to_objectchange_without_node_mapping_has_no_related_object(self):
        """Patch the REST API form of the change, because the serializer also reads the gone node mapping."""
        row = InterfaceMapping(node_mapping_id=uuid.uuid4(), physical_name="Gi0/9", container_name="eth9")
        with mock.patch("nautobot.extras.models.change_logging.serialize_object_v2", return_value={}):
            self.assertIsNone(row.to_objectchange("delete").related_object)

    def test_get_absolute_url_opens_node_mapping(self):
        row = InterfaceMapping.objects.get(physical_name="GigabitEthernet0/1")
        self.assertEqual(row.get_absolute_url(), row.node_mapping.get_absolute_url())


class InterfaceMappingValidationTestCase(TestCase):
    """Test the name rules of an interface row."""

    @classmethod
    def setUpTestData(cls):
        cls.arista, cls.nokia, _ = create_node_mappings()
        InterfaceMapping.objects.create(node_mapping=cls.arista, physical_name="Ethernet1", container_name="eth1")

    def assert_error(self, row, key):
        """Assert that full_clean() reports an error under the key."""
        with self.assertRaises(ValidationError) as context:
            row.full_clean()
        self.assertIn(key, context.exception.message_dict)

    def test_duplicate_physical_name_in_one_mapping_fails_validation(self):
        row = InterfaceMapping(node_mapping=self.arista, physical_name="Ethernet1", container_name="eth2")
        self.assert_error(row, "__all__")

    def test_duplicate_container_name_in_one_mapping_fails_validation(self):
        row = InterfaceMapping(node_mapping=self.arista, physical_name="Ethernet2", container_name="eth1")
        self.assert_error(row, "__all__")

    def test_same_names_on_two_mappings_pass(self):
        InterfaceMapping(node_mapping=self.nokia, physical_name="Ethernet1", container_name="eth1").validated_save()

    def test_empty_and_bad_names_fail_validation(self):
        for field_name, value in (
            ("physical_name", ""),
            ("container_name", ""),
            ("physical_name", "Ethernet9 "),
            ("container_name", "eth 9"),
        ):
            with self.subTest(field_name=field_name, value=value):
                values = {"node_mapping": self.nokia, "physical_name": "Ethernet9", "container_name": "eth9"}
                values[field_name] = value
                self.assert_error(InterfaceMapping(**values), field_name)


class InterfaceMappingDeleteTestCase(TestCase):
    """Test that the rows go away with their node mapping, Device Type, or Platform."""

    @classmethod
    def setUpTestData(cls):
        create_interface_mappings(create_node_mappings())

    def test_delete_node_mapping_deletes_rows(self):
        NodeMapping.objects.get(device_type__model="DCS-7280").delete()
        self.assertFalse(InterfaceMapping.objects.filter(physical_name__startswith="GigabitEthernet").exists())
        self.assertTrue(InterfaceMapping.objects.filter(physical_name="ens3").exists())

    def test_delete_device_type_deletes_rows(self):
        DeviceType.objects.get(model="DCS-7280").delete()
        self.assertEqual(list(InterfaceMapping.objects.values_list("physical_name", flat=True)), ["ens3"])

    def test_delete_platform_deletes_rows(self):
        Platform.objects.get(name="linux").delete()
        self.assertFalse(InterfaceMapping.objects.filter(physical_name="ens3").exists())
        self.assertEqual(InterfaceMapping.objects.count(), 3)


def create_lab_test_data():
    """Return the saved hosts, lab devices, labs, and lab nodes, and the Planned status."""
    hosts = create_containerlab_hosts()
    lab_devices = create_lab_devices()
    labs = create_labs(hosts, lab_devices)
    lab_nodes = create_lab_nodes(labs, lab_devices)
    return hosts, lab_devices, labs, lab_nodes, Status.objects.get(name=LabStatusChoices.PLANNED)


class LabTestCase(TestCase):
    """Base class with the lab test data."""

    @classmethod
    def setUpTestData(cls):
        cls.hosts, cls.lab_devices, cls.labs, cls.lab_nodes, cls.planned = create_lab_test_data()


class LabModelTestCase(ModelTestCases.BaseModelTestCase):
    """Run the generic Nautobot model tests on Lab."""

    model = Lab

    @classmethod
    def setUpTestData(cls):
        cls.hosts, cls.lab_devices, cls.labs, cls.lab_nodes, cls.planned = create_lab_test_data()

    def test_get_docs_url(self):
        """Skip the documentation check, because issue #25 adds the lab documentation page."""
        self.skipTest("The documentation page for this model comes in #25.")

    def test_str_returns_name(self):
        self.assertEqual(str(self.labs[0]), "lab-a")

    def test_natural_key_is_host_name_and_lab_name(self):
        self.assertEqual(self.labs[0].natural_key(), ["clab01", "lab-a"])
        self.assertEqual(Lab.objects.get_by_natural_key("clab01", "lab-a"), self.labs[0])


class LabValidationTestCase(LabTestCase):
    """Base class with the helpers for the lab validation tests."""

    def build_lab(self, **kwargs):
        """Return an unsaved Planned lab on clab01 with a free name and subnet, changed by the keyword arguments."""
        values = {"name": "new-lab", "host": self.hosts[0], "mgmt_subnet": "192.168.1.0/24", "status": self.planned}
        values.update(kwargs)
        return Lab(**values)

    def assert_errors(self, lab, errors):
        """Assert that full_clean() raises exactly these errors."""
        with self.assertRaises(ValidationError) as context:
            lab.full_clean()
        self.assertEqual(context.exception.message_dict, errors)


class LabNameCleanTestCase(LabValidationTestCase):  # pylint: disable=too-many-ancestors
    """Test the unique name check of a lab."""

    def test_duplicate_name_on_same_host_fails_validation(self):
        for name in ("lab-a", "LAB-A"):
            with self.subTest(name=name):
                self.assert_errors(self.build_lab(name=name), {"name": [DUPLICATE_LAB_MESSAGE]})

    def test_same_name_on_other_host_passes_validation(self):
        self.build_lab(name="lab-b", host=self.hosts[1]).full_clean()

    def test_edit_of_lab_passes_validation(self):
        lab = self.labs[0]
        lab.config_source = NodeMappingConfigSourceChoices.JINJA2
        lab.full_clean()


class LabSelectionCleanTestCase(LabValidationTestCase):  # pylint: disable=too-many-ancestors
    """Test the validation of the selection and the subnet room of a lab."""

    def test_device_filter_that_is_not_object_fails_validation(self):
        self.assert_errors(
            self.build_lab(device_filter=["leaf"]),
            {"device_filter": ['Enter a JSON object, for example {"key": "value"}.']},
        )
        self.build_lab(device_filter={}).full_clean()

    def test_device_filter_that_is_not_valid_fails_validation(self):
        self.assert_errors(
            self.build_lab(device_filter={"bogus": ["1"]}), {"device_filter": ["bogus: Unknown filter field."]}
        )
        self.build_lab(device_filter={"role": ["leaf"]}).full_clean()

    def test_unchanged_stale_filter_passes_validation(self):
        lab = self.build_lab(device_filter={"role": ["leaf"]})
        lab.save()
        Lab.objects.filter(pk=lab.pk).update(device_filter={"role": ["gone"]})
        lab.refresh_from_db()
        lab.name = "new-lab-2"
        lab.full_clean()
        lab.device_filter = {"role": ["nope"]}
        self.assert_errors(
            lab,
            {"device_filter": ["role: Select a valid choice. nope is not one of the available choices."]},
        )

    def test_dynamic_group_that_is_not_of_devices_fails_validation(self):
        location_group = DynamicGroup.objects.create(
            name="Lab locations", content_type=ContentType.objects.get_for_model(Location)
        )
        self.assert_errors(
            self.build_lab(dynamic_group=location_group), {"dynamic_group": ["Select a Dynamic Group of devices."]}
        )
        self.build_lab(dynamic_group=self.lab_devices["leaf_group"]).full_clean()

    def test_subnet_without_room_for_saved_nodes_fails_validation(self):
        lab = self.labs[0]
        lab.mgmt_subnet = "172.20.21.0/30"
        self.assert_errors(
            lab, {"mgmt_subnet": ["Node addresses in the subnet: 1. Nodes in the lab: 3. Use a larger subnet."]}
        )
        lab.mgmt_subnet = "172.20.21.0/29"
        lab.full_clean()
        self.build_lab(mgmt_subnet="10.0.0.0/30").full_clean()


class GoldenConfigNotInstalledCleanTestCase(LabValidationTestCase):  # pylint: disable=too-many-ancestors
    """Test the config source rule of node mappings and labs when Golden Config is not installed."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.platforms = [Platform.objects.create(name=name) for name in ("gc-saved", "gc-new")]

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(config_sources, "golden_config_installed", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def new_mapping(self, platform, **fields):
        """Return an unsaved, valid Platform mapping with the given fields changed."""
        return NodeMapping(platform=platform, kind="linux", image="alpine:latest", **fields)

    def test_not_installed_clean_rejects_new_golden_config_source(self):
        intended = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED
        backup = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP
        changed_mapping = self.new_mapping(self.platforms[0], config_source=backup)
        NodeMapping.objects.bulk_create([changed_mapping])
        changed_mapping.config_source = intended
        changed_lab = self.labs[2]
        changed_lab.config_source = backup
        cases = {
            "new mapping": self.new_mapping(self.platforms[1], config_source=backup),
            "changed mapping": changed_mapping,
            "new lab": self.build_lab(config_source=intended),
            "changed lab": changed_lab,
        }
        for name, instance in cases.items():
            with self.subTest(case=name):
                self.assert_errors(instance, {"config_source": [GOLDEN_CONFIG_MESSAGE]})

    def test_not_installed_save_of_other_field_passes(self):
        backup = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP
        mapping = self.new_mapping(self.platforms[0], config_source=backup)
        NodeMapping.objects.bulk_create([mapping])
        lab = self.labs[2]

        mapping.memory = "1Gb"
        mapping.validated_save()
        lab.name = "lab-gc"
        lab.validated_save()
        self.new_mapping(
            self.platforms[1], config_source=NodeMappingConfigSourceChoices.JINJA2, config_template="x"
        ).full_clean()
        self.build_lab(config_source=NodeMappingConfigSourceChoices.NONE).full_clean()

        mapping.refresh_from_db()
        lab.refresh_from_db()
        self.assertEqual((mapping.memory, mapping.config_source), ("1Gb", backup))
        self.assertEqual(
            (lab.name, lab.config_source), ("lab-gc", NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED)
        )


class LabDatabaseRuleTestCase(LabTestCase):
    """Test the database rules of a lab."""

    def test_database_rejects_name_that_differs_only_in_case_on_same_host(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Lab.objects.create(name="LAB-A", host=self.hosts[0], mgmt_subnet="192.168.2.0/24", status=self.planned)
        Lab.objects.create(name="LAB-A", host=self.hosts[2], mgmt_subnet="192.168.2.0/24", status=self.planned)


class LabCreateDefaultsTestCase(LabTestCase):
    """Test the owner and the status that a new lab gets."""

    def test_set_create_defaults_sets_owner_and_planned_status(self):
        alice = self.labs[0].owner
        lab = Lab(name="new-lab", host=self.hosts[2], mgmt_subnet="192.168.3.0/24")
        lab.set_create_defaults(alice)
        self.assertEqual(lab.owner, alice)
        self.assertEqual(lab.status.name, "Planned")
        lab.full_clean()

    def test_set_create_defaults_without_planned_status_for_labs_fails(self):
        self.planned.content_types.remove(ContentType.objects.get_for_model(Lab))
        lab = Lab(name="new-lab", host=self.hosts[2], mgmt_subnet="192.168.3.0/24")
        with self.assertRaises(ValidationError) as context:
            lab.set_create_defaults(self.labs[0].owner)
        self.assertEqual(context.exception.messages, [PLANNED_NOT_AVAILABLE_MESSAGE])


class LabChangeLogTestCase(LabTestCase):
    """Test the change log data of a lab."""

    def test_lab_change_log_has_no_topology_yaml(self):
        lab = self.labs[0]
        with web_request_context(self.user):
            lab.topology_yaml = "topology:\n  nodes: {}\n"
            lab.validated_save()
        change = ObjectChange.objects.filter(changed_object_id=lab.pk).latest("time")
        for data in (change.object_data, change.object_data_v2):
            self.assertEqual(data["name"], "lab-a")
            self.assertNotIn("topology_yaml", data)


class LabStatusMigrationTestCase(TestCase):
    """Test the lab statuses that the data migration links."""

    def test_migration_links_the_five_lab_statuses(self):
        statuses = Status.objects.get_for_model(Lab)
        self.assertEqual(
            set(statuses.values_list("name", flat=True)), {"Planned", "Built", "Deployed", "Failed", "Destroyed"}
        )
        colors = dict(statuses.filter(name__in=["Built", "Deployed", "Destroyed"]).values_list("name", "color"))
        self.assertEqual(colors, {"Built": "2196f3", "Deployed": "4caf50", "Destroyed": "9e9e9e"})


class LabDeleteTestCase(LabTestCase):
    """Test what a delete of a related object does to a lab and its nodes."""

    def test_delete_host_with_labs_raises_protected_error(self):
        with self.assertRaises(ProtectedError):
            self.hosts[0].delete()

    def test_delete_owner_keeps_lab_without_owner(self):
        self.labs[0].owner.delete()
        self.labs[0].refresh_from_db()
        self.assertIsNone(self.labs[0].owner)

    def test_delete_dynamic_group_keeps_lab_without_group(self):
        self.lab_devices["leaf_group"].delete()
        self.labs[1].refresh_from_db()
        self.assertIsNone(self.labs[1].dynamic_group)

    def test_delete_device_keeps_node_row_without_device(self):
        self.lab_devices["spine1"].delete()
        self.assertEqual(self.labs[0].devices.count(), 0)
        self.assertIsNone(LabNode.objects.get(lab=self.labs[0], node_name="spine1").device)

    def test_delete_lab_deletes_its_node_rows_only(self):
        self.labs[0].delete()
        self.assertEqual(LabNode.objects.filter(lab__name="lab-a", lab__host__name="clab01").count(), 0)
        self.assertTrue(LabNode.objects.filter(lab=self.labs[2], node_name="leaf1").exists())


class LabNodeModelTestCase(ModelTestCases.BaseModelTestCase):
    """Run the generic Nautobot model tests on LabNode."""

    model = LabNode

    @classmethod
    def setUpTestData(cls):
        cls.hosts, cls.lab_devices, cls.labs, cls.lab_nodes, cls.planned = create_lab_test_data()

    def test_get_docs_url(self):
        """Skip the documentation check, because issue #25 adds the lab documentation page."""
        self.skipTest("The documentation page for this model comes in #25.")

    def test_str_returns_node_name(self):
        self.assertEqual(str(self.lab_nodes[0]), "leaf1")

    def test_duplicate_node_name_on_lab_fails_validation(self):
        node = LabNode(lab=self.labs[0], node_name="leaf1", kind="linux", image="alpine:latest")
        with self.assertRaises(ValidationError):
            node.full_clean()

    def test_stub_node_with_device_fails_validation(self):
        node = LabNode(
            lab=self.labs[0],
            node_name="stub-wan9",
            is_stub=True,
            device=self.lab_devices["leaf1"],
            kind="linux",
            image="alpine:latest",
        )
        with self.assertRaises(ValidationError) as context:
            node.full_clean()
        self.assertEqual(context.exception.message_dict, {"device": ["A stub node has no device."]})

    def test_node_that_is_not_stub_without_device_passes_validation(self):
        LabNode(lab=self.labs[0], node_name="leaf9", kind="linux", image="alpine:latest").full_clean()
