"""Tests for the inspect service that reads the containers of a lab on its host."""

from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from nautobot.extras.models import Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.deploy import UNKNOWN_CONTAINERS_WARNING, error_text
from containerlab_orchestrator.destroy import NOT_FOUND_WARNING
from containerlab_orchestrator.inspection import (
    HELPERS_WARNING,
    INSPECT_DELETED_MESSAGE,
    INSPECT_GUARD_MESSAGE,
    INSPECT_STATUS_MESSAGE,
    NOT_RUNNING_WARNING,
    inspect_lab,
)
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.tests.test_build import BuildLabTestCase
from containerlab_orchestrator.tests.test_deploy import LOCKED_LAB_READ, OLD_ADDRESS, OLD_INSPECT, PLANNED_ADDRESSES

HOST_USER = "clab-user"
START_STATUSES = (
    LabStatusChoices.BUILT,
    LabStatusChoices.DEPLOYED,
    LabStatusChoices.FAILED,
    LabStatusChoices.DESTROYED,
)
SEED_ROWS = {"arista1": ("old", "old", OLD_ADDRESS), "leaf1": ("old", "old", OLD_ADDRESS)}
CLEARED_ROWS = {name: ("", "", address) for name, address in PLANNED_ADDRESSES.items()}
RUNNING_ROWS = {name: ("running", f"id-{name}", address) for name, address in PLANNED_ADDRESSES.items()}


def inspect_error(status, server_message=None, timed_out=False):
    """Return the ClabApiError of an inspect call that got this HTTP status."""
    return ClabApiError(
        f"Host clab01: GET /api/v1/labs/build-lab returned HTTP {status}: {server_message or 'Not Found'}",
        status_code=status,
        server_message=server_message,
        timed_out=timed_out,
    )


def plain_not_found():
    """Return the 404 error of a host that has no container of the lab."""
    return inspect_error(404, "lab 'build-lab' not found")


class InspectLabTestCase(BuildLabTestCase):  # pylint: disable=too-many-ancestors
    """Base class with the Deployed lab build-lab, old node state, and a mocked client whose nodes run."""

    def setUp(self):
        """Build the lab, set Deployed, seed old values, and mock a client that lists a running container per row."""
        super().setUp()
        build_lab(self.lab)
        self._seed(LabStatusChoices.DEPLOYED)
        patcher = mock.patch("containerlab_orchestrator.inspection.ClabApiClient", autospec=True)
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.client_mock = self.client_class.return_value
        self.client_mock.username = HOST_USER
        self.containers = [
            {
                "name": f"clab-build-lab-{row.node_name}",
                "nodeName": row.node_name,
                "state": "running",
                "container_id": f"id-{row.node_name}",
                "owner": HOST_USER,
                "image": row.image,
                "ipv4_address": f"{PLANNED_ADDRESSES[row.node_name]}/24",
            }
            for row in LabNode.objects.filter(lab=self.lab).order_by("node_name")
        ]
        self.client_mock.inspect.return_value = self.containers

    def _seed(self, status):
        """Set the status, an old stored inspect result, and an old state, container ID, and address on each row."""
        self._set_status(status)
        Lab.objects.filter(pk=self.lab.pk).update(last_inspect=OLD_INSPECT)
        LabNode.objects.filter(lab=self.lab).update(state="old", container_id="old", mgmt_ipv4=OLD_ADDRESS)

    def _lab(self):
        """Return the saved row of the lab."""
        return Lab.objects.get(pk=self.lab.pk)

    def _node_rows(self):
        """Return the state, the container ID, and the management address of each node row by node name."""
        return {name: (row.state, row.container_id, row.mgmt_ipv4) for name, row in self._rows().items()}

    def _state(self):
        """Return the status name, the last update time, the stored inspect result, and the node rows of the lab."""
        lab = self._lab()
        return lab.status.name, lab.last_updated, lab.last_inspect, self._node_rows()

    def _inspect_quietly(self):
        """Run the inspect, and ignore a ValidationError."""
        try:
            inspect_lab(self.lab)
        except ValidationError:
            pass

    def _assert_fails_with(self, messages):
        """Assert that the inspect fails with these messages."""
        with self.assertRaises(ValidationError) as raised:
            inspect_lab(self.lab)
        self.assertEqual(raised.exception.messages, messages)


class InspectLabResultTestCase(InspectLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the status, the node rows, the stored inspect result, and the warnings of an inspect that succeeds."""

    def test_all_running_sets_deployed_and_writes_the_rows(self):
        for status in START_STATUSES:
            with self.subTest(status=status):
                self._seed(status)
                self.client_class.reset_mock()
                result = inspect_lab(self.lab)
                lab = self._lab()
                self.assertEqual(lab.status.name, LabStatusChoices.DEPLOYED)
                self.assertEqual(self._node_rows(), RUNNING_ROWS)
                self.assertEqual(lab.last_inspect, self.containers)
                self.assertEqual(
                    (result.lab, result.node_count, result.running_count, result.warnings), (lab, 2, 2, [])
                )
                self.client_class.assert_called_once_with(lab.host)
                self.assertEqual(
                    self.client_mock.method_calls,
                    [mock.call.login(), mock.call.inspect("build-lab"), mock.call.close()],
                )

    def test_node_not_running_or_missing_sets_failed(self):
        arista1, leaf1 = self.containers
        cases = {
            "leaf1 exited": ([arista1, {**leaf1, "state": "exited"}], "leaf1", 1),
            "leaf1 missing": ([arista1], "leaf1", 1),
            "both stopped": ([{**arista1, "state": "paused"}, {**leaf1, "state": "exited"}], "arista1, leaf1", 0),
        }
        for case, (containers, nodes, running) in cases.items():
            with self.subTest(case=case):
                self._seed(LabStatusChoices.DEPLOYED)
                self.client_mock.inspect.return_value = containers
                result = inspect_lab(self.lab)
                self.assertEqual(self._lab().status.name, LabStatusChoices.FAILED)
                self.assertEqual(result.warnings, [NOT_RUNNING_WARNING.format(lab=self.lab, nodes=nodes)])
                self.assertEqual((result.node_count, result.running_count), (2, running))

    def test_plain_not_found_clears_the_rows(self):
        cases = (
            (LabStatusChoices.DEPLOYED, LabStatusChoices.DESTROYED),
            (LabStatusChoices.FAILED, LabStatusChoices.DESTROYED),
            (LabStatusChoices.BUILT, LabStatusChoices.BUILT),
            (LabStatusChoices.DESTROYED, LabStatusChoices.DESTROYED),
        )
        self.client_mock.inspect.side_effect = plain_not_found()
        for start, expected in cases:
            with self.subTest(start=start):
                self._seed(start)
                result = inspect_lab(self.lab)
                self.assertEqual((self._lab().status.name, self._lab().last_inspect), (expected, []))
                self.assertEqual(self._node_rows(), CLEARED_ROWS)
                self.assertEqual(result.warnings, [NOT_FOUND_WARNING.format(host=self.lab.host, lab=self.lab)])

    def test_empty_list_is_the_same_as_no_lab(self):
        self.client_mock.inspect.return_value = []
        result = inspect_lab(self.lab)
        self.assertEqual((self._lab().status.name, self._lab().last_inspect), (LabStatusChoices.DESTROYED, []))
        self.assertEqual(self._node_rows(), CLEARED_ROWS)
        self.assertEqual((result.running_count, result.warnings), (0, []))

    def test_helper_containers_only_set_failed(self):
        self.client_mock.inspect.side_effect = inspect_error(404, "Lab 'build-lab' not found.")
        for start in START_STATUSES:
            with self.subTest(start=start):
                self._seed(start)
                result = inspect_lab(self.lab)
                self.assertEqual((self._lab().status.name, self._lab().last_inspect), (LabStatusChoices.FAILED, []))
                self.assertEqual(self._node_rows(), CLEARED_ROWS)
                self.assertEqual(result.warnings, [HELPERS_WARNING.format(host=self.lab.host, lab=self.lab)])

    def test_unknown_containers_warn_and_keep_deployed(self):
        unknown = {"name": "clab-build-lab-old1", "nodeName": "old1", "owner": HOST_USER}
        self.client_mock.inspect.return_value = [*self.containers, unknown]
        result = inspect_lab(self.lab)
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)
        self.assertEqual(
            result.warnings,
            [UNKNOWN_CONTAINERS_WARNING.format(host=self.lab.host, lab=self.lab, names="clab-build-lab-old1")],
        )

    def test_write_is_narrow_and_locks_the_row(self):
        cases = (
            (LabStatusChoices.FAILED, self.containers, LabStatusChoices.DEPLOYED),
            (LabStatusChoices.BUILT, plain_not_found(), LabStatusChoices.BUILT),
        )
        for start, response, expected in cases:
            with self.subTest(start=start):
                self._seed(start)
                before = self._lab().last_updated
                self.client_mock.inspect.side_effect = [response]
                with mock.patch.object(Lab, "save", autospec=True, side_effect=Lab.save) as save:
                    with CaptureQueriesContext(connection) as queries:
                        inspect_lab(self.lab)
                self.assertEqual(self._lab().status.name, expected)
                self.assertEqual(save.call_count, 1)
                self.assertEqual(save.call_args.kwargs, {"update_fields": ["status", "last_inspect", "last_updated"]})
                self.assertGreater(self._lab().last_updated, before)
                self.assertTrue(any(LOCKED_LAB_READ.search(query["sql"]) for query in queries.captured_queries))

    def test_write_rolls_back_the_rows(self):
        before = self._state()
        with mock.patch.object(Lab, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                inspect_lab(self.lab)
        self.assertEqual(self._state(), before)
        self.assertEqual(self._node_rows(), SEED_ROWS)

    def test_unreadable_topology_gives_no_planned_address(self):
        self.client_mock.inspect.return_value = [{**container, "ipv4_address": "N/A"} for container in self.containers]
        for topology_yaml in ("", "name: [x"):
            with self.subTest(topology_yaml=topology_yaml):
                self._seed(LabStatusChoices.DEPLOYED)
                Lab.objects.filter(pk=self.lab.pk).update(topology_yaml=topology_yaml)
                inspect_lab(self.lab)
                self.assertEqual({row[2] for row in self._node_rows().values()}, {OLD_ADDRESS})

    def test_changed_lab_writes_nothing(self):
        def set_failed():
            self._set_status(LabStatusChoices.FAILED)

        def save_lab():
            Lab.objects.get(pk=self.lab.pk).save()

        def destroy_and_deploy_again():
            for name in (LabStatusChoices.DESTROYED, LabStatusChoices.DEPLOYED):
                lab = Lab.objects.get(pk=self.lab.pk)
                lab.status = Status.objects.get(name=name)
                lab.save(job_fields=True)

        for change in (set_failed, save_lab, destroy_and_deploy_again):
            with self.subTest(change=change.__name__):
                self._seed(LabStatusChoices.DEPLOYED)

                def change_then_list(_name, change=change):
                    change()
                    return self.containers

                self.client_mock.inspect.side_effect = change_then_list
                self._assert_fails_with([INSPECT_GUARD_MESSAGE.format(lab=self.lab)])
                self.assertEqual((self._lab().last_inspect, self._node_rows()), (OLD_INSPECT, SEED_ROWS))

    def test_deleted_lab_fails_with_message(self):
        def delete_then_list(_name):
            Lab.objects.filter(pk=self.lab.pk).delete()
            return self.containers

        self.client_mock.inspect.side_effect = delete_then_list
        self._assert_fails_with([INSPECT_DELETED_MESSAGE.format(lab=self.lab)])
        self.assertFalse(Lab.objects.filter(pk=self.lab.pk).exists())

    def test_client_is_closed(self):
        server_error = inspect_error(500, "lab 'build-lab' not found")
        login_error = inspect_error(401, "invalid credentials")
        cases = {
            "list": (None, None, None),
            "plain 404": (None, plain_not_found(), None),
            "500 with the not-found text": (None, server_error, [error_text(server_error)]),
            "login error": (login_error, None, [error_text(login_error)]),
        }
        for case, (login_side_effect, inspect_side_effect, messages) in cases.items():
            with self.subTest(case=case):
                self._seed(LabStatusChoices.DEPLOYED)
                before = self._state()
                self.client_mock.reset_mock()
                self.client_mock.login.side_effect = login_side_effect
                self.client_mock.inspect.side_effect = inspect_side_effect
                if messages:
                    self._assert_fails_with(messages)
                    self.assertEqual(self._state(), before)
                else:
                    inspect_lab(self.lab)
                self.client_mock.close.assert_called_once_with()


class InspectLabFailureTestCase(InspectLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the checks before any API call."""

    def test_planned_lab_fails_with_no_api_call(self):
        self._seed(LabStatusChoices.PLANNED)
        before = self._state()
        status = Status.objects.get(name=LabStatusChoices.PLANNED)
        self._assert_fails_with([INSPECT_STATUS_MESSAGE.format(lab=self.lab, status=status)])
        self.client_class.assert_not_called()
        self.assertEqual(self._state(), before)
