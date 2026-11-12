"""Tests for the ContainerlabHost forms."""

from nautobot.apps.testing import FormTestCases

from containerlab_orchestrator.forms import ContainerlabHostForm
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
