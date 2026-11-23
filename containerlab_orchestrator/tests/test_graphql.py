"""Tests for the GraphQL types of the app."""

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from nautobot.apps.graphql import execute_query
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Device
from nautobot.users.models import ObjectPermission

from containerlab_orchestrator.tests.fixtures import (
    SECRET_VALUE,
    create_containerlab_hosts,
    create_interface_mappings,
    create_lab_devices,
    create_lab_nodes,
    create_labs,
    create_node_mappings,
    create_related_object_data,
    grant_permission,
    patch_secret_variables,
)

HOSTS_QUERY = """
query {
  containerlab_hosts {
    name
    remote_url
    verify_ssl
    description
    secrets_group {
      name
      secrets {
        name
        provider
        parameters
      }
    }
  }
}
"""

NODE_MAPPINGS_QUERY = """
query {
  node_mappings {
    kind
    image
    env
    device_type {
      model
    }
    platform {
      name
    }
  }
}
"""

INTERFACE_MAPPINGS_QUERY = """
query {
  interface_mappings {
    physical_name
    container_name
    node_mapping {
      kind
    }
  }
}
"""

NODE_MAPPING_INTERFACES_QUERY = """
query {
  node_mappings {
    kind
    interface_mappings {
      physical_name
      container_name
    }
  }
}
"""

LABS_QUERY = """
query {
  labs {
    name
    mgmt_subnet
    host { name }
    status { name }
    dynamic_group { name }
    devices { name }
  }
}
"""

LAB_NODES_QUERY = """
query {
  lab_nodes {
    node_name
    is_stub
    mgmt_ipv4
    device { name }
    lab { name }
  }
}
"""

HIDDEN_RELATED_OBJECT_ROWS = (
    (
        "containerlab_orchestrator.view_lab",
        'query { labs(name: "lab-b") { host { name } status { name } dynamic_group { name } devices { name } } }',
        {"labs": [{"host": None, "status": None, "dynamic_group": None, "devices": []}]},
    ),
    (
        "containerlab_orchestrator.view_labnode",
        'query { lab_nodes(node_name: "spine1") { lab { name } device { name } } }',
        {"lab_nodes": [{"lab": None, "device": None}]},
    ),
    (
        "containerlab_orchestrator.view_containerlabhost",
        'query { containerlab_hosts(name: "clab01") { secrets_group { name } } }',
        {"containerlab_hosts": [{"secrets_group": None}]},
    ),
    (
        "containerlab_orchestrator.view_nodemapping",
        'query { node_mappings(kind: "ceos") { device_type { model } } }',
        {"node_mappings": [{"device_type": None}]},
    ),
    (
        "containerlab_orchestrator.view_nodemapping",
        'query { node_mappings(kind: "linux") { platform { name } } }',
        {"node_mappings": [{"platform": None}]},
    ),
    (
        "containerlab_orchestrator.view_interfacemapping",
        'query { interface_mappings(physical_name: "ens3") { node_mapping { kind } } }',
        {"interface_mappings": [{"node_mapping": None}]},
    ),
)
"""Each row: the only permission of the user, the query, and the data that the query returns."""


class ContainerlabHostGraphQLTestCase(TestCase):
    """Test the containerlab_hosts GraphQL query."""

    @classmethod
    def setUpTestData(cls):
        create_containerlab_hosts()

    def setUp(self):
        super().setUp()
        self.add_permissions(
            "containerlab_orchestrator.view_containerlabhost",
            "extras.view_secretsgroup",
            "extras.view_secret",
        )

    def test_query_returns_hosts(self):
        result = execute_query(HOSTS_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        hosts = result.data["containerlab_hosts"]
        self.assertEqual([host["name"] for host in hosts], ["clab01", "clab02", "clab03"])
        self.assertEqual(hosts[0]["remote_url"], "https://clab01.example.com:8080")
        self.assertTrue(hosts[0]["verify_ssl"])
        self.assertEqual(hosts[0]["secrets_group"]["name"], "Containerlab credentials 1")

    def test_query_returns_no_secret_value(self):
        with patch_secret_variables():
            result = execute_query(HOSTS_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["containerlab_hosts"][0]["secrets_group"]["secrets"]), 2)
        self.assertNotIn(SECRET_VALUE, str(result.data))


class NodeMappingGraphQLTestCase(TestCase):
    """Test the node_mappings GraphQL query."""

    @classmethod
    def setUpTestData(cls):
        create_node_mappings()

    def setUp(self):
        super().setUp()
        self.add_permissions(
            "containerlab_orchestrator.view_nodemapping",
            "dcim.view_devicetype",
            "dcim.view_platform",
        )

    def test_query_returns_mappings(self):
        result = execute_query(NODE_MAPPINGS_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        mappings = {mapping["kind"]: mapping for mapping in result.data["node_mappings"]}
        self.assertEqual(set(mappings), {"ceos", "nokia_srlinux", "linux"})
        self.assertEqual(mappings["ceos"]["image"], "ceos:4.32.0F")
        self.assertEqual(mappings["ceos"]["env"], {"CLAB_MGMT_VRF": "MGMT"})
        self.assertEqual(mappings["ceos"]["device_type"], {"model": "DCS-7280"})
        self.assertIsNone(mappings["ceos"]["platform"])
        self.assertEqual(mappings["linux"]["platform"], {"name": "linux"})
        self.assertIsNone(mappings["linux"]["device_type"])


class InterfaceMappingGraphQLTestCase(TestCase):
    """Test the interface_mappings GraphQL query and the interface rows of a node mapping."""

    @classmethod
    def setUpTestData(cls):
        create_interface_mappings(create_node_mappings())

    def setUp(self):
        super().setUp()
        self.add_permissions(
            "containerlab_orchestrator.view_interfacemapping",
            "containerlab_orchestrator.view_nodemapping",
        )

    def test_query_returns_rows(self):
        result = execute_query(INTERFACE_MAPPINGS_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        rows = result.data["interface_mappings"]
        self.assertCountEqual(
            [(row["physical_name"], row["container_name"], row["node_mapping"]["kind"]) for row in rows],
            [
                ("GigabitEthernet0/1", "eth1", "ceos"),
                ("GigabitEthernet0/2", "eth2", "ceos"),
                ("GigabitEthernet0/10", "eth10", "ceos"),
                ("ens3", "eth1", "linux"),
            ],
        )

    def test_node_mapping_query_returns_its_rows(self):
        result = execute_query(NODE_MAPPING_INTERFACES_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        mappings = {mapping["kind"]: mapping["interface_mappings"] for mapping in result.data["node_mappings"]}
        self.assertEqual(
            [row["physical_name"] for row in mappings["ceos"]],
            ["GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/10"],
        )
        self.assertEqual(mappings["linux"], [{"physical_name": "ens3", "container_name": "eth1"}])
        self.assertEqual(mappings["nokia_srlinux"], [])


class LabGraphQLTestCase(TestCase):
    """Test the labs and lab_nodes GraphQL queries."""

    @classmethod
    def setUpTestData(cls):
        lab_devices = create_lab_devices()
        create_lab_nodes(create_labs(create_containerlab_hosts(), lab_devices), lab_devices)

    def setUp(self):
        super().setUp()
        self.add_permissions(
            "containerlab_orchestrator.view_lab",
            "containerlab_orchestrator.view_labnode",
            "containerlab_orchestrator.view_containerlabhost",
            "dcim.view_device",
            "extras.view_status",
            "extras.view_dynamicgroup",
        )

    def test_query_returns_labs(self):
        result = execute_query(LABS_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        labs = {(lab["host"]["name"], lab["name"]): lab for lab in result.data["labs"]}
        self.assertEqual(set(labs), {("clab01", "lab-a"), ("clab01", "lab-b"), ("clab02", "lab-a")})
        lab_a = labs["clab01", "lab-a"]
        self.assertEqual(lab_a["mgmt_subnet"], "172.20.21.0/24")
        self.assertEqual(lab_a["status"], {"name": "Planned"})
        self.assertEqual(lab_a["devices"], [{"name": "spine1"}])
        self.assertEqual(labs["clab01", "lab-b"]["dynamic_group"], {"name": "Leaf devices"})

    def test_query_returns_lab_nodes(self):
        result = execute_query(LAB_NODES_QUERY, user=self.user)

        self.assertIsNone(result.errors)
        nodes = result.data["lab_nodes"]
        self.assertEqual(len(nodes), 5)
        stub = next(node for node in nodes if node["node_name"] == "stub-wan1")
        self.assertEqual((stub["is_stub"], stub["device"], stub["mgmt_ipv4"]), (True, None, "172.20.21.4"))

    def test_query_for_owner_returns_error(self):
        """Use the GraphQL API endpoint, because `execute_query` does not validate the field names."""
        response = self.client.post(
            reverse("graphql-api"), {"query": "query { labs { owner { username } } }"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot query field 'owner' on type 'LabType'.", str(response.json()["errors"]))

    def test_filter_arguments(self):
        labs = execute_query('query { labs(name: "lab-b") { name } }', user=self.user)
        stubs = execute_query("query { lab_nodes(is_stub: true) { node_name } }", user=self.user)

        self.assertIsNone(labs.errors)
        self.assertEqual(labs.data["labs"], [{"name": "lab-b"}])
        self.assertIsNone(stubs.errors)
        self.assertEqual(stubs.data["lab_nodes"], [{"node_name": "stub-wan1"}])


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class RelatedObjectPermissionGraphQLTestCase(TestCase):
    """Test that GraphQL hides a related object that the user cannot view."""

    @classmethod
    def setUpTestData(cls):
        create_related_object_data()

    def _user_with(self, *permissions):
        """Give the user only the given permissions, and return the user with an empty permission cache."""
        ObjectPermission.objects.filter(users=self.user).delete()
        self.add_permissions(*permissions)
        return get_user_model().objects.get(pk=self.user.pk)

    def _query(self, query, user):
        """Run the query as the user, check that it has no errors, and return the data."""
        result = execute_query(query, user=user)
        self.assertIsNone(result.errors)
        return result.data

    def test_related_object_without_view_permission_is_hidden(self):
        for permission, query, expected in HIDDEN_RELATED_OBJECT_ROWS:
            with self.subTest(query=query):
                self.assertEqual(self._query(query, self._user_with(permission)), expected)

        with self.subTest("view permission of the devices"):
            user = self._user_with("containerlab_orchestrator.view_lab", "dcim.view_device")
            data = self._query('query { labs(name: "lab-b") { devices { name } } }', user)
            self.assertEqual(data, {"labs": [{"devices": [{"name": "leaf2"}]}]})

        with self.subTest("constrained view permission of the devices"):
            self._user_with("containerlab_orchestrator.view_labnode")
            grant_permission(self.user, Device, "view", {"name": "leaf1"})
            user = get_user_model().objects.get(pk=self.user.pk)
            nodes = self._query("query { lab_nodes { node_name device { name } } }", user)["lab_nodes"]
            self.assertEqual(
                [node["device"] for node in nodes if node["node_name"] == "leaf1"], [{"name": "leaf1"}] * 2
            )
            self.assertEqual([node["device"] for node in nodes if node["node_name"] == "spine1"], [None])
