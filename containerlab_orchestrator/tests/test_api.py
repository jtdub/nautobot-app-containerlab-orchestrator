"""Tests for the ContainerlabHost REST API."""

from nautobot.apps.testing import APIViewTestCases
from rest_framework import status

from containerlab_orchestrator.models import ContainerlabHost
from containerlab_orchestrator.tests.fixtures import (
    SECRET_VALUE,
    create_containerlab_hosts,
    create_http_secrets_group,
    patch_secret_variables,
)


class ContainerlabHostAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot REST API tests on ContainerlabHost."""

    model = ContainerlabHost
    choices_fields = ()

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()
        cls.secrets_group = create_http_secrets_group("API credentials")
        cls.create_data = [
            {"name": "clab-api-01", "remote_url": "https://clab-api-01:8080", "secrets_group": cls.secrets_group.pk},
            {
                "name": "clab-api-02",
                "remote_url": "http://clab-api-02:8080",
                "verify_ssl": False,
                "secrets_group": cls.secrets_group.pk,
                "description": "Second API host",
            },
            {"name": "clab-api-03", "remote_url": "https://clab-api-03:8080", "secrets_group": cls.secrets_group.pk},
        ]
        cls.update_data = {"remote_url": "https://clab-updated:8080", "description": "Updated host"}
        cls.bulk_update_data = {"verify_ssl": False}

    def test_create_with_incomplete_secrets_group_returns_400(self):
        self.add_permissions("containerlab_orchestrator.add_containerlabhost", "extras.view_secretsgroup")
        incomplete_group = create_http_secrets_group("No password", password=False)
        data = {"name": "clab-bad", "remote_url": "https://clab-bad:8080", "secrets_group": incomplete_group.pk}

        response = self.client.post(self._get_list_url(), data, format="json", **self.header)

        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["secrets_group"],
            ["The Secrets Group must have an HTTP(S) password secret."],
        )
        self.assertFalse(ContainerlabHost.objects.filter(name="clab-bad").exists())

    def test_csv_import_with_incomplete_secrets_group_returns_400(self):
        self.add_permissions("containerlab_orchestrator.add_containerlabhost", "extras.view_secretsgroup")
        incomplete_group = create_http_secrets_group("No password CSV", password=False)
        csv_data = f"name,remote_url,secrets_group\nclab-csv,https://clab-csv:8080,{incomplete_group.pk}\n"

        response = self.client.post(self._get_list_url(), csv_data, content_type="text/csv", **self.header)

        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn("The Secrets Group must have an HTTP(S) password secret.", str(response.data))
        self.assertFalse(ContainerlabHost.objects.filter(name="clab-csv").exists())

    def test_response_contains_no_secret_value(self):
        self.add_permissions("containerlab_orchestrator.view_containerlabhost", "extras.view_secretsgroup")
        host = ContainerlabHost.objects.get(name="clab01")
        with patch_secret_variables():
            response = self.client.get(f"{self._get_detail_url(host)}?depth=2", **self.header)

        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["secrets_group"]["id"], str(host.secrets_group.pk))
        self.assertNotIn(SECRET_VALUE, response.content.decode(response.charset))
        self.assert_no_verboten_content(response)
