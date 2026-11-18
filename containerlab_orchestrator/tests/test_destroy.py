"""Tests for the destroy service that removes the lab of a Nautobot lab from its host."""

from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from nautobot.extras.models import Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.deploy import STATUS_MISSING_MESSAGE, error_text
from containerlab_orchestrator.destroy import (
    DESTROY_BUSY_MESSAGE,
    DESTROY_LAB_DELETED_MESSAGE,
    DESTROY_STATUS_CHANGED_MESSAGE,
    DESTROY_STATUS_MESSAGE,
    DESTROYED_DELETED_MESSAGE,
    DESTROYED_GUARD_MESSAGE,
    NOT_FOUND_WARNING,
    NOT_OWNED_MESSAGE,
    DestroyTimeLimit,
    destroy_lab,
)
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.tests.test_build import BuildLabTestCase
from containerlab_orchestrator.tests.test_deploy import LOCKED_LAB_READ, OLD_ADDRESS, OLD_INSPECT

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
        Lab.objects.filter(pk=self.lab.pk).update(last_inspect=OLD_INSPECT)
        LabNode.objects.filter(lab=self.lab).update(mgmt_ipv4=OLD_ADDRESS)
        result = destroy_lab(self.lab)
        lab = self._lab()
        self.assertEqual(lab.status.name, LabStatusChoices.DESTROYED)
        self.assertEqual(self._node_state(), CLEARED_NODES)
        self.assertEqual(lab.last_inspect, [])
        self.assertEqual({row.mgmt_ipv4 for row in self._rows().values()}, {OLD_ADDRESS})
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
        cases = ((LabStatusChoices.DESTROYED, None, []), (LabStatusChoices.FAILED, destroy_error(500), {}))
        for status, error, last_inspect in cases:
            with self.subTest(status=status):
                self._set_status(LabStatusChoices.DEPLOYED)
                Lab.objects.filter(pk=self.lab.pk).update(last_inspect=OLD_INSPECT)
                start = self._lab().last_updated
                self.client_mock.destroy.side_effect = error
                with mock.patch.object(Lab, "save", autospec=True, side_effect=Lab.save) as save:
                    with CaptureQueriesContext(connection) as queries:
                        self._destroy_quietly()
                self.assertEqual((self._lab().status.name, self._lab().last_inspect), (status, last_inspect))
                self.assertEqual(save.call_count, 1)
                self.assertEqual(save.call_args.kwargs, {"update_fields": ["status", "last_inspect", "last_updated"]})
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
        for status in Status.objects.filter(name__in=[LabStatusChoices.DESTROYED, LabStatusChoices.FAILED]):
            with self.subTest(status=status.name):
                status.content_types.remove(lab_type)
                try:
                    self._assert_fails_with_no_api_call([STATUS_MISSING_MESSAGE.format(status=status.name)])
                finally:
                    status.content_types.add(lab_type)

    def _assert_keeps_state(self, error, messages):
        """Assert that a destroy call with this error fails with these messages and does not change the lab."""
        self._set_status(LabStatusChoices.DEPLOYED)
        self.client_mock.destroy.side_effect = error
        before = self._state()
        self._assert_fails_with(messages)
        self.assertEqual(self._state(), before)

    def test_login_error_keeps_status(self):
        error = plain_not_found()
        self.client_mock.login.side_effect = error
        before = self._state()
        self._assert_fails_with([error.message])
        self.client_mock.destroy.assert_not_called()
        self.assertEqual(self._state(), before)
        self.client_mock.login.side_effect = SoftTimeLimitExceeded()
        with self.assertRaises(SoftTimeLimitExceeded) as raised:
            destroy_lab(self.lab)
        self.assertNotIsInstance(raised.exception, DestroyTimeLimit)
        self.client_mock.destroy.assert_not_called()
        self.assertEqual(self._state(), before)

    def test_other_not_found_keeps_status(self):
        not_owned = destroy_error(404, "lab 'build-lab' not found or not owned by user")
        cases = {
            "not owned": (not_owned, [NOT_OWNED_MESSAGE.format(host=self.lab.host, lab=self.lab)]),
            "no server text": (destroy_error(404), None),
            "other lab": (destroy_error(404, "lab 'other-lab' not found"), None),
        }
        for case, (error, messages) in cases.items():
            with self.subTest(case=case):
                self._assert_keeps_state(error, messages or [error.message])

    def test_host_change_sets_failed(self):
        closed = ClabApiError(
            f"Host clab01: {DESTROY_PATH} was sent, but the connection closed before a response came. "
            "The lab state is unknown.",
            timed_out=True,
        )
        cases = {
            "500": destroy_error(500),
            "501": destroy_error(501),
            "read timeout": destroy_error(None, timed_out=True),
            "connection closed": closed,
            "500 with the not-owned text": destroy_error(500, "lab 'build-lab' not found or not owned by user"),
        }
        for case, error in cases.items():
            with self.subTest(case=case):
                self._set_status(LabStatusChoices.DEPLOYED)
                self.client_mock.destroy.side_effect = error
                self._assert_fails_with([error.message])
                self.assertEqual(self._lab().status.name, LabStatusChoices.FAILED)
                self.assertEqual(self._node_state(), RUNNING_NODES)

    def test_conflict_and_other_errors_keep_status(self):
        conflict = destroy_error(409, "lab 'build-lab' is busy")
        busy_not_found = destroy_error(409, "lab 'build-lab' not found")
        cases = {
            "409": (conflict, [conflict.message, DESTROY_BUSY_MESSAGE.format(host=self.lab.host, lab=self.lab)]),
            "409 with the plain text": (
                busy_not_found,
                [busy_not_found.message, DESTROY_BUSY_MESSAGE.format(host=self.lab.host, lab=self.lab)],
            ),
            "400": (destroy_error(400, "bad request"), None),
            "401": (destroy_error(401, "unauthorized"), None),
            "403": (destroy_error(403, "forbidden"), None),
            "connection error": (ClabApiError("Host clab01: cannot reach https://clab01: ConnectionError."), None),
            "no call": (ClabApiError("Host clab01: the lab name 'x y' is not valid."), None),
        }
        for case, (error, messages) in cases.items():
            with self.subTest(case=case):
                self._assert_keeps_state(error, messages or [error.message])

    def test_failed_write_skips_a_changed_status(self):
        destroyed = Status.objects.get(name=LabStatusChoices.DESTROYED)

        def change_then_fail(*_args, **_kwargs):
            Lab.objects.filter(pk=self.lab.pk).update(status=destroyed)
            raise destroy_error(500)

        self.client_mock.destroy.side_effect = change_then_fail
        self._assert_fails_with([destroy_error(500).message, DESTROY_STATUS_CHANGED_MESSAGE.format(lab=self.lab)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.DESTROYED)

    def test_destroyed_write_skips_a_changed_status(self):
        failed = Status.objects.get(name=LabStatusChoices.FAILED)

        def change_then_return(*_args, **_kwargs):
            Lab.objects.filter(pk=self.lab.pk).update(status=failed)
            return {}

        self.client_mock.destroy.side_effect = change_then_return
        self._assert_fails_with([DESTROYED_GUARD_MESSAGE.format(lab=self.lab, host=self.lab.host)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.FAILED)
        self.assertEqual(self._node_state(), RUNNING_NODES)

    def test_soft_time_limit_paths(self):
        with self.subTest(case="destroy"):
            self.client_mock.destroy.side_effect = SoftTimeLimitExceeded()
            with self.assertRaises(DestroyTimeLimit):
                destroy_lab(self.lab)
            self.assertEqual(self._lab().status.name, LabStatusChoices.FAILED)
            self.assertEqual(self._node_state(), RUNNING_NODES)
            self.client_mock.close.assert_called_once_with()
        with self.subTest(case="destroy with a changed status"):
            self._set_status(LabStatusChoices.DEPLOYED)
            destroyed = Status.objects.get(name=LabStatusChoices.DESTROYED)

            def change_then_stop(*_args, **_kwargs):
                Lab.objects.filter(pk=self.lab.pk).update(status=destroyed)
                raise SoftTimeLimitExceeded()

            self.client_mock.destroy.side_effect = change_then_stop
            with self.assertRaises(DestroyTimeLimit):
                destroy_lab(self.lab)
            self.assertEqual(self._lab().status.name, LabStatusChoices.DESTROYED)
        with self.subTest(case="destroy with the row deleted"):
            self._set_status(LabStatusChoices.DEPLOYED)
            lab_pk = self.lab.pk

            def delete_then_stop(*_args, **_kwargs):
                Lab.objects.filter(pk=lab_pk).delete()
                raise SoftTimeLimitExceeded()

            self.client_mock.destroy.side_effect = delete_then_stop
            with transaction.atomic():
                with self.assertRaises(DestroyTimeLimit):
                    destroy_lab(self.lab)
                transaction.set_rollback(True)
        with self.subTest(case="Destroyed write"):
            self._set_status(LabStatusChoices.DEPLOYED)
            self.client_mock.destroy.side_effect = None
            with mock.patch.object(Lab, "save", side_effect=SoftTimeLimitExceeded()) as save:
                with self.assertRaises(SoftTimeLimitExceeded) as raised:
                    destroy_lab(self.lab)
            self.assertNotIsInstance(raised.exception, DestroyTimeLimit)
            self.assertEqual(save.call_count, 1)
            self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)
            self.assertEqual(self._node_state(), RUNNING_NODES)

    def test_deleted_lab_fails_with_message(self):
        lab, host = self._lab(), self._lab().host
        cases = {
            "500": (destroy_error(500), DESTROY_LAB_DELETED_MESSAGE),
            "2xx": (None, DESTROYED_DELETED_MESSAGE),
            "plain 404": (plain_not_found(), DESTROYED_DELETED_MESSAGE),
        }
        for case, (error, message) in cases.items():
            with self.subTest(case=case):

                def delete_then_return(*_args, error=error, **_kwargs):
                    Lab.objects.filter(pk=lab.pk).delete()
                    if error:
                        raise error
                    return {}

                self.client_mock.destroy.side_effect = delete_then_return
                with transaction.atomic():
                    self._assert_fails_with([message.format(lab=lab, host=host)])
                    self.assertFalse(Lab.objects.filter(pk=lab.pk).exists())
                    transaction.set_rollback(True)

    def test_server_text_is_cut(self):
        error = destroy_error(500, "a" * 594 + "MARKER")
        self.client_mock.destroy.side_effect = error
        self._assert_fails_with([error_text(error)])
        self.assertNotIn("MARKER", error_text(error))
