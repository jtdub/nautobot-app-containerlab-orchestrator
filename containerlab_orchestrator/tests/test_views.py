"""Tests for the ContainerlabHost web UI."""

from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.urls import reverse
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.apps.testing import TestCase, TransactionTestCase, ViewTestCases, create_job_result_and_run_job

from containerlab_orchestrator.models import ContainerlabHost
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts, create_http_secrets_group

HOST_LIST_URL_NAME = "plugins:containerlab_orchestrator:containerlabhost_list"


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
