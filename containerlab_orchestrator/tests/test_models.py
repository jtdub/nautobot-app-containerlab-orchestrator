"""Tests for the ContainerlabHost model."""

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.apps.testing import ModelTestCases, TestCase
from nautobot.extras.models import Secret, SecretsGroupAssociation

from containerlab_orchestrator.models import ContainerlabHost
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts, create_http_secrets_group

MISSING_USERNAME_MESSAGE = "The Secrets Group must have an HTTP(S) username secret."
MISSING_PASSWORD_MESSAGE = "The Secrets Group must have an HTTP(S) password secret."


class ContainerlabHostModelTestCase(ModelTestCases.BaseModelTestCase):
    """Run the generic Nautobot model tests on ContainerlabHost."""

    model = ContainerlabHost

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()

    def test_get_docs_url(self):
        """Skip the documentation check, because issue #25 adds the host documentation page."""
        self.skipTest("Issue #25 adds the ContainerlabHost documentation page.")

    def test_str_returns_name(self):
        host = ContainerlabHost.objects.get(name="clab01")
        self.assertEqual(str(host), "clab01")

    def test_verify_ssl_defaults_to_true(self):
        host = ContainerlabHost(name="clab-default", url="https://clab-default:8080")
        self.assertTrue(host.verify_ssl)


class ContainerlabHostCleanTestCase(TestCase):
    """Test the validation of a host and its Secrets Group."""

    def build_host(self, secrets_group, name="clab01", url="https://clab01:8080"):
        """Return an unsaved host with the given Secrets Group."""
        return ContainerlabHost(name=name, url=url, secrets_group=secrets_group)

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

    def test_duplicate_name_fails_validation(self):
        secrets_group = create_http_secrets_group("Shared")
        self.build_host(secrets_group).validated_save()
        duplicate = self.build_host(secrets_group, url="https://clab02:8080")
        with self.assertRaises(ValidationError) as context:
            duplicate.full_clean()
        self.assertIn("name", context.exception.message_dict)

    def test_duplicate_url_is_valid(self):
        secrets_group = create_http_secrets_group("Shared")
        self.build_host(secrets_group).validated_save()
        self.build_host(secrets_group, name="clab02").full_clean()

    def test_url_accepts_short_host_name(self):
        host = self.build_host(create_http_secrets_group("Short"), url="http://clab01:8080")
        host.full_clean()


class ContainerlabHostDeleteTestCase(TestCase):
    """Test the protection of a Secrets Group that a host uses."""

    def test_delete_secrets_group_in_use_raises_protected_error(self):
        secrets_group = create_http_secrets_group("In use")
        ContainerlabHost.objects.create(name="clab01", url="https://clab01:8080", secrets_group=secrets_group)
        with self.assertRaises(ProtectedError):
            secrets_group.delete()
