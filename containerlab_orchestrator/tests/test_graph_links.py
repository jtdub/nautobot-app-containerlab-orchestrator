"""Tests for the link data of the lab graph."""

import yaml
from django.test import override_settings
from nautobot.apps.testing import TestCase

from containerlab_orchestrator.graph import UNREADABLE_YAML_TEXT, YAML_LOADER, LabGraph, lab_graph
from containerlab_orchestrator.models import LabNode
from containerlab_orchestrator.tests.fixtures import (
    LINKS_YAML,
    create_containerlab_hosts,
    create_lab_devices,
    create_lab_nodes,
    create_labs,
    grant_permission,
    store_yaml,
)

DRAWN_LINKS = [
    {"source": "leaf1", "target": "spine1", "endpoints_text": "leaf1:eth1 to spine1:eth1"},
    {"source": "spine1", "target": "stub-wan1", "endpoints_text": "spine1:eth2 to stub-wan1:eth1"},
]
"""The graph lines of the two links of LINKS_YAML."""

LINKS_THAT_THE_GRAPH_SKIPS = """    - endpoints: ["ghost:eth1", "leaf1:eth2"]
    - endpoints: ["host:veth1", "leaf1:eth3"]
    - endpoints: ["leaf1", "spine1:eth3"]
    - endpoints: [":eth1", "spine1:eth4"]
    - endpoints: ["leaf1:eth4"]
    - bad
"""
"""Six more link items for LINKS_YAML that the graph cannot draw."""


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class LabGraphLinksTestCase(TestCase):
    """Test the links of the lab graph from the stored topology YAML of lab-a."""

    @classmethod
    def setUpTestData(cls):
        lab_devices = create_lab_devices()
        labs = create_labs(create_containerlab_hosts(), lab_devices)
        create_lab_nodes(labs, lab_devices)
        cls.lab_a = labs[0]

    def yaml_graph(self, text):
        """Return the graph of lab-a for the test user, after the lab stores the topology YAML."""
        store_yaml(self.lab_a, text)
        self.lab_a.refresh_from_db()
        return lab_graph(self.lab_a, self.user)

    def test_lab_graph_draws_each_yaml_link_with_endpoint_text(self):
        grant_permission(self.user, LabNode, "view")

        graph = self.yaml_graph(LINKS_YAML)

        self.assertEqual(graph.data["links"], DRAWN_LINKS)
        self.assertEqual(graph.data["skipped_links"], 0)

    def test_lab_graph_skips_and_counts_links_it_cannot_draw(self):
        grant_permission(self.user, LabNode, "view")

        graph = self.yaml_graph(LINKS_YAML + LINKS_THAT_THE_GRAPH_SKIPS)

        self.assertEqual(graph.data["links"], DRAWN_LINKS)
        self.assertEqual(graph.data["skipped_links"], 6)

    def test_lab_graph_skips_links_to_rows_the_user_cannot_view(self):
        grant_permission(self.user, LabNode, "view", constraints={"node_name": "leaf1"})

        graph = self.yaml_graph(LINKS_YAML)

        self.assertEqual(graph.data["links"], [])
        self.assertEqual(graph.data["skipped_links"], 2)

    def test_lab_graph_with_no_links_section_draws_rows_only(self):
        grant_permission(self.user, LabNode, "view")

        for text in ("name: x\n", "topology: {}\n", "topology:\n", "topology:\n  links:\n"):
            with self.subTest(text=text):
                graph = self.yaml_graph(text)

                self.assertEqual(graph.message, "")
                self.assertEqual(len(graph.data["nodes"]), 3)
                self.assertEqual(graph.data["links"], [])
                self.assertEqual(graph.data["skipped_links"], 0)

    def test_lab_graph_with_unreadable_yaml_gives_message(self):
        grant_permission(self.user, LabNode, "view")

        for text in ("topology: [", "- a list\n", "topology: [1]\n", "topology:\n  links: 5\n"):
            with self.subTest(text=text):
                self.assertEqual(self.yaml_graph(text), LabGraph(None, UNREADABLE_YAML_TEXT))

    def test_yaml_loader_is_the_c_loader_when_available(self):
        self.assertIs(YAML_LOADER, getattr(yaml, "CSafeLoader", yaml.SafeLoader))
