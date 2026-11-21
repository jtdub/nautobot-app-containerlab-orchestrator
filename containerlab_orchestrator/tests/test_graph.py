"""Tests for the node data of the lab graph."""

import json

from django.test import override_settings
from nautobot.apps.testing import TestCase

from containerlab_orchestrator.graph import STATE_GROUPS, lab_graph, state_group
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


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class LabGraphTestCase(TestCase):
    """Test the state groups and the node data of lab-a."""

    @classmethod
    def setUpTestData(cls):
        lab_devices = create_lab_devices()
        labs = create_labs(create_containerlab_hosts(), lab_devices)
        create_lab_nodes(labs, lab_devices)
        cls.lab_a = labs[0]
        store_yaml(cls.lab_a, LINKS_YAML)

    def node_graph(self):
        """Return the graph of lab-a for the test user, with the view permission of the node rows."""
        grant_permission(self.user, LabNode, "view")
        return lab_graph(self.lab_a, self.user)

    def test_state_group_maps_running_empty_and_other_states(self):
        self.assertEqual(state_group("running"), "running")
        self.assertEqual(state_group(""), "not_inspected")
        for state in ("exited", "created", "paused", "Running"):
            with self.subTest(state=state):
                self.assertEqual(state_group(state), "not_running")

    def test_lab_graph_lists_each_viewable_row_with_hover_fields(self):
        graph = self.node_graph()

        self.assertEqual(graph.message, "")
        nodes = graph.data["nodes"]
        self.assertEqual(
            nodes[0],
            {
                "name": "leaf1",
                "group": "running",
                "stub": False,
                "info": {"kind": "ceos", "image": "ceos:4.32.0F", "mgmt_ipv4": "172.20.21.2", "state": "running"},
            },
        )
        self.assertEqual([node["name"] for node in nodes], ["leaf1", "spine1", "stub-wan1"])
        self.assertEqual([node["name"] for node in nodes if node["stub"]], ["stub-wan1"])

    def test_lab_graph_does_not_list_rows_of_another_lab(self):
        graph = self.node_graph()

        names = [node["name"] for node in graph.data["nodes"]]
        images = [node["info"]["image"] for node in graph.data["nodes"]]
        self.assertNotIn("leaf2", names)
        self.assertNotIn("ghcr.io/nokia/srlinux:24.10", images)

    def test_lab_graph_gives_empty_text_for_no_address_and_no_state(self):
        LabNode.objects.filter(lab=self.lab_a, node_name="leaf1").update(mgmt_ipv4=None, state="")

        leaf1 = self.node_graph().data["nodes"][0]

        self.assertEqual(leaf1["info"]["mgmt_ipv4"], "")
        self.assertEqual(leaf1["info"]["state"], "")
        self.assertEqual(leaf1["group"], "not_inspected")

    def test_lab_graph_data_has_no_echarts_theme_keys(self):
        graph = self.node_graph()

        self.assertTrue(set(graph.data).isdisjoint({"color", "legend", "title", "xAxis", "yAxis"}))
        self.assertEqual(
            graph.data["groups"], {group.key: {"label": group.label, "color": group.color} for group in STATE_GROUPS}
        )
        self.assertFalse(any("value" in node for node in graph.data["nodes"]))
        json.dumps(graph.data)

    def test_lab_graph_superuser_sees_all_rows(self):
        self.user.is_superuser = True
        self.user.save()

        graph = lab_graph(self.lab_a, self.user)

        self.assertEqual(len(graph.data["nodes"]), 3)

    def test_lab_graph_has_no_links_before_the_link_part(self):
        graph = self.node_graph()

        self.assertEqual(graph.data["links"], [])
        self.assertEqual(graph.data["skipped_links"], 0)
