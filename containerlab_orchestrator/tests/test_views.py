"""Tests for the containerlab_orchestrator web UI."""

import json
import re
import unittest

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.apps.testing import TestCase, TransactionTestCase, ViewTestCases, create_job_result_and_run_job
from nautobot.dcim.models import DeviceType, Manufacturer, Platform
from nautobot.extras.models import ObjectChange

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping
from containerlab_orchestrator.tables import InterfaceMappingTable
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_http_secrets_group,
    create_interface_mappings,
    create_node_mappings,
)

HOST_LIST_URL_NAME = "plugins:containerlab_orchestrator:containerlabhost_list"
NODE_MAPPING_LIST_URL_NAME = "plugins:containerlab_orchestrator:nodemapping_list"
INTERFACE_ROWS_PREFIX = "interface_mappings"
ARISTA_PHYSICAL_NAMES = ("GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/10")
NODE_MAPPING_EDIT_PERMISSIONS = (
    "containerlab_orchestrator.add_nodemapping",
    "containerlab_orchestrator.change_nodemapping",
    "containerlab_orchestrator.view_nodemapping",
    "dcim.view_devicetype",
)


def node_mapping_post_data(mapping, **changes):
    """Return the edit form POST data of a node mapping, with the given field changes."""
    data = {
        "device_type": mapping.device_type_id or "",
        "platform": mapping.platform_id or "",
        "kind": mapping.kind,
        "image": mapping.image,
        "node_type": mapping.node_type,
        "cpu": mapping.cpu or "",
        "memory": mapping.memory,
        "env": json.dumps(mapping.env),
        "overrides": json.dumps(mapping.overrides),
        "config_source": mapping.config_source,
        "config_template": mapping.config_template,
    }
    data.update(changes)
    return data


def formset_post_data(rows, initial_count):
    """Return the POST data of the interface rows formset for the given row dictionaries."""
    data = {
        f"{INTERFACE_ROWS_PREFIX}-TOTAL_FORMS": len(rows),
        f"{INTERFACE_ROWS_PREFIX}-INITIAL_FORMS": initial_count,
        f"{INTERFACE_ROWS_PREFIX}-MIN_NUM_FORMS": 0,
        f"{INTERFACE_ROWS_PREFIX}-MAX_NUM_FORMS": 1000,
    }
    for index, row in enumerate(rows):
        for key in ("id", "node_mapping", "physical_name", "container_name", "DELETE"):
            data[f"{INTERFACE_ROWS_PREFIX}-{index}-{key}"] = row.get(key, "")
    return data


def existing_row(row, **changes):
    """Return the POST row dictionary of a saved interface row, with the given changes."""
    return {
        "id": row.pk,
        "node_mapping": row.node_mapping_id,
        "physical_name": row.physical_name,
        "container_name": row.container_name,
        **changes,
    }


def interface_pairs(mapping):
    """Return the physical and container name pairs of a node mapping, in the model order."""
    return list(mapping.interface_mappings.values_list("physical_name", "container_name"))


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
            "config_source": NodeMappingConfigSourceChoices.NONE,
            "config_template": "",
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


class NodeMappingInterfaceRowsViewTestCase(TestCase):
    """Test the interface rows on the node mapping create and edit form."""

    @classmethod
    def setUpTestData(cls):
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        cls.arista, cls.nokia, _ = node_mappings
        cls.free_device_type = DeviceType.objects.create(
            manufacturer=Manufacturer.objects.create(name="Cisco"), model="CSR1000v"
        )

    def setUp(self):
        super().setUp()
        self.add_permissions(*NODE_MAPPING_EDIT_PERMISSIONS)
        self.add_url = reverse("plugins:containerlab_orchestrator:nodemapping_add")
        self.edit_url = reverse("plugins:containerlab_orchestrator:nodemapping_edit", kwargs={"pk": self.arista.pk})

    def arista_rows(self):
        """Return the POST row dictionaries of the three saved Arista rows."""
        return [existing_row(self.arista.interface_mappings.get(physical_name=name)) for name in ARISTA_PHYSICAL_NAMES]

    def edit_post_data(self):
        """Return an Arista edit POST that keeps row 1, changes row 2, deletes row 3, and adds GigabitEthernet0/3."""
        first, second, tenth = self.arista_rows()
        return {
            **node_mapping_post_data(self.arista),
            **formset_post_data(
                [
                    first,
                    {**second, "container_name": "eth3"},
                    {**tenth, "DELETE": "on"},
                    {"physical_name": "GigabitEthernet0/3", "container_name": "eth4"},
                ],
                initial_count=3,
            ),
        }

    def test_create_with_two_rows_saves_mapping_and_rows(self):
        data = {
            **node_mapping_post_data(self.arista, device_type=self.free_device_type.pk),
            **formset_post_data(
                [
                    {"physical_name": "GigabitEthernet1", "container_name": "eth1"},
                    {"physical_name": "GigabitEthernet2", "container_name": "eth2"},
                ],
                initial_count=0,
            ),
        }

        response = self.client.post(self.add_url, data)

        self.assertHttpStatus(response, 302)
        mapping = NodeMapping.objects.get(device_type=self.free_device_type)
        self.assertEqual(interface_pairs(mapping), [("GigabitEthernet1", "eth1"), ("GigabitEthernet2", "eth2")])

    def test_edit_changes_deletes_and_adds_rows(self):
        response = self.client.post(self.edit_url, self.edit_post_data())

        self.assertHttpStatus(response, 302)
        self.assertEqual(
            interface_pairs(self.arista),
            [("GigabitEthernet0/1", "eth1"), ("GigabitEthernet0/2", "eth3"), ("GigabitEthernet0/3", "eth4")],
        )

    def test_edit_with_duplicate_new_rows_shows_formset_error_and_saves_nothing(self):
        duplicates = {
            "physical_name": [("GigabitEthernet0/20", "eth20"), ("GigabitEthernet0/20", "eth21")],
            "container_name": [("GigabitEthernet0/20", "eth20"), ("GigabitEthernet0/21", "eth20")],
        }
        before = interface_pairs(self.arista)
        for field_name, pairs in duplicates.items():
            with self.subTest(field_name=field_name):
                new_rows = [{"physical_name": physical, "container_name": container} for physical, container in pairs]
                data = {
                    **node_mapping_post_data(self.arista, image="ceos:4.99.0F"),
                    **formset_post_data(self.arista_rows() + new_rows, initial_count=3),
                }

                response = self.client.post(self.edit_url, data)

                self.assertHttpStatus(response, 200)
                self.assertBodyContains(response, "Please correct the duplicate data for")
                self.assertEqual(interface_pairs(self.arista), before)
                self.arista.refresh_from_db()
                self.assertEqual(self.arista.image, "ceos:4.32.0F")

    def test_create_with_bad_container_name_shows_row_error_and_saves_nothing(self):
        data = {
            **node_mapping_post_data(self.arista, device_type=self.free_device_type.pk),
            **formset_post_data([{"physical_name": "GigabitEthernet1", "container_name": "eth 1"}], initial_count=0),
        }

        response = self.client.post(self.add_url, data)

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "Fix the errors in the interface rows.")
        self.assertBodyContains(
            response, "The container interface name must contain only printable characters and no spaces."
        )
        self.assertBodyContains(response, 'value="eth 1"')
        self.assertFalse(NodeMapping.objects.filter(device_type=self.free_device_type).exists())

    def test_edit_with_bad_kind_keeps_typed_rows(self):
        data = {
            **node_mapping_post_data(self.arista, kind="Ceos"),
            **formset_post_data(
                self.arista_rows() + [{"physical_name": "GigabitEthernet0/3", "container_name": "eth4"}],
                initial_count=3,
            ),
        }

        response = self.client.post(self.edit_url, data)

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "start with a letter.")
        self.assertBodyContains(response, 'value="GigabitEthernet0/3"')
        self.assertFalse(self.arista.interface_mappings.filter(physical_name="GigabitEthernet0/3").exists())

    def test_edit_with_bad_kind_and_no_formset_data_has_no_management_form_error(self):
        response = self.client.post(self.edit_url, node_mapping_post_data(self.arista, kind="Ceos"))

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "start with a letter.")
        self.assertNotContains(response, "ManagementForm data is missing")

    @unittest.skipUnless(
        connection.vendor == "mysql", "Only the MySQL default collation ignores case in a unique index."
    )
    def test_new_rows_that_differ_only_in_case_show_error_on_mysql(self):
        data = {
            **node_mapping_post_data(self.nokia),
            **formset_post_data(
                [
                    {"physical_name": "ethernet-1/1", "container_name": "eth1"},
                    {"physical_name": "ethernet-1/2", "container_name": "ETH1"},
                ],
                initial_count=0,
            ),
        }

        response = self.client.post(
            reverse("plugins:containerlab_orchestrator:nodemapping_edit", kwargs={"pk": self.nokia.pk}), data
        )

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "Names that differ only in case or accent count as the same name.")
        self.assertFalse(self.nokia.interface_mappings.exists())

    def test_add_page_shows_one_empty_interface_row(self):
        response = self.client.get(self.add_url)

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "Interface Mappings")
        self.assertBodyContains(response, f'name="{INTERFACE_ROWS_PREFIX}-TOTAL_FORMS" value="1"')
        self.assertBodyContains(response, f'name="{INTERFACE_ROWS_PREFIX}-0-physical_name"')

    def test_edit_page_shows_interface_rows(self):
        response = self.client.get(self.edit_url)

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "<strong>Node mapping</strong>")
        self.assertBodyContains(response, "Interface Mappings")
        self.assertBodyContains(response, 'value="GigabitEthernet0/10"')
        self.assertBodyContains(response, f'name="{INTERFACE_ROWS_PREFIX}-TOTAL_FORMS"')

    def test_change_log_tab_lists_row_changes_from_the_form(self):
        self.client.post(self.edit_url, self.edit_post_data())
        self.add_permissions("extras.view_objectchange")

        response = self.client.get(
            reverse("plugins:containerlab_orchestrator:nodemapping_changelog", kwargs={"pk": self.arista.pk})
        )

        self.assertHttpStatus(response, 200)
        for row_text in ("GigabitEthernet0/2 → eth3", "GigabitEthernet0/3 → eth4", "GigabitEthernet0/10 → eth10"):
            self.assertBodyContains(response, row_text)
        row_change = ObjectChange.objects.get(
            changed_object_type=ContentType.objects.get_for_model(InterfaceMapping),
            object_repr="GigabitEthernet0/3 → eth4",
        )
        self.assertHttpStatus(self.client.get(row_change.get_absolute_url()), 200)


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class NodeMappingInterfacePanelTestCase(TestCase):
    """Test the Interface Mappings panel on the node mapping detail page."""

    @classmethod
    def setUpTestData(cls):
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        cls.arista, cls.nokia, _ = node_mappings

    def get_detail_page(self, mapping=None):
        """Return the response of the detail page of a node mapping, by default the Arista mapping."""
        return self.client.get((mapping or self.arista).get_absolute_url())

    def assertEditInterfacesLink(self, response, mapping):  # pylint: disable=invalid-name
        """Assert that one link element holds the edit URL of the mapping and the label Edit interfaces."""
        edit_url = reverse("plugins:containerlab_orchestrator:nodemapping_edit", kwargs={"pk": mapping.pk})
        link = re.compile(rf'<a href="{re.escape(edit_url)}"[^>]*>\s*<span[^>]*></span>\s*Edit interfaces\s*</a>')
        self.assertRegex(response.content.decode(), link)

    def test_panel_shows_rows_and_edit_button_with_change_permission(self):
        self.add_permissions(
            "containerlab_orchestrator.view_nodemapping",
            "containerlab_orchestrator.view_interfacemapping",
            "containerlab_orchestrator.change_nodemapping",
        )

        response = self.get_detail_page()

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "Interface Mappings")
        self.assertBodyContains(response, "GigabitEthernet0/10")
        self.assertBodyContains(response, "eth10")
        self.assertEditInterfacesLink(response, self.arista)

    def test_panel_of_mapping_with_no_rows_shows_empty_table_and_edit_button(self):
        self.add_permissions(
            "containerlab_orchestrator.view_nodemapping",
            "containerlab_orchestrator.view_interfacemapping",
            "containerlab_orchestrator.change_nodemapping",
        )

        response = self.get_detail_page(self.nokia)

        self.assertHttpStatus(response, 200)
        self.assertBodyContains(response, "Interface Mappings")
        self.assertNotContains(response, "GigabitEthernet0/10")
        self.assertEditInterfacesLink(response, self.nokia)

    @override_settings(PAGINATE_COUNT=2)
    def test_panel_shows_all_rows_when_rows_exceed_page_size(self):
        self.add_permissions(
            "containerlab_orchestrator.view_nodemapping", "containerlab_orchestrator.view_interfacemapping"
        )

        response = self.get_detail_page()

        for physical_name in ARISTA_PHYSICAL_NAMES:
            with self.subTest(physical_name=physical_name):
                self.assertBodyContains(response, physical_name)

    def test_panel_hides_edit_button_without_change_permission(self):
        self.add_permissions(
            "containerlab_orchestrator.view_nodemapping", "containerlab_orchestrator.view_interfacemapping"
        )

        response = self.get_detail_page()

        self.assertBodyContains(response, "GigabitEthernet0/10")
        self.assertNotContains(response, "Edit interfaces")

    def test_panel_hides_rows_without_interface_view_permission(self):
        self.add_permissions("containerlab_orchestrator.view_nodemapping")

        response = self.get_detail_page()

        self.assertBodyContains(response, "Interface Mappings")
        self.assertNotContains(response, "GigabitEthernet0/10")


class InterfaceMappingTableTestCase(TestCase):
    """Test the sort order of the interface rows table."""

    def test_sort_by_physical_name_uses_natural_interface_order(self):
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        arista = node_mappings[0]
        for order_by, expected in (
            ("physical_name", list(ARISTA_PHYSICAL_NAMES)),
            ("-physical_name", list(reversed(ARISTA_PHYSICAL_NAMES))),
        ):
            with self.subTest(order_by=order_by):
                table = InterfaceMappingTable(arista.interface_mappings.all(), order_by=order_by)

                self.assertEqual([row.record.physical_name for row in table.rows], expected)
