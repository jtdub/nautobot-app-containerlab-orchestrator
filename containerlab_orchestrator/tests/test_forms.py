"""Tests for the ContainerlabHost forms."""

import re

from nautobot.apps.testing import FormTestCases, TestCase

from containerlab_orchestrator.forms import ContainerlabHostFilterForm, ContainerlabHostForm
from containerlab_orchestrator.tests.fixtures import create_http_secrets_group
from containerlab_orchestrator.tests.test_models import MISSING_PASSWORD_MESSAGE


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
