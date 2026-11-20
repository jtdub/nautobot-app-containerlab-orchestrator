"""Tests for the Sync Labs job."""

import os
from unittest import mock

import requests
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.choices import JobResultStatusChoices, LogLevelChoices, SecretsGroupSecretTypeChoices
from nautobot.apps.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.extras.models import Job, ObjectChange, Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.deploy import STATUS_MISSING_MESSAGE
from containerlab_orchestrator.inspection import NOT_RUNNING_WARNING
from containerlab_orchestrator.jobs import SYNC_CHANGED_SUMMARY, SYNC_SUMMARY, SYNC_UNCHANGED_SUMMARY, SyncLabs
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.sync import (
    SYNC_BUSY_WARNING,
    SYNC_CHANGED_WARNING,
    SYNC_DELETED_WARNING,
    SYNC_FAILED_LINE,
    SYNC_HOST_ALL_GONE_MESSAGE,
    SYNC_HOST_SKIPPED_MESSAGE,
    SYNC_TIME_LIMIT_MESSAGE,
)
from containerlab_orchestrator.tests.fixtures import grant_permission, secret_variable_name
from containerlab_orchestrator.tests.test_clab_client import fake_response, login_response
from containerlab_orchestrator.tests.test_deploy import OLD_INSPECT
from containerlab_orchestrator.tests.test_jobs import JOB_MODULE
from containerlab_orchestrator.tests.test_sync import CLIENT_TARGET, LOGIN_ERROR, SyncClientsMixin


class SyncLabsJobTestCase(SyncClientsMixin, TransactionTestCase):
    """Run the Sync Labs job on lab-a and lab-b of clab01 and lab-a of clab02, with one mocked client for each host."""

    @staticmethod
    def run_sync(username="sync-admin"):
        """Run the job as the user, and return the job result."""
        return create_job_result_and_run_job(JOB_MODULE, "SyncLabs", username=username)

    @staticmethod
    def job_lines(job_result):
        """Return the level, the message, and the log object of each line that the job wrote, in order."""
        entries = job_result.job_log_entries.exclude(grouping__in=("initialization", "post_run")).order_by("created")
        return list(entries.values_list("log_level", "message", "log_object"))

    @staticmethod
    def synced(lab, template=SYNC_CHANGED_SUMMARY, running=None, warnings=0, status=LabStatusChoices.DEPLOYED):
        """Return the success line of a lab that synced, with each node of the lab running by default."""
        nodes = LabNode.objects.filter(lab=lab).count()
        text = template.format(
            lab=lab,
            host=lab.host,
            nodes=nodes,
            running=nodes if running is None else running,
            warnings=warnings,
            status=status,
        )
        return (LogLevelChoices.LOG_SUCCESS, text, str(lab))

    def failed(self, lab, template, **values):
        """Return the failure line of the lab."""
        return (LogLevelChoices.LOG_FAILURE, self._line(template, lab, **values), str(lab))

    def skipped(self, lab, template):
        """Return the skip warning of the lab."""
        return (LogLevelChoices.LOG_WARNING, self._line(template, lab), str(lab))

    @staticmethod
    def summary(labs=3, changed=0, unchanged=0, skipped=0, failed=0):
        """Return the summary line and the result of a sync with the counts."""
        counts = {"changed": changed, "unchanged": unchanged, "skipped": skipped, "failed": failed}
        return (LogLevelChoices.LOG_INFO, SYNC_SUMMARY.format(labs=labs, **counts), ""), {"labs": labs, **counts}

    def assert_sync(self, job_result, lines, counts, status=JobResultStatusChoices.STATUS_SUCCESS):
        """Check the status, the job lines with the summary line at the end, and the result counts of the job."""
        summary_line, result = self.summary(**counts)
        self.assertJobResultStatus(job_result, status)
        self.assertEqual(self.job_lines(job_result), [*lines, summary_line])
        self.assertEqual(job_result.result, result)

    def assert_time_limit(self, job_result, lines):
        """Check that the time limit failed the job with the job lines, no summary, and the error in the traceback."""
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(self.job_lines(job_result), lines)
        self.assertIn(SoftTimeLimitExceeded.__name__, job_result.traceback or "")

    def test_sync_job_is_registered_with_time_limits(self):
        job_model = Job.objects.get(module_name=JOB_MODULE, job_class_name="SyncLabs")
        self.assertEqual(job_model.name, "Sync Labs")
        self.assertEqual(job_model.grouping, "Containerlab Orchestrator")
        self.assertEqual(
            job_model.description,
            "Inspect each Deployed or Failed lab that you can view and change on its containerlab host. Schedule the "
            "job to keep the lab status current. The job changes nothing on the host.",
        )
        self.assertEqual((job_model.soft_time_limit, job_model.time_limit), (3500, 3600))
        self.assertFalse(job_model.has_sensitive_variables)
        self.assertFalse(job_model.enabled)
        self.assertTrue(job_model.is_singleton)
        self.assertTrue(SyncLabs.is_singleton)
        self.assertEqual(list(SyncLabs._get_vars()), [])  # pylint: disable=protected-access

    def test_two_hosts_one_fails_logs_one_line_for_each_lab(self):
        self.login_errors["clab01"] = ClabApiError(LOGIN_ERROR)
        job_result = self.run_sync()
        lines = [
            self.failed(self.lab_a, SYNC_FAILED_LINE, error=LOGIN_ERROR),
            self.failed(self.lab_b, SYNC_FAILED_LINE, error=LOGIN_ERROR),
            self.synced(self.lab_a_clab02),
        ]
        self.assert_sync(job_result, lines, {"changed": 1, "failed": 2}, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(self._calls("clab01"), [mock.call.login(), mock.call.close()])
        self.assertEqual(self.lab_status(self.lab_a_clab02), LabStatusChoices.DEPLOYED)

    def test_all_synced_ends_completed_with_summary(self):
        labs = (self.lab_a, self.lab_b, self.lab_a_clab02)
        first = self.run_sync()
        self.assert_sync(first, [self.synced(lab) for lab in labs], {"changed": 3})
        second = self.run_sync()
        self.assert_sync(second, [self.synced(lab, SYNC_UNCHANGED_SUMMARY) for lab in labs], {"unchanged": 3})

    def test_no_change_adds_no_change_log_entry(self):
        labs = (self.lab_a, self.lab_b, self.lab_a_clab02)

        def change_counts():
            return [ObjectChange.objects.filter(changed_object_id=lab.pk).count() for lab in labs]

        for answer in self.answers.values():
            for container in answer:
                container["status"] = "Up 1 hours"
        before = change_counts()
        self.assertJobResultStatus(self.run_sync())
        after_first = change_counts()
        self.assertEqual([count - old for count, old in zip(after_first, before)], [1, 1, 1])
        stored = list(Lab.objects.order_by("pk").values_list("last_updated", "last_inspect"))
        for answer in self.answers.values():
            for container in answer:
                container["status"] = "Up 2 hours"
        self.assertJobResultStatus(self.run_sync())
        self.assertEqual(change_counts(), after_first)
        self.assertEqual(list(Lab.objects.order_by("pk").values_list("last_updated", "last_inspect")), stored)
        self.assertEqual({item["status"] for (_, value) in stored for item in value}, {"Up 1 hours"})

    def test_skip_lines_are_in_the_stored_log(self):
        with self.subTest("a busy lab is a warning, and the run ends Completed"):
            self._lab_job_result({"lab": str(self.lab_a.pk)})
            job_result = self.run_sync()
            lines = [
                self.skipped(self.lab_a, SYNC_BUSY_WARNING),
                self.synced(self.lab_b),
                self.synced(self.lab_a_clab02),
            ]
            self.assert_sync(job_result, lines, {"changed": 2, "skipped": 1})
        with self.subTest("a lab that changed or is gone after the list"):
            self._reset()

            def change_the_other_labs():
                self.set_status(self.lab_b, LabStatusChoices.DESTROYED)
                Lab.objects.filter(pk=self.lab_a_clab02.pk).delete()
                return self.listed[("clab01", "lab-a")]

            self.answers[("clab01", "lab-a")] = change_the_other_labs
            job_result = self.run_sync()
            lines = [
                self.skipped(self.lab_b, SYNC_CHANGED_WARNING),
                self.synced(self.lab_a),
                self.skipped(self.lab_a_clab02, SYNC_DELETED_WARNING),
            ]
            self.assert_sync(job_result, lines, {"changed": 1, "skipped": 2})

    def test_host_skip_lines_are_in_the_stored_log(self):
        with self.subTest("a host with no response"):
            self.answers[("clab01", "lab-a")] = ClabApiError("Host clab01: timed out.", timed_out=True)
            job_result = self.run_sync()
            lines = [
                self.failed(self.lab_a, SYNC_FAILED_LINE, error="Host clab01: timed out."),
                self.failed(self.lab_b, SYNC_HOST_SKIPPED_MESSAGE),
                self.synced(self.lab_a_clab02),
            ]
            self.assert_sync(job_result, lines, {"changed": 1, "failed": 2}, JobResultStatusChoices.STATUS_FAILURE)
        with self.subTest("a host that reports no lab for each lab"):
            self._reset()
            for lab in (self.lab_a, self.lab_b):
                self.answers[("clab01", lab.name)] = self._not_found("clab01", lab.name)
            job_result = self.run_sync()
            lines = [
                self.failed(self.lab_a, SYNC_HOST_ALL_GONE_MESSAGE, count=2),
                self.failed(self.lab_b, SYNC_HOST_ALL_GONE_MESSAGE, count=2),
                self.synced(self.lab_a_clab02),
            ]
            self.assert_sync(job_result, lines, {"changed": 1, "failed": 2}, JobResultStatusChoices.STATUS_FAILURE)
            self.assertEqual(self.lab_status(self.lab_a), LabStatusChoices.DEPLOYED)

    def test_no_lab_in_scope_ends_completed(self):
        Lab.objects.update(status=Status.objects.get(name=LabStatusChoices.BUILT))
        self.assert_sync(self.run_sync(), [], {"labs": 0})
        self.client_class.assert_not_called()

    def test_inspect_warnings_come_before_the_success_line(self):
        container = self.answers[("clab02", "lab-a")][0]
        container["state"] = "exited"
        job_result = self.run_sync()
        warning = NOT_RUNNING_WARNING.format(lab=self.lab_a_clab02, nodes=container["nodeName"])
        lines = [
            self.synced(self.lab_a),
            self.synced(self.lab_b),
            (LogLevelChoices.LOG_WARNING, warning, str(self.lab_a_clab02)),
            self.synced(self.lab_a_clab02, running=0, warnings=1, status=LabStatusChoices.FAILED),
        ]
        self.assert_sync(job_result, lines, {"changed": 3})

    def test_user_who_cannot_change_a_lab_gets_no_line_for_it(self):
        bob = get_user_model().objects.get(username="bob")
        grant_permission(bob, Lab, "view")
        grant_permission(bob, Lab, "change", {"name": "lab-b"})
        job_result = self.run_sync("bob")
        self.assert_sync(job_result, [self.synced(self.lab_b)], {"labs": 1, "changed": 1})
        self.assertEqual(self.client_class.call_args_list, [mock.call(self.hosts[0])])
        self.assertEqual(self._calls("clab01"), [mock.call.login(), mock.call.inspect("lab-b"), mock.call.close()])

    def test_time_limit_logs_the_labs_with_no_result_line(self):
        labs = (self.lab_a, self.lab_b, self.lab_a_clab02)
        with self.subTest("the inspect of the last lab"):
            self.answers[("clab02", "lab-a")] = SoftTimeLimitExceeded()
            job_result = self.run_sync()
            lines = [
                self.synced(self.lab_a),
                self.synced(self.lab_b),
                self.failed(self.lab_a_clab02, SYNC_TIME_LIMIT_MESSAGE),
            ]
            self.assert_time_limit(job_result, lines)
            self.assertEqual(self._calls("clab02"), [mock.call.login(), mock.call.inspect("lab-a"), mock.call.close()])
        with self.subTest("a read lab that the job did not write"):
            self._reset()
            self.host_mocks.clear()
            self.answers[("clab01", "lab-b")] = SoftTimeLimitExceeded()
            job_result = self.run_sync()
            self.assert_time_limit(job_result, [self.failed(lab, SYNC_TIME_LIMIT_MESSAGE) for lab in labs])
            self.assertEqual(Lab.objects.get(pk=self.lab_a.pk).last_inspect, OLD_INSPECT)
            self.assertEqual(self._calls("clab01")[-1], mock.call.close())
            self.assertNotIn("clab02", self.host_mocks)
        with self.subTest("the close of the last client, after each result line"):
            self._reset()

            def client_whose_close_stops(host):
                client = self._new_client(host)
                if host.name == "clab02":
                    client.close.side_effect = SoftTimeLimitExceeded()
                return client

            self.client_class.side_effect = client_whose_close_stops
            job_result = self.run_sync()
            self.assert_time_limit(job_result, [self.synced(lab) for lab in labs])
            self.assertNotIn(IndexError.__name__, job_result.traceback or "")

    def test_missing_status_fails_the_job(self):
        Status.objects.get(name=LabStatusChoices.DESTROYED).content_types.remove(ContentType.objects.get_for_model(Lab))
        job_result = self.run_sync()
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        message = STATUS_MISSING_MESSAGE.format(status=LabStatusChoices.DESTROYED)
        self.assertEqual(self.job_lines(job_result), [(LogLevelChoices.LOG_FAILURE, message, "")])
        self.assertIsNone(job_result.result)
        self.client_class.assert_not_called()

    def test_job_log_and_result_hold_no_secret(self):
        marker = {"user": "MARKER-103-user", "password": "MARKER-103-password", "token": "MARKER-103-token"}
        containers = {
            name: [{**item, "owner": marker["user"]} for item in self.listed[("clab01", name)]]
            for name in ("lab-a", "lab-b")
        }

        def send(_adapter, request, **_kwargs):
            if request.url.endswith("/login"):
                return login_response(marker["token"])
            if request.url.startswith("https://clab02"):
                return fake_response(500, {"error": "inspect failed"})
            return fake_response(200, containers[request.url.rsplit("/", 1)[-1]])

        variables = {
            secret_variable_name(SecretsGroupSecretTypeChoices.TYPE_USERNAME): marker["user"],
            secret_variable_name(SecretsGroupSecretTypeChoices.TYPE_PASSWORD): marker["password"],
        }
        with (
            mock.patch(CLIENT_TARGET, ClabApiClient),
            mock.patch.dict(os.environ, variables),
            mock.patch.object(requests.adapters.HTTPAdapter, "send", autospec=True, side_effect=send) as sent,
        ):
            job_result = self.run_sync()
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(job_result.result, self.summary(changed=2, failed=1)[1])
        requests_sent = [call.args[1] for call in sent.call_args_list]
        self.assertIn(marker["password"], requests_sent[0].body.decode())
        self.assertEqual(requests_sent[1].headers["Authorization"], f"Bearer {marker['token']}")
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("MARKER-103", entry.message)
                self.assertNotIn("MARKER-103", entry.log_object)
        self.assertNotIn("MARKER-103", str(job_result.result))
