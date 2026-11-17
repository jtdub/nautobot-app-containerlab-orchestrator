"""Tests for the destroy service that removes the lab of a Nautobot lab from its host."""

from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from nautobot.extras.models import Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.deploy import STATUS_MISSING_MESSAGE
from containerlab_orchestrator.destroy import (
    DESTROY_STATUS_MESSAGE,
    NOT_FOUND_WARNING,
    destroy_lab,
)
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.tests.test_build import BuildLabTestCase
from containerlab_orchestrator.tests.test_deploy import LOCKED_LAB_READ

DESTROY_PATH = "DELETE /api/v1/labs/build-lab"
RUNNING_NODES = {"arista1": ("running", "id-arista1"), "leaf1": ("running", "id-leaf1")}
CLEARED_NODES = {"arista1": ("", ""), "leaf1": ("", "")}


def destroy_error(status, server_message=None, timed_out=False):
    """Return the ClabApiError of a destroy call that got this HTTP status, or that got no response in time."""
    if timed_out:
        return ClabApiError(
            f"Host clab01: {DESTROY_PATH} was sent, but no response came in 780 seconds. "
            "The lab state is unknown. Run Inspect.",
            timed_out=True,
        )
    return ClabApiError(
        f"Host clab01: {DESTROY_PATH} returned HTTP {status}: {server_message or 'Not Found'}",
        status_code=status,
        server_message=server_message,
    )


def plain_not_found():
    """Return the 404 error of a host that has no lab of this name."""
    return destroy_error(404, "lab 'build-lab' not found")


class DestroyLabTestCase(BuildLabTestCase):  # pylint: disable=too-many-ancestors
    """Base class with the Deployed lab build-lab, its running nodes, and a mocked client that destroys it."""

    def setUp(self):
        """Build the lab, set Deployed, give each node a container, and mock the client."""
        super().setUp()
        build_lab(self.lab)
        self._set_status(LabStatusChoices.DEPLOYED)
        for name, (state, container_id) in RUNNING_NODES.items():
            LabNode.objects.filter(lab=self.lab, node_name=name).update(state=state, container_id=container_id)
        patcher = mock.patch("containerlab_orchestrator.destroy.ClabApiClient", autospec=True)
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.client_mock = self.client_class.return_value
        self.client_mock.destroy.return_value = {"message": "Lab 'build-lab' destroyed successfully"}

    def _lab(self):
        """Return the saved row of the lab."""
        return Lab.objects.get(pk=self.lab.pk)

    def _node_state(self):
        """Return the state and the container ID of each node row by node name."""
        return {row.node_name: (row.state, row.container_id) for row in self._rows().values()}

    def _state(self):
        """Return the status name, the last update time, and the node state of the lab."""
        lab = self._lab()
        return lab.status.name, lab.last_updated, self._node_state()

    def _destroy_quietly(self, **kwargs):
        """Run the destroy, and ignore a ValidationError."""
        try:
            destroy_lab(self.lab, **kwargs)
        except ValidationError:
            pass

    def _assert_fails_with(self, messages):
        """Assert that the destroy fails with these messages."""
        with self.assertRaises(ValidationError) as raised:
            destroy_lab(self.lab)
        self.assertEqual(raised.exception.messages, messages)


class DestroyLabResultTestCase(DestroyLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the status, the node state, the warnings, and the client calls of a destroy that succeeds."""

    def test_destroy_sets_destroyed_and_clears_node_state(self):
        result = destroy_lab(self.lab)
        lab = self._lab()
        self.assertEqual(lab.status.name, LabStatusChoices.DESTROYED)
        self.assertEqual(self._node_state(), CLEARED_NODES)
        self.assertEqual((result.lab, result.node_count, result.warnings), (lab, 2, []))
        self.client_class.assert_called_once_with(lab.host)
        self.assertEqual(
            self.client_mock.method_calls,
            [mock.call.login(), mock.call.destroy("build-lab", cleanup=True), mock.call.close()],
        )

    def test_keep_option_sends_cleanup_false(self):
        destroy_lab(self.lab, keep_lab_directory=True)
        self.client_mock.destroy.assert_called_once_with("build-lab", cleanup=False)
        self.assertEqual(self._lab().status.name, LabStatusChoices.DESTROYED)

    def test_failed_lab_can_destroy(self):
        self._set_status(LabStatusChoices.FAILED)
        destroy_lab(self.lab)
        self.assertEqual(self._lab().status.name, LabStatusChoices.DESTROYED)
        self.assertEqual(self._node_state(), CLEARED_NODES)

    def test_plain_not_found_gives_warning_and_sets_destroyed(self):
        self.client_mock.destroy.side_effect = plain_not_found()
        result = destroy_lab(self.lab)
        self.assertEqual(result.warnings, [NOT_FOUND_WARNING.format(host=self.lab.host, lab=self.lab)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.DESTROYED)
        self.assertEqual(self._node_state(), CLEARED_NODES)

    def test_ok_status_error_counts_as_destroyed(self):
        self.client_mock.destroy.side_effect = ClabApiError(
            f"Host clab01: {DESTROY_PATH} returned a response that is not JSON.", status_code=200
        )
        result = destroy_lab(self.lab)
        self.assertEqual((result.node_count, result.warnings), (2, []))
        self.assertEqual(self._lab().status.name, LabStatusChoices.DESTROYED)
        self.assertEqual(self._node_state(), CLEARED_NODES)

    def test_destroyed_write_rolls_back_the_rows(self):
        with mock.patch.object(Lab, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                destroy_lab(self.lab)
        self.assertEqual(self._node_state(), RUNNING_NODES)
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)

    def test_status_writes_are_narrow_and_lock_the_row(self):
        for status, error in ((LabStatusChoices.DESTROYED, None), (LabStatusChoices.FAILED, destroy_error(500))):
            with self.subTest(status=status):
                self._set_status(LabStatusChoices.DEPLOYED)
                start = self._lab().last_updated
                self.client_mock.destroy.side_effect = error
                with mock.patch.object(Lab, "save", autospec=True, side_effect=Lab.save) as save:
                    with CaptureQueriesContext(connection) as queries:
                        self._destroy_quietly()
                self.assertEqual(self._lab().status.name, status)
                self.assertEqual(save.call_count, 1)
                self.assertEqual(save.call_args.kwargs, {"update_fields": ["status", "last_updated"]})
                self.assertGreater(self._lab().last_updated, start)
                self.assertTrue(any(LOCKED_LAB_READ.search(query["sql"]) for query in queries.captured_queries))

    def test_client_is_closed(self):
        cases = {
            "2xx": (None, None),
            "plain 404": (None, plain_not_found()),
            "500": (None, destroy_error(500)),
            "login error": (destroy_error(401, "invalid credentials"), None),
        }
        for case, (login_error, error) in cases.items():
            with self.subTest(case=case):
                self._set_status(LabStatusChoices.DEPLOYED)
                self.client_mock.reset_mock()
                self.client_mock.login.side_effect = login_error
                self.client_mock.destroy.side_effect = error
                self._destroy_quietly()
                self.client_mock.close.assert_called_once_with()


class DestroyLabFailureTestCase(DestroyLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the checks before any API call, and the status after each failed client call."""

    def _assert_fails_with_no_api_call(self, messages):
        """Assert that the destroy fails with these messages, makes no client, and does not change the lab."""
        before = self._state()
        self._assert_fails_with(messages)
        self.client_class.assert_not_called()
        self.assertEqual(self._state(), before)

    def test_status_that_cannot_destroy_fails_with_no_api_call(self):
        for status in (LabStatusChoices.PLANNED, LabStatusChoices.BUILT, LabStatusChoices.DESTROYED):
            with self.subTest(status=status):
                self._set_status(status)
                self._assert_fails_with_no_api_call([DESTROY_STATUS_MESSAGE.format(lab=self.lab, status=status)])

    def test_missing_status_fails_with_no_api_call(self):
        lab_type = ContentType.objects.get_for_model(Lab)
        for name in (LabStatusChoices.DESTROYED, LabStatusChoices.FAILED):
            with self.subTest(status=name):
                status = Status.objects.get(name=name)
                status.content_types.remove(lab_type)
                self._assert_fails_with_no_api_call([STATUS_MISSING_MESSAGE.format(status=name)])
                status.content_types.add(lab_type)
