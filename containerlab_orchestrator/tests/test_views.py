"""Tests for the containerlab_orchestrator web UI."""

from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.urls import reverse
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.apps.testing import TestCase, TransactionTestCase, ViewTestCases, create_job_result_and_run_job
from nautobot.dcim.models import DeviceType, Manufacturer, Platform

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.models import ContainerlabHost, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_http_secrets_group,
    create_node_mappings,
)

HOST_LIST_URL_NAME = "plugins:containerlab_orchestrator:containerlabhost_list"
NODE_MAPPING_LIST_URL_NAME = "plugins:containerlab_orchestrator:nodemapping_list"


class ContainerlabHostViewTestCase(ViewTestCases.PrimaryObjectViewTestCase):  # pylint: disable=too-many-ancestors
    """Run the generic Nautobot UI view tests on ContainerlabHost."""

    model = ContainerlabHost

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()
        cls.secrets_group = create_http_secrets_group("View credentials")
        cls.form_data = {
            "name": "clab-view-01",
            "remote_url": "http://clab-view-01:8080",
            "verify_ssl": False,
            "secrets_group": cls.secrets_group.pk,
            "description": "View host",
        }
        cls.update_data = {
            "name": "clab-view-02",
            "remote_url": "https://clab-view-02:8080",
            "verify_ssl": True,
            "secrets_group": cls.secrets_group.pk,
            "description": "Edited host",
        }
        cls.bulk_edit_data = {
            "description": "Bulk edited host",
            "verify_ssl": False,
            "secrets_group": cls.secrets_group.pk,
        }

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_list_filtered_by_verify_ssl_shows_only_matching_hosts(self):
        self.add_permissions("containerlab_orchestrator.view_containerlabhost")

        response = self.client.get(self._get_url("list"), {"verify_ssl": "True"}, headers={"HX-Request": "true"})

        self.assertHttpStatus(response, 200)
        for host in ContainerlabHost.objects.all():
            with self.subTest(host=host.name):
                if host.verify_ssl:
                    self.assertContains(response, host.get_absolute_url())
                else:
                    self.assertNotContains(response, host.get_absolute_url())


class ContainerlabHostBulkEditJobTestCase(TransactionTestCase):
    """Run the bulk edit system job on ContainerlabHost.

    The job writes its log through a second database connection, so this test commits its data.
    The job log hides each word "Secret", so the test looks for the rest of the message.
    """

    def setUp(self):
        super().setUp()
        create_containerlab_hosts()

    def test_bulk_edit_to_incomplete_secrets_group_fails(self):
        host = ContainerlabHost.objects.get(name="clab01")
        original_group = host.secrets_group
        incomplete_group = create_http_secrets_group("Bulk edit no password", password=False)

        job_result = create_job_result_and_run_job(
            "nautobot.core.jobs.bulk_actions",
            "BulkEditObjects",
            content_type=ContentType.objects.get_for_model(ContainerlabHost).pk,
            edit_all=False,
            filter_query_params={},
            pk_list=[str(host.pk)],
            form_data={"pk": [str(host.pk)], "secrets_group": str(incomplete_group.pk)},
        )

        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertIn(
            "must have an HTTP(S) password",
            " ".join(job_result.job_log_entries.values_list("message", flat=True)),
        )
        host.refresh_from_db()
        self.assertEqual(host.secrets_group, original_group)


class ContainerlabHostDiscoveryTestCase(TestCase):
    """Test that a user finds the hosts through the menu and the global search."""

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_navigation_menu_has_containerlab_hosts_item(self):
        self.add_permissions("containerlab_orchestrator.view_containerlabhost")

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Containerlab Hosts")
        self.assertContains(response, f'href="{reverse(HOST_LIST_URL_NAME)}"')

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_navigation_menu_hides_containerlab_hosts_item_without_permission(self):
        response = self.client.get(reverse("home"))

        self.assertNotContains(response, f'href="{reverse(HOST_LIST_URL_NAME)}"')

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_global_search_returns_host(self):
        self.add_permissions("containerlab_orchestrator.view_containerlabhost")
        host = ContainerlabHost.objects.get(name="clab02")

        response = self.client.get(
            reverse("search"),
            {"q": "clab02", "model": "containerlab_orchestrator.containerlabhost"},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, host.get_absolute_url())
        self.assertNotContains(response, ContainerlabHost.objects.get(name="clab01").get_absolute_url())


class NodeMappingViewTestCase(  # pylint: disable=too-many-ancestors
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.GetObjectNotesViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkEditObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    """Run the generic Nautobot UI view tests on NodeMapping.

    The list leaves out the bulk rename tests of PrimaryObjectViewTestCase. Their setUp skips every test of a model
    with no name field, and NodeMapping has no name field.
    """

    model = NodeMapping

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()
        device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.create(name="Cisco"), model="CSR1000v"
        )
        cls.form_data = {
            "device_type": device_type.pk,
            "kind": "ceos",
            "image": "ceos:4.32.0F",
            "env": '{"A": "1"}',
            "overrides": "{}",
        }
        cls.update_data = {**cls.form_data, "image": "ceos:4.33.0F"}
        cls.bulk_edit_data = {"image": "ceos:4.34.0F", "config_source": NodeMappingConfigSourceChoices.NONE}

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_detail_page_says_env_is_not_secret(self):
        self.add_permissions("containerlab_orchestrator.view_nodemapping")
        mapping = NodeMapping.objects.get(device_type__model="DCS-7280")

        response = self.client.get(mapping.get_absolute_url())

        self.assertBodyContains(response, "ENVIRONMENT VARIABLES (NOT SECRET)")
        self.assertBodyContains(response, "CLAB_MGMT_VRF")

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_list_shows_config_source_label(self):
        self.add_permissions("containerlab_orchestrator.view_nodemapping")

        response = self.client.get(self._get_url("list"), headers={"HX-Request": "true"})

        self.assertBodyContains(response, "None (kind default)")


class NodeMappingBulkEditJobTestCase(TransactionTestCase):
    """Run the bulk edit system job on NodeMapping.

    The job writes its log through a second database connection, so this test commits its data.
    """

    def setUp(self):
        super().setUp()
        create_node_mappings()

    def test_bulk_edit_clears_config_source_to_lab_default(self):
        mapping = NodeMapping.objects.get(device_type__model="7220 IXR-D3")

        job_result = create_job_result_and_run_job(
            "nautobot.core.jobs.bulk_actions",
            "BulkEditObjects",
            content_type=ContentType.objects.get_for_model(NodeMapping).pk,
            edit_all=False,
            filter_query_params={},
            pk_list=[str(mapping.pk)],
            form_data={"pk": [str(mapping.pk)], "_nullify": ["config_source"]},
        )

        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_SUCCESS)
        mapping.refresh_from_db()
        self.assertEqual(mapping.config_source, "")


class NodeMappingImportJobTestCase(TransactionTestCase):
    """Run the CSV import system job on NodeMapping.

    The job writes its log through a second database connection, so this test commits its data.
    """

    def test_csv_import_creates_platform_mappings_with_env(self):
        csv_data = "\n".join(
            (
                "platform,kind,image,env",
                'csv-platform-1,linux,alpine:3.20,"{""A"": ""1""}"',
                'csv-platform-2,linux,alpine:3.21,"{""B"": ""2""}"',
                'csv-platform-3,linux,alpine:3.22,"{""C"": ""3""}"',
            )
        )
        for number in range(1, 4):
            Platform.objects.create(name=f"csv-platform-{number}")

        job_result = create_job_result_and_run_job(
            "nautobot.core.jobs",
            "ImportObjects",
            content_type=ContentType.objects.get_for_model(NodeMapping).pk,
            csv_data=csv_data,
        )

        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_SUCCESS)
        self.assertEqual(NodeMapping.objects.get(platform__name="csv-platform-2").env, {"B": "2"})
        self.assertEqual(NodeMapping.objects.filter(platform__name__startswith="csv-platform-").count(), 3)


class NodeMappingDiscoveryTestCase(TestCase):
    """Test that a user finds the node mappings through the menu."""

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_navigation_menu_has_node_mappings_item(self):
        self.add_permissions("containerlab_orchestrator.view_nodemapping")

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Node Mappings")
        self.assertContains(response, f'href="{reverse(NODE_MAPPING_LIST_URL_NAME)}"')

    @override_settings(EXEMPT_VIEW_PERMISSIONS=[])
    def test_navigation_menu_hides_node_mappings_item_without_permission(self):
        response = self.client.get(reverse("home"))

        self.assertNotContains(response, f'href="{reverse(NODE_MAPPING_LIST_URL_NAME)}"')
