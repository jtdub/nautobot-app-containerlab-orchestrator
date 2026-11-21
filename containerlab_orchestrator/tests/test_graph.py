"""Tests for the node data of the lab graph."""

from django.test import override_settings
from nautobot.apps.testing import TestCase

from containerlab_orchestrator.graph import state_group


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class LabGraphTestCase(TestCase):
    """Test the state groups and the node data of lab-a."""

    def test_state_group_maps_running_empty_and_other_states(self):
        self.assertEqual(state_group("running"), "running")
        self.assertEqual(state_group(""), "not_inspected")
        for state in ("exited", "created", "paused", "Running"):
            with self.subTest(state=state):
                self.assertEqual(state_group(state), "not_running")
