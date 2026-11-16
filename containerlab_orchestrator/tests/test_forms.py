"""Tests for the containerlab_orchestrator forms."""

import re
from unittest import mock

from nautobot.apps.testing import FormTestCases, TestCase
from nautobot.dcim.models import DeviceType, Manufacturer, Platform
from nautobot.extras.models import Status

from containerlab_orchestrator import config_sources
from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.forms import (
    ContainerlabHostFilterForm,
    ContainerlabHostForm,
    LabBulkEditForm,
    LabFilterForm,
    LabForm,
    NodeMappingBulkEditForm,
    NodeMappingFilterForm,
    NodeMappingForm,
)
from containerlab_orchestrator.models import Lab, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_http_secrets_group,
    create_node_mappings,
)
from containerlab_orchestrator.tests.test_models import (
    BOTH_TARGETS_MESSAGE,
    MISSING_PASSWORD_MESSAGE,
    NO_TARGET_MESSAGE,
)


class ContainerlabHostFormTestCase(FormTestCases.BaseFormTestCase):
    """Test the ContainerlabHost create and edit form."""

    form_class = ContainerlabHostForm

    @staticmethod
    def _form_data(secrets_group):
        return {
            "name": "clab-form-01",
            "remote_url": "http://clab-form-01:8080",
            "verify_ssl": True,
            "secrets_group": secrets_group.pk,
            "description": "Form host",
        }

    def test_form_is_valid_with_complete_secrets_group(self):
        form = ContainerlabHostForm(data=self._form_data(create_http_secrets_group("Form credentials")))

        self.assertTrue(form.is_valid(), form.errors)
        host = form.save()
        self.assertEqual(host.remote_url, "http://clab-form-01:8080")

    def test_form_shows_error_for_incomplete_secrets_group(self):
        incomplete_group = create_http_secrets_group("Form no password", password=False)
        form = ContainerlabHostForm(data=self._form_data(incomplete_group))

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["secrets_group"], [MISSING_PASSWORD_MESSAGE])


class ContainerlabHostFilterFormTestCase(TestCase):
    """Test the ContainerlabHost list filter form."""

    def test_verify_ssl_filter_offers_empty_blank_yes_and_no(self):
        rendered = str(ContainerlabHostFilterForm()["verify_ssl"])

        options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', rendered)
        self.assertEqual(options, [("", "---------"), ("True", "Yes"), ("False", "No")])

    def test_name_filter_shows_name_placeholder(self):
        rendered = str(ContainerlabHostFilterForm()["name"])

        self.assertIn('placeholder="Name"', rendered)

    def test_declared_filter_fields_show_no_none_placeholder(self):
        form = ContainerlabHostFilterForm()
        for field_name in ContainerlabHostFilterForm.declared_fields:
            with self.subTest(field=field_name):
                self.assertNotIn('placeholder="None"', str(form[field_name]))


class NodeMappingFormTestCase(FormTestCases.BaseFormTestCase):
    """Test the NodeMapping create and edit form."""

    form_class = NodeMappingForm

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()
        cls.free_device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.create(name="Juniper"), model="vMX"
        )
        cls.free_platform = Platform.objects.create(name="frr")
        cls.taken_device_type = DeviceType.objects.get(model="DCS-7280")

    def _form_data(self, **changes):
        data = {
            "device_type": self.free_device_type.pk,
            "kind": "juniper_vmx",
            "image": "vrnetlab/vr-vmx:23.2",
            "env": '{"A": "1"}',
            "overrides": "{}",
        }
        data.update(changes)
        return data

    def test_valid_form_saves_mapping(self):
        form = NodeMappingForm(data=self._form_data())

        self.assertTrue(form.is_valid(), form.errors)
        mapping = form.save()
        self.assertEqual(mapping.device_type, self.free_device_type)
        self.assertEqual(mapping.env, {"A": "1"})

    def test_empty_env_and_overrides_save_empty_objects(self):
        form = NodeMappingForm(data=self._form_data(env="", overrides=""))

        self.assertTrue(form.is_valid(), form.errors)
        mapping = form.save()
        self.assertEqual(mapping.env, {})
        self.assertEqual(mapping.overrides, {})

    def test_env_and_overrides_keep_model_help_text(self):
        form = NodeMappingForm()

        self.assertIn("not secret", form.fields["env"].help_text)
        self.assertIn("WARNING", form.fields["overrides"].help_text)

    def test_config_template_help_text_warns(self):
        self.assertEqual(
            NodeMappingForm().fields["config_template"].help_text,
            "A Jinja2 template for the startup config. WARNING: The template can read Nautobot data. "
            "Each user who can view the lab can see the result.",
        )

    def test_each_rule_shows_form_error(self):
        cases = (
            ({"platform": self.free_platform.pk}, "__all__", BOTH_TARGETS_MESSAGE),
            ({"device_type": ""}, "__all__", NO_TARGET_MESSAGE),
            ({"device_type": self.taken_device_type.pk}, "device_type", "Node mapping with this Device type already"),
            ({"env": "[1]"}, "env", "Enter a JSON object"),
            ({"kind": "Ceos"}, "kind", "Use lowercase letters"),
            ({"memory": "512"}, "memory", "Enter a number and a unit"),
            ({"config_source": NodeMappingConfigSourceChoices.JINJA2}, "config_template", "Enter a template when"),
            ({"config_template": "{% if %}"}, "config_template", "Template syntax error on line 1:"),
        )
        for changes, field_name, message_start in cases:
            with self.subTest(changes=changes):
                form = NodeMappingForm(data=self._form_data(**changes))

                self.assertFalse(form.is_valid())
                self.assertTrue(
                    any(message.startswith(message_start) for message in form.errors[field_name]),
                    form.errors,
                )


class NodeMappingFilterFormTestCase(TestCase):
    """Test the NodeMapping list filter form."""

    def test_declared_filter_fields_show_no_none_placeholder(self):
        form = NodeMappingFilterForm()
        for field_name in NodeMappingFilterForm.declared_fields:
            with self.subTest(field=field_name):
                self.assertNotIn('placeholder="None"', str(form[field_name]))


class NodeMappingBulkEditFormTestCase(TestCase):
    """Test the NodeMapping bulk edit form."""

    def test_declared_fields_show_no_none_placeholder(self):
        form = NodeMappingBulkEditForm(NodeMapping)
        for field_name in NodeMappingBulkEditForm.declared_fields:
            with self.subTest(field=field_name):
                self.assertNotIn('placeholder="None"', str(form[field_name]))


class LabFormTestCase(TestCase):
    """Test the Lab create, edit, bulk edit, and filter forms."""

    @classmethod
    def setUpTestData(cls):
        cls.host = create_containerlab_hosts()[0]

    def test_empty_device_filter_saves_empty_object(self):
        form = LabForm(
            data={
                "name": "form-lab",
                "host": self.host.pk,
                "mgmt_subnet": "192.168.50.0/24",
                "config_source": "none",
                "device_filter": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["device_filter"], {})

    def test_lab_form_fields_have_labels(self):
        form = LabForm()
        expected_labels = {
            "name": "Name",
            "host": "Host",
            "mgmt_subnet": "Management subnet",
            "config_source": "Default config source",
            "device_filter": "Device filter",
            "dynamic_group": "Dynamic Group",
            "devices": "Devices",
        }
        for field_name, label in expected_labels.items():
            with self.subTest(field=field_name):
                self.assertEqual(form.fields[field_name].label, label)
        self.assertNotIn("owner", form.fields)
        self.assertNotIn("status", form.fields)

    def test_filter_and_bulk_edit_fields_have_labels(self):
        for form in (LabFilterForm(), LabBulkEditForm(Lab)):
            for field_name in type(form).declared_fields:
                with self.subTest(form=type(form).__name__, field=field_name):
                    if field_name != "pk":
                        self.assertTrue(form.fields[field_name].label)
                    self.assertNotIn('placeholder="None"', str(form[field_name]))


BLANK_CHOICE = (None, "---------")
BASE_CHOICES = [("none", "None (kind default)"), ("jinja2", "Jinja2 template")]
GOLDEN_CONFIG_CHOICES = [
    ("golden_config_intended", "Golden Config intended"),
    ("golden_config_backup", "Golden Config backup"),
]


class GoldenConfigChoiceFormTestCase(TestCase):
    """Test the config source choices of the forms with and without Golden Config."""

    @classmethod
    def setUpTestData(cls):
        cls.host = create_containerlab_hosts()[0]
        cls.mapping = create_node_mappings()[0]
        cls.planned = Status.objects.get(name=LabStatusChoices.PLANNED)

    @staticmethod
    def edit_form_choices():
        """Return the config source choices of each edit form and each bulk edit form."""
        forms = {
            "NodeMappingForm": NodeMappingForm(),
            "LabForm": LabForm(),
            "NodeMappingBulkEditForm": NodeMappingBulkEditForm(NodeMapping),
            "LabBulkEditForm": LabBulkEditForm(Lab),
        }
        return {name: list(form.fields["config_source"].choices) for name, form in forms.items()}

    def assert_filter_forms_keep_all_choices(self):
        """Assert that the filter forms offer each config source."""
        for form in (NodeMappingFilterForm(), LabFilterForm()):
            with self.subTest(form=type(form).__name__):
                self.assertEqual(
                    list(form.fields["config_source"].choices), [BLANK_CHOICE, *BASE_CHOICES, *GOLDEN_CONFIG_CHOICES]
                )

    def test_not_installed_forms_hide_golden_config_choices(self):
        with mock.patch.object(config_sources, "golden_config_installed", return_value=False):
            choices = self.edit_form_choices()
            self.assert_filter_forms_keep_all_choices()

        self.assertEqual(
            choices,
            {
                "NodeMappingForm": [BLANK_CHOICE, *BASE_CHOICES],
                "LabForm": BASE_CHOICES,
                "NodeMappingBulkEditForm": [BLANK_CHOICE, *BASE_CHOICES],
                "LabBulkEditForm": [BLANK_CHOICE, *BASE_CHOICES],
            },
        )

    def test_installed_forms_show_golden_config_choices(self):
        all_choices = [*BASE_CHOICES, *GOLDEN_CONFIG_CHOICES]

        self.assertEqual(
            self.edit_form_choices(),
            {
                "NodeMappingForm": [BLANK_CHOICE, *all_choices],
                "LabForm": all_choices,
                "NodeMappingBulkEditForm": [BLANK_CHOICE, *all_choices],
                "LabBulkEditForm": [BLANK_CHOICE, *all_choices],
            },
        )
        self.assert_filter_forms_keep_all_choices()

    def test_not_installed_form_keeps_saved_golden_config_source(self):
        backup = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP
        intended = NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED
        NodeMapping.objects.filter(pk=self.mapping.pk).update(config_source=backup)
        self.mapping.refresh_from_db()
        lab = Lab.objects.create(
            name="gc-lab", host=self.host, mgmt_subnet="192.168.60.0/24", status=self.planned, config_source=intended
        )
        mapping_data = {"device_type": self.mapping.device_type.pk, "kind": "ceos", "image": "ceos:4.33.0F"}
        lab_data = {"name": "gc-lab-2", "host": self.host.pk, "mgmt_subnet": "192.168.60.0/24"}
        cases = (
            (NodeMappingForm, self.mapping, mapping_data, (backup, "Golden Config backup (Golden Config not installed)")),
            (LabForm, lab, lab_data, (intended, "Golden Config intended (Golden Config not installed)")),
        )
        with mock.patch.object(config_sources, "golden_config_installed", return_value=False):
            for form_class, instance, data, saved_choice in cases:
                with self.subTest(form=form_class.__name__):
                    form = form_class(instance=instance)
                    self.assertEqual(list(form.fields["config_source"].choices)[-1], saved_choice)
                    self.assertEqual(form["config_source"].value(), saved_choice[0])

                    form = form_class(instance=instance, data={**data, "config_source": saved_choice[0]})
                    self.assertTrue(form.is_valid(), form.errors)
                    saved = form.save()
                    saved.refresh_from_db()
                    self.assertEqual(saved.config_source, saved_choice[0])
