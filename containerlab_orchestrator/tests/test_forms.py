"""Tests for the containerlab_orchestrator forms."""

import re

from nautobot.apps.testing import FormTestCases, TestCase
from nautobot.dcim.models import DeviceType, Manufacturer, Platform

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.forms import (
    ContainerlabHostFilterForm,
    ContainerlabHostForm,
    NodeMappingBulkEditForm,
    NodeMappingFilterForm,
    NodeMappingForm,
)
from containerlab_orchestrator.models import NodeMapping
from containerlab_orchestrator.tests.fixtures import create_http_secrets_group, create_node_mappings
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
