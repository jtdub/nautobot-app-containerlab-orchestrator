"""Tests for the sync service that inspects each lab in scope on its host."""

from datetime import timedelta
from unittest import TestCase, mock
from uuid import uuid4

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.utils import timezone
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.apps.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.core.utils.logging import sanitize
from nautobot.extras.context_managers import web_request_context
from nautobot.extras.models import Job, JobResult, ObjectChange, Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.deploy import NO_TOPOLOGY_MESSAGE, SERVER_TEXT_LIMIT, STATUS_MISSING_MESSAGE
from containerlab_orchestrator.destroy import NOT_OWNED_NOT_FOUND, PLAIN_NOT_FOUND
from containerlab_orchestrator.inspection import (
    HELPERS_NOT_FOUND,
    HELPERS_WARNING,
    INSPECT_GUARD_MESSAGE,
    INSPECT_NOT_OWNED_MESSAGE,
    NOT_RUNNING_WARNING,
    write_inspect,
)
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.sync import (
    SYNC_BUSY_WARNING,
    SYNC_CHANGED_WARNING,
    SYNC_DELETED_WARNING,
    SYNC_ERROR_LINE,
    SYNC_FAILED_LINE,
    SYNC_HOST_ALL_GONE_MESSAGE,
    SYNC_HOST_SKIPPED_MESSAGE,
    SYNC_TIME_LIMIT_MESSAGE,
    SyncKind,
    _is_busy,
    _job_lab,
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
INSPECT_CLIENT_TARGET = "containerlab_orchestrator.inspection.ClabApiClient"
JOB_MODULE = "containerlab_orchestrator.jobs"
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


class SyncClientsMixin(SyncLabsSetupMixin):
    """Add one mocked client for each host, the answers of each lab, and the helpers of the sync tests."""

    def setUp(self):
        """Seed old node values, and mock one client for each host whose containers run."""
        super().setUp()
        self._seed_old_values()
        self.answers = {}
        self.login_errors = {}
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
        self.listed = dict(self.answers)

    def _new_client(self, host):
        """Return a new mocked client for the host, whose inspect gives the answer of the lab."""
        client = mock.create_autospec(ClabApiClient, instance=True)
        client.username = HOST_USER
        client.login.side_effect = self.login_errors.get(host.name)

        def inspect(name):
            self.events.append(("read", host.name, name))
            answer = self.answers[(host.name, name)]
            if isinstance(answer, BaseException):
                raise answer
            return answer() if callable(answer) else answer

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

    def _synced(self, *labs, kind=SyncKind.CHANGED):
        """Return the summary of each lab that synced with the kind."""
        return [(lab, kind, "") for lab in labs]

    def _unexpected(self, lab, error_type):
        """Return the summary of a lab that failed with an unexpected error of the type."""
        return (lab, SyncKind.FAILED, self._line(SYNC_ERROR_LINE, lab, error_type=error_type))

    @staticmethod
    def _lab_job_result(task_kwargs, age=None, **fields):
        """Save a result of the Inspect Lab job with the kwargs, STARTED and new unless the arguments say other."""
        values = {
            "job_model": Job.objects.get(module_name=JOB_MODULE, job_class_name="InspectLab"),
            "name": "Inspect Lab",
            "status": JobResultStatusChoices.STATUS_STARTED,
            **fields,
        }
        result = JobResult.objects.create(task_kwargs=task_kwargs, **values)
        if age is not None:
            JobResult.objects.filter(pk=result.pk).update(date_created=timezone.now() - age)
        return result

    def _busy(self, lab):
        """Return the summary of a lab that the sync skipped because a lab job of the lab runs."""
        return (lab, SyncKind.SKIPPED, self._line(SYNC_BUSY_WARNING, lab))

    @staticmethod
    def _not_found(host_name, name, template=PLAIN_NOT_FOUND):
        """Return the client error of a host that gives a 404 with the server text of the template."""
        return ClabApiError(
            f"Host {host_name}: GET /api/v1/labs/{name} returned HTTP 404: not found",
            status_code=404,
            server_message=template.format(lab=name),
        )

    def _reset(self):
        """Set each lab Deployed with old values, give each lab its containers, and delete each job result."""
        Lab.objects.update(status=Status.objects.get(name=LabStatusChoices.DEPLOYED))
        self._seed_old_values()
        self.answers.update(self.listed)
        JobResult.objects.all().delete()


class SyncServiceTestCase(SyncClientsMixin, TransactionTestCase):
    """Test the lab list, the login, the two phases, and the outcome of each lab of a sync."""

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
        self.login_errors["clab01"] = ClabApiError(LOGIN_ERROR, status_code=200)
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
        self.login_errors["clab01"] = ClabApiError(message, status_code=500, server_message=server_text)
        outcomes = self._run_sync()
        cut = message[: -len(server_text)] + server_text[:SERVER_TEXT_LIMIT]
        self.assertEqual(self._summary(outcomes)[:2], [self._failed(self.lab_a, cut), self._failed(self.lab_b, cut)])

    def test_unexpected_login_error_fails_each_lab_of_the_host(self):
        self.login_errors["clab01"] = RuntimeError("secret text")
        outcomes = self._run_sync()
        unexpected = [self._unexpected(lab, "RuntimeError") for lab in (self.lab_a, self.lab_b)]
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

    def test_foreign_result_fails_one_lab_only(self):
        listed = self.answers[("clab01", "lab-a")]
        foreign = [{**listed[0], "owner": "someone-else"}, *listed[1:]]
        not_owned = ClabApiError(
            "Host clab01: GET /api/v1/labs/lab-a returned HTTP 404: not owned",
            status_code=404,
            server_message=NOT_OWNED_NOT_FOUND.format(lab="lab-a"),
        )
        error = INSPECT_NOT_OWNED_MESSAGE.format(host=self.lab_a.host, lab=self.lab_a)
        for name, answer in (("a container of another owner", foreign), ("a not-owned 404", not_owned)):
            with self.subTest(name):
                self._seed_old_values()
                self.answers[("clab01", "lab-a")] = answer
                before = Lab.objects.get(pk=self.lab_a.pk)
                outcomes = self._run_sync()
                self.assertEqual(
                    self._summary(outcomes),
                    [self._failed(self.lab_a, error), *self._synced(self.lab_b, self.lab_a_clab02)],
                )
                after = Lab.objects.get(pk=self.lab_a.pk)
                self.assertEqual(
                    (after.status, after.last_inspect, after.last_updated),
                    (before.status, OLD_INSPECT, before.last_updated),
                )

    def test_lab_that_changed_between_read_and_write_fails(self):
        listed = self.answers[("clab01", "lab-b")]

        def change_lab_a_then_list():
            Lab.objects.filter(pk=self.lab_a.pk).update(last_updated=timezone.now())
            return listed

        self.answers[("clab01", "lab-b")] = change_lab_a_then_list
        outcomes = self._run_sync()
        guard = INSPECT_GUARD_MESSAGE.format(lab=self.lab_a)
        self.assertEqual(
            self._summary(outcomes), [self._failed(self.lab_a, guard), *self._synced(self.lab_b, self.lab_a_clab02)]
        )
        self.assertEqual(Lab.objects.get(pk=self.lab_a.pk).last_inspect, OLD_INSPECT)

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
        for name, message, status_code in (
            ("an HTTP 500 does not skip the host", INSPECT_500, 500),
            ("an error with no status and no flag does not skip the host", "Host clab01: invalid lab name.", None),
        ):
            with self.subTest(name):
                self._seed_old_values()
                self.host_mocks.clear()
                self.answers[("clab01", "lab-a")] = ClabApiError(message, status_code=status_code)
                outcomes = self._run_sync()
                self.assertEqual(
                    self._summary(outcomes),
                    [self._failed(self.lab_a, message), *self._synced(self.lab_b, self.lab_a_clab02)],
                )
                self.assertEqual(
                    self._calls("clab01"),
                    [mock.call.login(), mock.call.inspect("lab-a"), mock.call.inspect("lab-b"), mock.call.close()],
                )

    def test_busy_lab_is_skipped(self):
        lab_id = str(self.lab_a.pk)
        model_value = {"id": lab_id, "__nautobot_type__": "containerlab_orchestrator.models.Lab", "display": "lab-a"}
        cases = (
            ("a text", {"lab": lab_id}, {}),
            ("a UUID", {"lab": self.lab_a.pk}, {}),
            ("a model instance", {"lab": model_value}, {}),
            ("a PENDING result", {"lab": lab_id}, {"status": JobResultStatusChoices.STATUS_PENDING}),
            ("a result of 5 hours and 59 minutes", {"lab": lab_id}, {"age": timedelta(hours=5, minutes=59)}),
        )
        for name, task_kwargs, fields in cases:
            with self.subTest(name):
                self._reset()
                self._lab_job_result(task_kwargs, **fields)
                outcomes = self._run_sync()
                self.assertEqual(
                    self._summary(outcomes), [self._busy(self.lab_a), *self._synced(self.lab_b, self.lab_a_clab02)]
                )
                self.assertEqual(
                    self._calls("clab01"), [mock.call.login(), mock.call.inspect("lab-b"), mock.call.close()]
                )
                self.assertEqual(Lab.objects.get(pk=self.lab_a.pk).last_inspect, OLD_INSPECT)

    def test_real_lab_job_result_makes_the_lab_busy(self):
        with mock.patch(INSPECT_CLIENT_TARGET, autospec=True):
            job_result = create_job_result_and_run_job(
                JOB_MODULE, "InspectLab", username="sync-admin", lab=self.lab_a.pk
            )
        JobResult.objects.filter(pk=job_result.pk).update(status=JobResultStatusChoices.STATUS_STARTED)
        self.assertEqual(JobResult.objects.get(pk=job_result.pk).task_kwargs, {"lab": str(self.lab_a.pk)})
        self.assertTrue(_is_busy(self.lab_a))
        self.assertFalse(_is_busy(self.lab_b))
        lab_id = str(self.lab_a.pk)
        self.assertEqual([_job_lab({"lab": self.lab_a.pk}), _job_lab({"lab": {"id": self.lab_a.pk}})], [lab_id, lab_id])

    def test_results_that_do_not_make_a_lab_busy(self):
        lab_id = str(self.lab_a.pk)
        other_job = Job.objects.exclude(module_name=JOB_MODULE).first()
        self.assertIsNotNone(other_job)
        cases = (
            ("older than 6 hours", {"lab": lab_id}, {"age": timedelta(hours=6, minutes=1)}),
            ("status SUCCESS", {"lab": lab_id}, {"status": JobResultStatusChoices.STATUS_SUCCESS}),
            ("another lab", {"lab": str(uuid4())}, {}),
            ("a job of another module", {"lab": lab_id}, {"job_model": other_job}),
            ("no lab input", {}, {}),
            ("kwargs that are not a dict", [lab_id], {}),
        )
        for name, task_kwargs, fields in cases:
            with self.subTest(name):
                self._reset()
                self._lab_job_result(task_kwargs, **fields)
                outcomes = self._run_sync()
                self.assertEqual(self._summary(outcomes), self._synced(self.lab_a, self.lab_b, self.lab_a_clab02))
                self.assertEqual(
                    self._calls("clab01"),
                    [mock.call.login(), mock.call.inspect("lab-a"), mock.call.inspect("lab-b"), mock.call.close()],
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
        self.assertEqual(
            self._summary(outcomes),
            [self._unexpected(self.lab_a, "DatabaseError"), *self._synced(self.lab_b, self.lab_a_clab02)],
        )

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
            self._synced(self.lab_a, self.lab_b, self.lab_a_clab02, kind=SyncKind.UNCHANGED),
        )
        self.assertEqual([outcome.result.saved for outcome in second], [False, False, False])
        self.assertEqual({lab.pk: (lab.last_updated, lab.last_inspect) for lab in Lab.objects.all()}, stored)
        self.answers[("clab02", "lab-a")][0]["state"] = "exited"
        third = self._run_sync()
        self.assertEqual(
            self._summary(third),
            [
                *self._synced(self.lab_a, self.lab_b, kind=SyncKind.UNCHANGED),
                *self._synced(self.lab_a_clab02),
            ],
        )
        self.assertEqual(self.lab_status(self.lab_a_clab02), LabStatusChoices.FAILED)
        node_name = self.answers[("clab02", "lab-a")][0]["nodeName"]
        self.assertEqual(third[2].result.warnings, [NOT_RUNNING_WARNING.format(lab=self.lab_a_clab02, nodes=node_name)])
        self.assertEqual([outcome.result.warnings for outcome in third[:2]], [[], []])
        self.answers[("clab02", "lab-a")] = ClabApiError(
            "Host clab02: GET /api/v1/labs/lab-a returned HTTP 404: helpers only",
            status_code=404,
            server_message=HELPERS_NOT_FOUND.format(lab="lab-a"),
        )
        fourth = self._run_sync()
        self.assertEqual(
            fourth[2].result.warnings, [HELPERS_WARNING.format(host=self.lab_a_clab02.host, lab=self.lab_a_clab02)]
        )

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
            self._line(SYNC_BUSY_WARNING, lab),
            self._line(SYNC_HOST_ALL_GONE_MESSAGE, lab, count=2),
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(sanitize(text), text)


class SyncBreakerTestCase(SyncClientsMixin, TransactionTestCase):
    """Test the host breaker, which writes no lab of a host that reports no lab for each lab that the job read."""

    def _all_gone(self, lab, count):
        """Return the summary of a lab that the breaker did not write."""
        return (lab, SyncKind.FAILED, self._line(SYNC_HOST_ALL_GONE_MESSAGE, lab, count=count))

    def _gone(self, *labs):
        """Make the host of each lab report no lab of its name."""
        for lab in labs:
            self.answers[(lab.host.name, lab.name)] = self._not_found(lab.host.name, lab.name)

    @staticmethod
    def _stored(*labs):
        """Return the status, the stored result, the last update time, and the node rows of each lab row."""
        return [
            (
                lab.status.name,
                lab.last_inspect,
                lab.last_updated,
                list(
                    lab.lab_nodes.order_by("node_name").values_list("node_name", "state", "container_id", "mgmt_ipv4")
                ),
            )
            for lab in Lab.objects.filter(pk__in=[lab.pk for lab in labs]).order_by("name")
        ]

    @staticmethod
    def _change_counts(*labs):
        """Return the count of change log entries of each lab."""
        return [ObjectChange.objects.filter(changed_object_id=lab.pk).count() for lab in labs]

    def test_host_breaker_writes_nothing_when_each_lab_is_gone(self):
        labs = (self.lab_a, self.lab_b, self.lab_a_clab02)
        cases = (("two plain 404 answers", self._not_found("clab01", "lab-b")), ("a plain 404 and an empty list", []))
        for name, lab_b_answer in cases:
            with self.subTest(name):
                self._reset()
                self._gone(self.lab_a)
                self.answers[("clab01", "lab-b")] = lab_b_answer
                before = self._stored(self.lab_a, self.lab_b)
                counts = self._change_counts(*labs)
                with web_request_context(self.sync_admin):
                    outcomes = self._run_sync()
                self.assertEqual(
                    self._summary(outcomes),
                    [self._all_gone(self.lab_a, 2), self._all_gone(self.lab_b, 2), *self._synced(self.lab_a_clab02)],
                )
                self.assertEqual(self._stored(self.lab_a, self.lab_b), before)
                self.assertEqual(before[0][0], LabStatusChoices.DEPLOYED)
                self.assertEqual(self._change_counts(*labs), [*counts[:2], counts[2] + 1])
                self.assertEqual(
                    self._calls("clab01"),
                    [mock.call.login(), mock.call.inspect("lab-a"), mock.call.inspect("lab-b"), mock.call.close()],
                )

    def test_host_breaker_needs_two_gone_labs(self):
        with self.subTest("the one lab of a host is gone"):
            self._reset()
            self._gone(self.lab_a_clab02)
            outcomes = self._run_sync()
            self.assertEqual(self._summary(outcomes), self._synced(self.lab_a, self.lab_b, self.lab_a_clab02))
            self.assertEqual(self.lab_status(self.lab_a_clab02), LabStatusChoices.DESTROYED)
        with self.subTest("a gone lab and a listed lab"):
            self._reset()
            self._gone(self.lab_a)
            outcomes = self._run_sync()
            self.assertEqual(self._summary(outcomes), self._synced(self.lab_a, self.lab_b, self.lab_a_clab02))
            self.assertEqual(
                [self.lab_status(self.lab_a), self.lab_status(self.lab_b)],
                [LabStatusChoices.DESTROYED, LabStatusChoices.DEPLOYED],
            )
        with self.subTest("a lab with helper containers and a gone lab"):
            self._reset()
            self._gone(self.lab_b)
            self.answers[("clab01", "lab-a")] = self._not_found("clab01", "lab-a", HELPERS_NOT_FOUND)
            outcomes = self._run_sync()
            self.assertEqual(self._summary(outcomes), self._synced(self.lab_a, self.lab_b, self.lab_a_clab02))
            self.assertEqual(
                [self.lab_status(self.lab_a), self.lab_status(self.lab_b)],
                [LabStatusChoices.FAILED, LabStatusChoices.DESTROYED],
            )

    def test_host_breaker_counts_only_read_labs(self):
        with self.subTest("a busy lab and a gone lab"):
            self._reset()
            self._lab_job_result({"lab": str(self.lab_a.pk)})
            self._gone(self.lab_b)
            outcomes = self._run_sync()
            self.assertEqual(
                self._summary(outcomes), [self._busy(self.lab_a), *self._synced(self.lab_b, self.lab_a_clab02)]
            )
            self.assertEqual(
                [self.lab_status(self.lab_a), self.lab_status(self.lab_b)],
                [LabStatusChoices.DEPLOYED, LabStatusChoices.DESTROYED],
            )
        with self.subTest("a failed lab and a gone lab"):
            self._reset()
            self.answers[("clab01", "lab-a")] = ClabApiError(INSPECT_500, status_code=500)
            self._gone(self.lab_b)
            outcomes = self._run_sync()
            self.assertEqual(
                self._summary(outcomes),
                [self._failed(self.lab_a, INSPECT_500), *self._synced(self.lab_b, self.lab_a_clab02)],
            )
            self.assertEqual(self.lab_status(self.lab_b), LabStatusChoices.DESTROYED)
        with self.subTest("a busy lab and two gone labs give a count of 2"):
            self._reset()
            Lab.objects.filter(pk=self.lab_a_clab02.pk).update(host=self.hosts[0], name="lab-c")
            lab_c = Lab.objects.get(pk=self.lab_a_clab02.pk)
            build_lab(lab_c)
            self.set_status(lab_c, LabStatusChoices.DEPLOYED)
            self._lab_job_result({"lab": str(self.lab_a.pk)})
            self._gone(self.lab_b, lab_c)
            outcomes = self._run_sync()
            self.assertEqual(
                self._summary(outcomes),
                [self._busy(self.lab_a), self._all_gone(self.lab_b, 2), self._all_gone(lab_c, 2)],
            )
            self.assertEqual(self.lab_status(lab_c), LabStatusChoices.DEPLOYED)
