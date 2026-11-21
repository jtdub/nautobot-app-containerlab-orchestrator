"""Tests for the topology YAML download endpoint and the Topology YAML tab of the lab page."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from nautobot.apps.testing import APITestCase, TestCase
from rest_framework import status

from containerlab_orchestrator.api.views import NO_TOPOLOGY_MESSAGE
from containerlab_orchestrator.models import Lab
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_lab_devices,
    create_labs,
    store_yaml,
)
from containerlab_orchestrator.views import LARGE_YAML_TEXT, NO_BUILD_TEXT, TAB_YAML_LIMIT

TOPOLOGY_URL_NAME = "plugins-api:containerlab_orchestrator-api:lab-topology"
LAB_LIST_URL_NAME = "plugins:containerlab_orchestrator:lab_list"
STORED_YAML = "name: lab-a\ntopology:\n  nodes:\n    leaf1:\n      kind: ceos\n      image: ceos:4.32.0F\n"
SCRIPT_YAML = (
    "name: lab-a\ntopology:\n  nodes:\n    leaf1:\n      startup-config: |\n        <script>alert(1)</script>\n"
)


def topology_url(lab):
    """Return the topology download URL of the lab."""
    return reverse(TOPOLOGY_URL_NAME, kwargs={"pk": lab.pk})


class LabTopologyAPITestCase(APITestCase):  # pylint: disable=too-many-ancestors
    """Test the topology YAML download endpoint and the lab serializer."""

    model = Lab

    @classmethod
    def setUpTestData(cls):
        cls.labs = create_labs(create_containerlab_hosts(), create_lab_devices())
        cls.lab_a, cls.lab_b = cls.labs[0], cls.labs[1]
        store_yaml(cls.lab_a, STORED_YAML)

    def test_download_returns_yaml_file(self):
        self.add_permissions("containerlab_orchestrator.view_lab")

        response = self.client.get(topology_url(self.lab_a), **self.header)

        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/yaml")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="lab-a.clab.yml"')
        self.assertEqual(response.content.decode(), STORED_YAML)

    def test_lab_with_no_yaml_gives_404_with_message(self):
        self.add_permissions("containerlab_orchestrator.view_lab")

        response = self.client.get(topology_url(self.lab_b), **self.header)

        self.assertHttpStatus(response, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["detail"], NO_TOPOLOGY_MESSAGE.format(lab=self.lab_b))

    def test_user_without_view_lab_gets_403(self):
        response = self.client.get(topology_url(self.lab_a), **self.header)

        self.assertHttpStatus(response, status.HTTP_403_FORBIDDEN)

    def test_lab_outside_the_view_constraint_gives_404(self):
        self.add_permissions("containerlab_orchestrator.view_lab", constraints={"name": "lab-b"})

        response = self.client.get(topology_url(self.lab_a), **self.header)

        self.assertHttpStatus(response, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(STORED_YAML, response.content.decode())

    def test_csv_request_gets_no_topology_file(self):
        self.add_permissions("containerlab_orchestrator.view_lab")
        cases = {
            "accept": (topology_url(self.lab_a), {"HTTP_ACCEPT": "text/csv"}, status.HTTP_406_NOT_ACCEPTABLE),
            "format": (f"{topology_url(self.lab_a)}?format=csv", {}, status.HTTP_404_NOT_FOUND),
        }

        for case, (url, headers, expected_status) in cases.items():
            with self.subTest(case=case):
                response = self.client.get(url, **{**self.header, **headers})

                self.assertHttpStatus(response, expected_status)
                self.assertNotIn("lab_data.csv", response.get("Content-Disposition", ""))

    def test_lab_api_has_no_topology_yaml(self):
        self.add_permissions("containerlab_orchestrator.view_lab")
        list_url = self._get_list_url()

        detail = self.client.get(self._get_detail_url(self.lab_a), **self.header).json()
        listed = self.client.get(list_url, **self.header).json()["results"]
        csv_text = self.client.get(list_url, **{**self.header, "HTTP_ACCEPT": "text/csv"}).content.decode()

        self.assertIn("mgmt_subnet", detail)
        self.assertNotIn("topology_yaml", detail)
        for lab in listed:
            with self.subTest(lab=lab["display"]):
                self.assertNotIn("topology_yaml", lab)
        self.assertIn("mgmt_subnet", csv_text)
        self.assertNotIn("topology_yaml", csv_text)


class LabTopologyTabTestCase(TestCase):
    """Test the Topology YAML tab, the Download YAML button, and the lab list query."""

    @classmethod
    def setUpTestData(cls):
        cls.labs = create_labs(create_containerlab_hosts(), create_lab_devices())
        cls.lab_a = cls.labs[0]

    def setUp(self):
        super().setUp()
        self.add_permissions("containerlab_orchestrator.view_lab")

    def get_lab_a_body(self):
        """Return the HTML of the lab-a (clab01) page."""
        response = self.client.get(self.lab_a.get_absolute_url())
        self.assertHttpStatus(response, 200)
        return response.content.decode()

    def test_tab_shows_yaml_and_download_button(self):
        store_yaml(self.lab_a, STORED_YAML)

        body = self.get_lab_a_body()

        self.assertIn('aria-controls="topology_yaml"', body)
        self.assertIn("Topology YAML", body)
        self.assertIn('<div id="topology_yaml"', body)
        self.assertIn("image: ceos:4.32.0F", body)
        self.assertIn(f'href="{topology_url(self.lab_a)}"', body)
        self.assertIn("Download YAML", body)

    def test_tab_escapes_config_text(self):
        store_yaml(self.lab_a, SCRIPT_YAML)

        body = self.get_lab_a_body()

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("<script>alert(1)</script>", body)

    def test_tab_with_no_build_shows_message_and_no_button(self):
        body = self.get_lab_a_body()

        self.assertIn('<div id="topology_yaml"', body)
        self.assertIn(NO_BUILD_TEXT, body)
        self.assertNotIn("Download YAML", body)
        self.assertNotIn(topology_url(self.lab_a), body)

    def test_tab_shows_size_text_for_large_yaml(self):
        large_yaml = "x" * (TAB_YAML_LIMIT + 1)
        store_yaml(self.lab_a, large_yaml)

        body = self.get_lab_a_body()

        self.assertIn(LARGE_YAML_TEXT.format(size=f"{TAB_YAML_LIMIT + 1:,} characters"), body)
        self.assertNotIn("x" * 1000, body)
        self.assertIn("Download YAML", body)

    def test_tab_shows_yaml_at_the_size_limit(self):
        store_yaml(self.lab_a, "y" * TAB_YAML_LIMIT)

        body = self.get_lab_a_body()

        self.assertIn("y" * TAB_YAML_LIMIT, body)

    def test_lab_list_does_not_load_topology_yaml(self):
        store_yaml(self.lab_a, STORED_YAML)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse(LAB_LIST_URL_NAME), headers={"HX-Request": "true"})

        self.assertHttpStatus(response, 200)
        self.assertIn(self.lab_a.get_absolute_url(), response.content.decode())
        lab_queries = [query["sql"] for query in queries if "containerlab_orchestrator_lab" in query["sql"]]
        self.assertTrue(lab_queries)
        for sql in lab_queries:
            self.assertNotIn("topology_yaml", sql)
