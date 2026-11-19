"""Tests for the sync service that inspects each lab in scope on its host."""

from unittest import TestCase, mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from nautobot.apps.testing import TransactionTestCase
from nautobot.core.utils.logging import sanitize
from nautobot.extras.models import Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.deploy import NO_TOPOLOGY_MESSAGE, SERVER_TEXT_LIMIT, STATUS_MISSING_MESSAGE
from containerlab_orchestrator.inspection import write_inspect
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.sync import (
    SYNC_CHANGED_WARNING,
    SYNC_DELETED_WARNING,
    SYNC_ERROR_LINE,
    SYNC_FAILED_LINE,
    SYNC_HOST_SKIPPED_MESSAGE,
    SYNC_TIME_LIMIT_MESSAGE,
    SyncKind,
    sync_labs,
    sync_targets,
)
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_lab_devices,
    create_lab_statuses,
    create_labs,
    grant_permission,
)
from containerlab_orchestrator.tests.test_deploy import OLD_INSPECT
from containerlab_orchestrator.tests.test_inspection import HOST_USER

CLIENT_TARGET = "containerlab_orchestrator.sync.ClabApiClient"
WRITE_TARGET = "containerlab_orchestrator.sync.write_inspect"
LOGIN_ERROR = "Host clab01: login returned no token."
INSPECT_500 = "Host clab01: GET /api/v1/labs/lab-a returned HTTP 500: inspect failed"


class SyncLabsSetupMixin(TestCase):
    """Make lab-a and lab-b of clab01 and lab-a of clab02 Deployed with a build, and the superuser sync-admin."""

    def setUp(self):
        """Create the statuses, the hosts, the labs, their builds, and the job user."""
        super().setUp()
        create_lab_statuses()
        self.hosts = create_containerlab_hosts()
        labs = create_labs(self.hosts, create_lab_devices())
        deployed = Status.objects.get(name=LabStatusChoices.DEPLOYED)
        for lab in labs:
            build_lab(lab)
        Lab.objects.filter(pk__in=[lab.pk for lab in labs]).update(status=deployed)
        self.lab_a, self.lab_b, self.lab_a_clab02 = (Lab.objects.get(pk=lab.pk) for lab in labs)
        self.sync_admin = get_user_model().objects.create_user(username="sync-admin", is_superuser=True)

    def set_status(self, lab, name):
        """Set the status of the lab row."""
        Lab.objects.filter(pk=lab.pk).update(status=Status.objects.get(name=name))

    def lab_status(self, lab):
        """Return the status name of the lab row."""
        return Lab.objects.get(pk=lab.pk).status.name


class SyncServiceTestCase(SyncLabsSetupMixin, TransactionTestCase):
    """Test the lab list, the login, the two phases, and the outcome of each lab of a sync."""

    def setUp(self):
        """Seed old node values, and mock one client for each host whose containers run."""
        super().setUp()
        self._seed_old_values()
        self.answers = {}
        for lab in (self.lab_a, self.lab_b, self.lab_a_clab02):
            self.answers[(lab.host.name, lab.name)] = [
                {
                    "owner": HOST_USER,
                    "image": row.image,
                    "nodeName": row.node_name,
                    "name": f"clab-{lab.name}-{row.node_name}",
                    "container_id": f"id-{row.node_name}",
                    "state": "running",
                }
                for row in LabNode.objects.filter(lab=lab).order_by("node_name")
            ]
        self.events = []
        self.host_mocks = {}
        patcher = mock.patch(CLIENT_TARGET, autospec=True, side_effect=self._new_client)
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)

    def _new_client(self, host):
        """Return a new mocked client for the host, whose inspect gives the answer of the lab."""
        client = mock.create_autospec(ClabApiClient, instance=True)
        client.username = HOST_USER

        def inspect(name):
            self.events.append(("read", host.name, name))
            answer = self.answers[(host.name, name)]
            if isinstance(answer, BaseException):
                raise answer
            return answer

        client.inspect.side_effect = inspect
        self.host_mocks[host.name] = client
        return client

    @staticmethod
    def _seed_old_values():
        """Set an old stored inspect result and an old state and container ID on each node row."""
        Lab.objects.update(last_inspect=OLD_INSPECT)
        LabNode.objects.update(state="old", container_id="old")

    def _run_sync(self, labs=None):
        """Run the sync on the labs, which are the labs in scope of sync-admin by default, and return the outcomes."""
        return list(sync_labs(sync_targets(self.sync_admin) if labs is None else labs))

    @staticmethod
    def _summary(outcomes):
        """Return the lab, the kind, and the line of each outcome."""
        return [(outcome.lab, outcome.kind, outcome.line) for outcome in outcomes]

    def _calls(self, host_name):
        """Return the method calls of the client of the host."""
        return self.host_mocks[host_name].method_calls

    @staticmethod
    def _line(template, lab, **values):
        """Return the text of the template for the lab and its host."""
        return template.format(lab=lab, host=lab.host, **values)

    def _failed(self, lab, error):
        """Return the summary of a failed lab with the error text."""
        return (lab, SyncKind.FAILED, self._line(SYNC_FAILED_LINE, lab, error=error))

    def _synced(self, *labs):
        """Return the summary of each lab that changed."""
        return [(lab, SyncKind.CHANGED, "") for lab in labs]

    def _login_fails_on_clab01(self, error):
        """Make the login of the clab01 client raise the error."""
        new_client = self._new_client

        def client_for(host):
            client = new_client(host)
            if host.name == "clab01":
                client.login.side_effect = error
            return client

        self.client_class.side_effect = client_for

    def test_targets_are_deployed_or_failed_labs_the_user_can_change(self):
        with self.subTest("Built and Destroyed labs are out, a Failed lab is in"):
            self.set_status(self.lab_a, LabStatusChoices.FAILED)
            self.set_status(self.lab_b, LabStatusChoices.BUILT)
            self.set_status(self.lab_a_clab02, LabStatusChoices.DESTROYED)
            self.assertEqual(sync_targets(self.sync_admin), [self.lab_a])
            Lab.objects.update(status=Status.objects.get(name=LabStatusChoices.DEPLOYED))
        users = {name: get_user_model().objects.create_user(username=name) for name in ("viewer", "changer", "both")}
        grant_permission(users["viewer"], Lab, "view")
        grant_permission(users["changer"], Lab, "change")
        grant_permission(users["both"], Lab, "view")
        grant_permission(users["both"], Lab, "change", {"name": "lab-b"})
        with self.subTest("a user with view only gets none"):
            self.assertEqual(sync_targets(users["viewer"]), [])
        with self.subTest("a user with change only gets none"):
            self.assertEqual(sync_targets(users["changer"]), [])
        with self.subTest("a change permission for one lab gives that lab"):
            self.assertEqual(sync_targets(users["both"]), [self.lab_b])
        with self.subTest("the superuser gets each lab in the order of host name, then lab name"):
            targets = sync_targets(self.sync_admin)
            self.assertIsInstance(targets, list)
            self.assertEqual(targets, [self.lab_a, self.lab_b, self.lab_a_clab02])
            self.assertEqual(targets[0].host.name, "clab01")

    def test_one_login_for_each_host_and_each_client_closes(self):
        with mock.patch(WRITE_TARGET, side_effect=self._recorded_write):
            outcomes = self._run_sync()
        self.assertEqual(self._summary(outcomes), self._synced(self.lab_a, self.lab_b, self.lab_a_clab02))
        self.assertEqual(self.client_class.call_args_list, [mock.call(self.hosts[0]), mock.call(self.hosts[1])])
        self.assertEqual(
            self._calls("clab01"),
            [mock.call.login(), mock.call.inspect("lab-a"), mock.call.inspect("lab-b"), mock.call.close()],
        )
        self.assertEqual(self._calls("clab02"), [mock.call.login(), mock.call.inspect("lab-a"), mock.call.close()])
        self.assertEqual(
            self.events,
            [
                ("read", "clab01", "lab-a"),
                ("read", "clab01", "lab-b"),
                ("write", "clab01", "lab-a"),
                ("write", "clab01", "lab-b"),
                ("read", "clab02", "lab-a"),
                ("write", "clab02", "lab-a"),
            ],
        )

    def _recorded_write(self, lab, *args, **kwargs):
        """Record the write of the lab, then write it."""
        self.events.append(("write", lab.host.name, lab.name))
        return write_inspect(lab, *args, **kwargs)

    def test_login_error_fails_each_lab_of_the_host_and_the_next_host_syncs(self):
        self._login_fails_on_clab01(ClabApiError(LOGIN_ERROR, status_code=200))
        outcomes = self._run_sync()
        self.assertEqual(
            self._summary(outcomes),
            [
                self._failed(self.lab_a, LOGIN_ERROR),
                self._failed(self.lab_b, LOGIN_ERROR),
                *self._synced(self.lab_a_clab02),
            ],
        )
        self.assertEqual(self._calls("clab01"), [mock.call.login(), mock.call.close()])
        self.assertEqual(self.lab_status(self.lab_a), LabStatusChoices.DEPLOYED)

    def test_login_error_text_is_cut_as_in_the_other_jobs(self):
        server_text = "x" * (SERVER_TEXT_LIMIT + 50)
        message = f"Host clab01: POST /login returned HTTP 500: {server_text}"
        self._login_fails_on_clab01(ClabApiError(message, status_code=500, server_message=server_text))
        outcomes = self._run_sync()
        cut = message[: -len(server_text)] + server_text[:SERVER_TEXT_LIMIT]
        self.assertEqual(self._summary(outcomes)[:2], [self._failed(self.lab_a, cut), self._failed(self.lab_b, cut)])

    def test_unexpected_login_error_fails_each_lab_of_the_host(self):
        self._login_fails_on_clab01(RuntimeError("secret text"))
        outcomes = self._run_sync()
        unexpected = [
            (lab, SyncKind.FAILED, self._line(SYNC_ERROR_LINE, lab, error_type="RuntimeError"))
            for lab in (self.lab_a, self.lab_b)
        ]
        self.assertEqual(self._summary(outcomes), [*unexpected, *self._synced(self.lab_a_clab02)])
        self.assertEqual(self._calls("clab01"), [mock.call.login(), mock.call.close()])

    def test_inspect_error_fails_one_lab_only(self):
        self.answers[("clab01", "lab-a")] = ClabApiError(INSPECT_500, status_code=500, server_message="inspect failed")
        before = Lab.objects.get(pk=self.lab_a.pk)
        outcomes = self._run_sync()
        self.assertEqual(
            self._summary(outcomes),
            [self._failed(self.lab_a, INSPECT_500), *self._synced(self.lab_b, self.lab_a_clab02)],
        )
        after = Lab.objects.get(pk=self.lab_a.pk)
        self.assertEqual(
            (after.status, after.last_inspect, after.last_updated), (before.status, OLD_INSPECT, before.last_updated)
        )

    def test_two_messages_are_one_line(self):
        with mock.patch(WRITE_TARGET, side_effect=ValidationError(["First.", "Second."])):
            outcomes = self._run_sync()
        self.assertEqual(
            self._summary(outcomes),
            [self._failed(lab, "First. Second.") for lab in (self.lab_a, self.lab_b, self.lab_a_clab02)],
        )

    def test_no_response_skips_the_rest_of_the_host(self):
        for name, flags in (("timed out", {"timed_out": True}), ("unreachable", {"unreachable": True})):
            with self.subTest(name):
                self._seed_old_values()
                self.events.clear()
                self.answers[("clab01", "lab-a")] = ClabApiError(f"Host clab01: {name}.", **flags)
                outcomes = self._run_sync()
                skipped = (self.lab_b, SyncKind.FAILED, self._line(SYNC_HOST_SKIPPED_MESSAGE, self.lab_b))
                self.assertEqual(
                    self._summary(outcomes),
                    [self._failed(self.lab_a, f"Host clab01: {name}."), skipped, *self._synced(self.lab_a_clab02)],
                )
                self.assertEqual(
                    self._calls("clab01"), [mock.call.login(), mock.call.inspect("lab-a"), mock.call.close()]
                )
        with self.subTest("an HTTP 500 does not skip the host"):
            self._seed_old_values()
            self.answers[("clab01", "lab-a")] = ClabApiError(INSPECT_500, status_code=500)
            outcomes = self._run_sync()
            self.assertEqual(
                self._summary(outcomes),
                [self._failed(self.lab_a, INSPECT_500), *self._synced(self.lab_b, self.lab_a_clab02)],
            )

    def test_lab_that_changed_after_the_list_is_skipped(self):
        changes = (
            ("set Destroyed", SYNC_CHANGED_WARNING, lambda: self.set_status(self.lab_a, LabStatusChoices.DESTROYED)),
            (
                "moved to clab03",
                SYNC_CHANGED_WARNING,
                lambda: Lab.objects.filter(pk=self.lab_a.pk).update(host=self.hosts[2]),
            ),
            ("deleted", SYNC_DELETED_WARNING, lambda: Lab.objects.filter(pk=self.lab_a.pk).delete()),
        )
        for name, template, change in changes:
            with self.subTest(name):
                self._seed_old_values()
                labs = sync_targets(self.sync_admin)
                change()
                outcomes = self._run_sync(labs)
                skipped = (self.lab_a, SyncKind.SKIPPED, self._line(template, self.lab_a))
                self.assertEqual(self._summary(outcomes), [skipped, *self._synced(self.lab_b, self.lab_a_clab02)])
                self.assertEqual(
                    self._calls("clab01"), [mock.call.login(), mock.call.inspect("lab-b"), mock.call.close()]
                )
                Lab.objects.filter(pk=self.lab_a.pk).update(
                    host=self.hosts[0], status=Status.objects.get(name=LabStatusChoices.DEPLOYED)
                )
        with self.subTest("set Failed still syncs"):
            self._seed_old_values()
            labs = sync_targets(self.sync_admin)
            self.set_status(self.lab_b, LabStatusChoices.FAILED)
            outcomes = self._run_sync(labs)
            self.assertEqual(self._summary(outcomes), self._synced(self.lab_b, self.lab_a_clab02))

    def test_bad_topology_fails_with_no_inspect_call(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(topology_yaml="")
        outcomes = self._run_sync()
        error = NO_TOPOLOGY_MESSAGE.format(lab=self.lab_a)
        self.assertEqual(
            self._summary(outcomes), [self._failed(self.lab_a, error), *self._synced(self.lab_b, self.lab_a_clab02)]
        )
        self.assertEqual(self._calls("clab01"), [mock.call.login(), mock.call.inspect("lab-b"), mock.call.close()])

    def test_unexpected_error_fails_one_lab_only(self):
        def write_breaks_on_lab_a(lab, *args, **kwargs):
            if lab.pk == self.lab_a.pk:
                raise DatabaseError("lock wait timeout with row data")
            return write_inspect(lab, *args, **kwargs)

        with mock.patch(WRITE_TARGET, side_effect=write_breaks_on_lab_a):
            outcomes = self._run_sync()
        unexpected = (self.lab_a, SyncKind.FAILED, self._line(SYNC_ERROR_LINE, self.lab_a, error_type="DatabaseError"))
        self.assertEqual(self._summary(outcomes), [unexpected, *self._synced(self.lab_b, self.lab_a_clab02)])

    def test_missing_status_raises_before_a_call(self):
        Status.objects.get(name=LabStatusChoices.DESTROYED).content_types.remove(ContentType.objects.get_for_model(Lab))
        outcomes = sync_labs(sync_targets(self.sync_admin))
        with self.assertRaises(ValidationError) as raised:
            next(outcomes)
        self.assertEqual(raised.exception.messages, [STATUS_MISSING_MESSAGE.format(status=LabStatusChoices.DESTROYED)])
        self.client_class.assert_not_called()

    def test_unchanged_and_changed_kinds(self):
        first = self._run_sync()
        self.assertEqual(self._summary(first), self._synced(self.lab_a, self.lab_b, self.lab_a_clab02))
        self.assertEqual([outcome.result.saved for outcome in first], [True, True, True])
        stored = {lab.pk: (lab.last_updated, lab.last_inspect) for lab in Lab.objects.all()}
        second = self._run_sync()
        self.assertEqual(
            self._summary(second),
            [(lab, SyncKind.UNCHANGED, "") for lab in (self.lab_a, self.lab_b, self.lab_a_clab02)],
        )
        self.assertEqual([outcome.result.saved for outcome in second], [False, False, False])
        self.assertEqual({lab.pk: (lab.last_updated, lab.last_inspect) for lab in Lab.objects.all()}, stored)
        self.answers[("clab02", "lab-a")][0]["state"] = "exited"
        third = self._run_sync()
        self.assertEqual(
            self._summary(third),
            [
                (self.lab_a, SyncKind.UNCHANGED, ""),
                (self.lab_b, SyncKind.UNCHANGED, ""),
                *self._synced(self.lab_a_clab02),
            ],
        )
        self.assertEqual(self.lab_status(self.lab_a_clab02), LabStatusChoices.FAILED)

    def test_time_limit_closes_the_client_and_raises(self):
        with self.subTest("in the inspect call"):
            self.answers[("clab01", "lab-a")] = SoftTimeLimitExceeded()
            with self.assertRaises(SoftTimeLimitExceeded):
                self._run_sync()
            self.assertEqual(self._calls("clab01"), [mock.call.login(), mock.call.inspect("lab-a"), mock.call.close()])
            self.assertNotIn("clab02", self.host_mocks)
        with self.subTest("in the write"):
            self.host_mocks.clear()
            self.answers[("clab01", "lab-a")] = []
            with (
                mock.patch(WRITE_TARGET, side_effect=SoftTimeLimitExceeded()),
                self.assertRaises(SoftTimeLimitExceeded),
            ):
                self._run_sync()
            self.assertEqual(
                self._calls("clab01"),
                [mock.call.login(), mock.call.inspect("lab-a"), mock.call.inspect("lab-b"), mock.call.close()],
            )
            self.assertNotIn("clab02", self.host_mocks)

    def test_new_messages_are_not_sanitized(self):
        lab = self.lab_a
        texts = (
            self._line(SYNC_FAILED_LINE, lab, error=INSPECT_500),
            self._line(SYNC_ERROR_LINE, lab, error_type="OperationalError"),
            self._line(SYNC_HOST_SKIPPED_MESSAGE, lab),
            self._line(SYNC_CHANGED_WARNING, lab),
            self._line(SYNC_DELETED_WARNING, lab),
            self._line(SYNC_TIME_LIMIT_MESSAGE, lab),
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(sanitize(text), text)
