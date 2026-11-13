"""Tests for the ContainerlabHost and NodeMapping GraphQL types."""

from nautobot.apps.graphql import execute_query
from nautobot.apps.testing import TestCase

from containerlab_orchestrator.tests.fixtures import (
    SECRET_VALUE,
    create_containerlab_hosts,
    create_node_mappings,
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
