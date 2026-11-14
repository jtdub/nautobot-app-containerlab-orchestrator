"""Tests for the ContainerlabHost, NodeMapping, and InterfaceMapping models."""

import uuid
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import ProtectedError
from jinja2 import TemplateError
from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.apps.testing import ModelTestCases, TestCase
from nautobot.dcim.models import DeviceType, Manufacturer, Platform
from nautobot.extras.models import Secret, SecretsGroupAssociation

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_http_secrets_group,
    create_interface_mappings,
    create_node_mappings,
)

MISSING_USERNAME_MESSAGE = "The Secrets Group must have an HTTP(S) username secret."
MISSING_PASSWORD_MESSAGE = "The Secrets Group must have an HTTP(S) password secret."
BOTH_TARGETS_MESSAGE = "Set a Device Type or a Platform, but not both."
NO_TARGET_MESSAGE = "Set a Device Type or a Platform."


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
        row = InterfaceMapping(node_mapping_id=uuid.uuid4(), physical_name="Gi0/9", container_name="eth9")
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

    def test_empty_names_fail_validation(self):
        for field_name in ("physical_name", "container_name"):
            with self.subTest(field_name=field_name):
                values = {"node_mapping": self.nokia, "physical_name": "Ethernet9", "container_name": "eth9"}
                values[field_name] = ""
                self.assert_error(InterfaceMapping(**values), field_name)

    def test_bad_names_fail_validation(self):
        for field_name, value in (("physical_name", "Ethernet9 "), ("container_name", "eth 9")):
            with self.subTest(field_name=field_name):
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
