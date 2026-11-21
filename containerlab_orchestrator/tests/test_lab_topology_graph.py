"""Tests for the Topology tab of the lab page."""

import json
import re
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from nautobot.apps.testing import TestCase
from nautobot.extras.models import Job

from containerlab_orchestrator.graph import (
    NO_NODE_PERMISSION_TEXT,
    NO_VIEWABLE_NODES_TEXT,
    SKIPPED_LINKS_TEXT,
    STATE_GROUPS,
    UNREADABLE_YAML_TEXT,
    lab_graph,
)
from containerlab_orchestrator.models import LabNode
from containerlab_orchestrator.sync import LAB_JOBS_MODULE
from containerlab_orchestrator.tests.fixtures import LINKS_YAML, grant_permission, store_yaml
from containerlab_orchestrator.tests.test_lab_job_buttons import LAB_JOB_PERMISSIONS
from containerlab_orchestrator.tests.test_views import LAB_DETAIL_PERMISSIONS, create_lab_view_data
from containerlab_orchestrator.views import (
    GRAPH_CAPTION_TEXT,
    GRAPH_NO_LIBRARY_TEXT,
    GRAPH_YAML_LIMIT,
    LARGE_GRAPH_TEXT,
    NO_BUILD_TEXT,
)

CHART_ELEMENT = 'id="lab-topology-graph"'
CHART_CONFIG_ID = "echarts-config-lab-topology-graph"
GHOST_LINK_YAML = f'{LINKS_YAML}    - endpoints: ["ghost:eth1", "leaf1:eth2"]\n'
YAML_HEAD = "topology: {}\n"


def topology_pane(body):
    """Return the HTML of the Topology tab pane in the lab page body."""
    return body[body.index('<div id="topology"') : body.index('<div id="topology_yaml"')]


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class LabTopologyGraphTabTestCase(TestCase):
    """Test the Topology tab, its graph data, its legend, and its messages."""

    @classmethod
    def setUpTestData(cls):
        _, _, labs = create_lab_view_data()
        cls.lab_a = labs[0]
        store_yaml(cls.lab_a, LINKS_YAML)

    def setUp(self):
        super().setUp()
        self.add_permissions(*LAB_DETAIL_PERMISSIONS)

    def get_lab_a_body(self):
        """Return the HTML of the lab-a (clab01) page."""
        response = self.client.get(self.lab_a.get_absolute_url())
        self.assertHttpStatus(response, 200)
        return response.content.decode()

    def assert_no_chart(self, body):
        """Assert that the page body has no chart element and no graph data."""
        self.assertNotIn(CHART_ELEMENT, body)
        self.assertNotIn(CHART_CONFIG_ID, body)

    def test_topology_tab_comes_after_main_and_before_topology_yaml(self):
        body = self.get_lab_a_body()

        self.assertLess(body.index('aria-controls="main"'), body.index('aria-controls="topology"'))
        self.assertLess(body.index('aria-controls="topology"'), body.index('aria-controls="topology_yaml"'))
        self.assertIsNotNone(re.search(r'aria-controls="topology"[^>]*>\s*Topology\s*</a>', body))

    def test_panel_body_id_does_not_repeat_the_tab_id(self):
        body = self.get_lab_a_body()

        self.assertEqual(body.count('id="topology"'), 1)
        self.assertIn('id="topology-graph"', body)

    def test_tab_with_no_build_shows_message_and_no_chart(self):
        store_yaml(self.lab_a, "")

        body = self.get_lab_a_body()

        self.assertIn(NO_BUILD_TEXT, topology_pane(body))
        self.assert_no_chart(body)

    def test_tab_holds_graph_data_as_json_script(self):
        body = self.get_lab_a_body()

        match = re.search(rf'<script id="{CHART_CONFIG_ID}" type="application/json">(.*?)</script>', body)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1)), lab_graph(self.lab_a, self.user).data)
        self.assertIn(CHART_ELEMENT, topology_pane(body))

    def test_graph_json_escapes_html_in_node_values(self):
        LabNode.objects.filter(lab=self.lab_a, node_name="leaf1").update(image="x<script>alert(1)</script>")

        body = self.get_lab_a_body()

        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("\\u003Cscript\\u003E", body)

    def test_tab_shows_legend_caption_and_stub_shape(self):
        pane = topology_pane(self.get_lab_a_body())

        for group in STATE_GROUPS:
            with self.subTest(group=group.key):
                self.assertIn(f'<i class="mdi mdi-circle" style="color: {group.color}"></i> {group.label}', pane)
        self.assertIn("Stub node", pane)
        self.assertIn("mdi-square", pane)
        self.assertNotIn("mdi-square-outline", pane)
        self.assertIn(GRAPH_CAPTION_TEXT, pane)

    def test_tab_without_node_permission_shows_message_and_no_chart(self):
        self.remove_permissions("containerlab_orchestrator.view_labnode")

        body = self.get_lab_a_body()

        self.assertIn(NO_NODE_PERMISSION_TEXT, topology_pane(body))
        self.assert_no_chart(body)

    def test_tab_shows_skipped_link_count(self):
        store_yaml(self.lab_a, GHOST_LINK_YAML)

        pane = topology_pane(self.get_lab_a_body())

        self.assertIn(SKIPPED_LINKS_TEXT.format(count=1), pane)
        store_yaml(self.lab_a, LINKS_YAML)
        self.assertNotIn("Links that the graph does not show", topology_pane(self.get_lab_a_body()))

    def test_tab_with_large_yaml_shows_size_text(self):
        store_yaml(self.lab_a, YAML_HEAD + "#" * (GRAPH_YAML_LIMIT + 1 - len(YAML_HEAD)))

        with mock.patch("containerlab_orchestrator.views.lab_graph", wraps=lab_graph) as read_graph:
            body = self.get_lab_a_body()

        read_graph.assert_not_called()
        self.assertIn(LARGE_GRAPH_TEXT.format(size=f"{GRAPH_YAML_LIMIT + 1:,} characters"), topology_pane(body))
        self.assert_no_chart(body)
        store_yaml(self.lab_a, YAML_HEAD + "#" * (GRAPH_YAML_LIMIT - len(YAML_HEAD)))
        with mock.patch("containerlab_orchestrator.views.lab_graph", wraps=lab_graph) as read_graph:
            pane = topology_pane(self.get_lab_a_body())
        read_graph.assert_called_once()
        self.assertNotIn("The topology YAML has", pane)
        self.assertIn(CHART_ELEMENT, pane)

    def test_lab_page_loads_no_script_from_another_site(self):
        srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', self.get_lab_a_body())

        self.assertTrue(any("dist/js/libraries.js" in src for src in srcs))
        for src in srcs:
            with self.subTest(src=src):
                self.assertTrue(src.startswith("/"))
                self.assertFalse(src.startswith("//"))

    def test_tab_with_unreadable_yaml_shows_message(self):
        store_yaml(self.lab_a, "topology: [")

        body = self.get_lab_a_body()

        self.assertIn(UNREADABLE_YAML_TEXT, topology_pane(body))
        self.assert_no_chart(body)

    def test_topology_tab_shows_no_lab_job_buttons(self):
        self.add_permissions(*LAB_JOB_PERMISSIONS)
        Job.objects.filter(module_name=LAB_JOBS_MODULE).update(enabled=True)
        self.user.is_superuser = True
        self.user.save()
        build_url = reverse(
            "extras:job_run_by_class_path", kwargs={"class_path": f"{LAB_JOBS_MODULE}.BuildLabTopology"}
        )

        body = self.get_lab_a_body()
        pane = topology_pane(body)

        self.assertIn(build_url, body)
        self.assertNotIn(build_url, pane)
        self.assertIn("Download YAML", body)
        self.assertNotIn("Download YAML", pane)

    def test_tab_with_constraint_that_matches_no_row_shows_message(self):
        self.remove_permissions("containerlab_orchestrator.view_labnode")
        grant_permission(self.user, LabNode, "view", constraints={"node_name": "none"})

        body = self.get_lab_a_body()

        self.assertIn(NO_VIEWABLE_NODES_TEXT, topology_pane(body))
        self.assert_no_chart(body)

    def test_graph_script_waits_for_the_page_and_watches_the_size(self):
        pane = topology_pane(self.get_lab_a_body())

        for text in (
            "DOMContentLoaded",
            "ResizeObserver",
            "[data-no-library-text]",
            f'data-no-library-text="{GRAPH_NO_LIBRARY_TEXT}"',
            "darkMode",
        ):
            with self.subTest(text=text):
                self.assertIn(text, pane)
        self.assertNotIn("window.nb.echarts", pane)
        self.assertNotIn("MutationObserver", pane)
