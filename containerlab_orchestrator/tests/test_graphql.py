"""Tests for the ContainerlabHost GraphQL type."""

from nautobot.apps.graphql import execute_query
from nautobot.apps.testing import TestCase

from containerlab_orchestrator.tests.fixtures import SECRET_VALUE, create_containerlab_hosts, patch_secret_variables

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
