"""Tests for the Build Lab Topology, Deploy Lab, Destroy Lab, and Redeploy Lab jobs."""

from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.test import override_settings
from nautobot.apps.choices import JobResultStatusChoices, LogLevelChoices
from nautobot.apps.testing import TransactionTestCase, create_job_result_and_run_job
from nautobot.dcim.models import Device
from nautobot.extras.models import Job, Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.deploy import (
    INSPECT_WARNING,
    MISSING_IMAGES_MESSAGE,
    TIME_LIMIT_MESSAGE,
    DeployTimeLimit,
)
from containerlab_orchestrator.destroy import (
    DESTROY_BUSY_MESSAGE,
    DESTROY_TIME_LIMIT_MESSAGE,
    NOT_FOUND_WARNING,
    DestroyTimeLimit,
)
from containerlab_orchestrator.jobs import (
    DEPLOY_SUMMARY,
    DESTROY_SUMMARY,
    KEEP_DIRECTORY_HELP,
    NO_CHANGE_MESSAGE,
    NO_VIEW_MESSAGE,
    NOT_OWNER_MESSAGE,
    REDEPLOY_SUMMARY,
    REPLACE_HELP,
    SUMMARY_MESSAGE,
    DeployLab,
    DestroyLab,
    RedeployLab,
)
from containerlab_orchestrator.models import Lab, LabNode, NodeMapping
from containerlab_orchestrator.redeploy import HALF_DONE_MESSAGE
from containerlab_orchestrator.selection import EMPTY_SELECTION_MESSAGE, HIDDEN_DEVICES_MESSAGE
from containerlab_orchestrator.tests.fixtures import (
    create_containerlab_hosts,
    create_lab_devices,
    create_lab_statuses,
    create_labs,
    create_node_mappings,
    grant_permission,
)
from containerlab_orchestrator.tests.test_app_config import DEFAULT_SETTINGS
from containerlab_orchestrator.tests.test_topology import TopologyTestCase
from containerlab_orchestrator.topology import CABLE_WARNING, W_MGMT_ONLY

JOB_MODULE = "containerlab_orchestrator.jobs"
JOB_CLASS = "BuildLabTopology"


class BuildLabTopologyJobTestCase(TransactionTestCase):
    """Run the Build Lab Topology job.

    The job writes its log through a second database connection, so these tests commit their data.
    """

    def setUp(self):
        """Create the lab statuses, the hosts, the devices, the labs, and the permissions of alice and bob."""
        super().setUp()
        create_lab_statuses()
        self.lab_devices = create_lab_devices()
        self.lab_a, self.lab_b, self.lab_a_clab02 = create_labs(create_containerlab_hosts(), self.lab_devices)
        self.users = {user.username: user for user in get_user_model().objects.all()}
        self.device_views = {}
        for name in ("alice", "bob"):
            grant_permission(self.users[name], Lab, "view")
            grant_permission(self.users[name], Lab, "change")
            self.device_views[name] = grant_permission(self.users[name], Device, "view")

    def run_build(self, username, lab=None):
        """Run the job as the user on the lab, which is lab-a of clab01 by default, and return the job result."""
        lab = lab or self.lab_a
        return create_job_result_and_run_job(JOB_MODULE, JOB_CLASS, username=username, lab=lab.pk)

    @staticmethod
    def messages(job_result, level):
        """Return the messages of the job log entries at the level."""
        return list(job_result.job_log_entries.filter(log_level=level).values_list("message", flat=True))

    def assert_failed_with(self, job_result, message, lab=None):
        """Check that the job failed with the message, and that the lab has no build."""
        lab = Lab.objects.get(pk=(lab or self.lab_a).pk)
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertIn(message, self.messages(job_result, LogLevelChoices.LOG_FAILURE))
        self.assertEqual((lab.topology_yaml, lab.built_at), ("", None))

    def test_job_is_registered_with_no_sensitive_variables(self):
        job_model = Job.objects.get(module_name=JOB_MODULE, job_class_name=JOB_CLASS)
        self.assertFalse(job_model.has_sensitive_variables)
        self.assertEqual(job_model.grouping, "Containerlab Orchestrator")

    def test_owner_build_succeeds(self):
        job_result = self.run_build("alice")
        self.assertJobResultStatus(job_result)
        lab = Lab.objects.get(pk=self.lab_a.pk)
        self.assertEqual(lab.status.name, LabStatusChoices.BUILT)
        self.assertIsNotNone(lab.built_at)
        self.assertIn("leaf1:", lab.topology_yaml)
        self.assertEqual(set(lab.lab_nodes.values_list("node_name", flat=True)), {"leaf1", "leaf2", "spine1"})
        summary = SUMMARY_MESSAGE.format(lab=lab, nodes=3, links=0, warnings=0, status=LabStatusChoices.BUILT)
        self.assertEqual(self.messages(job_result, LogLevelChoices.LOG_SUCCESS).count(summary), 1)
        self.assertEqual(job_result.result, {"nodes": 3, "links": 0, "warnings": 0})

    def test_builder_warnings_are_in_the_job_log(self):
        leaf1, leaf2 = self.lab_devices["leaf1"], self.lab_devices["leaf2"]
        (mgmt,) = TopologyTestCase.add_interfaces(leaf1, "Management1", mgmt_only=True)
        TopologyTestCase.connect(mgmt, TopologyTestCase.add_interfaces(leaf2, "Ethernet1")[0])
        job_result = self.run_build("alice")
        self.assertJobResultStatus(job_result)
        self.assertEqual(
            self.messages(job_result, LogLevelChoices.LOG_WARNING),
            [CABLE_WARNING.format(device=leaf1, interface=mgmt.name, text=W_MGMT_ONLY)],
        )
        self.assertEqual(job_result.result["warnings"], 1)

    def test_superuser_can_build_a_lab_of_another_owner(self):
        job_result = self.run_build("build-admin")
        self.assertJobResultStatus(job_result)
        self.assertEqual(Lab.objects.get(pk=self.lab_a.pk).status.name, LabStatusChoices.BUILT)

    def test_non_owner_fails_and_lab_does_not_change(self):
        job_result = self.run_build("bob")
        self.assert_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="build"))
        self.assertEqual(Lab.objects.get(pk=self.lab_a.pk).status.name, LabStatusChoices.PLANNED)
        self.assertFalse(LabNode.objects.filter(lab=self.lab_a).exists())

    def test_user_who_cannot_view_the_lab_gets_a_message_with_no_name(self):
        grant_permission(self.users["carol"], Lab, "view", constraints={"name": "lab-b"})
        job_result = self.run_build("carol")
        self.assert_failed_with(job_result, NO_VIEW_MESSAGE.format(verb="build"))
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("lab-a", entry.message)
                self.assertNotIn("lab-a", entry.log_object)
        failures = job_result.job_log_entries.filter(log_level=LogLevelChoices.LOG_FAILURE)
        self.assertEqual(set(failures.values_list("log_object", flat=True)), {""})

    def test_owner_without_change_permission_fails(self):
        carol = self.users["carol"]
        grant_permission(carol, Lab, "view")
        grant_permission(carol, Device, "view")
        job_result = self.run_build("carol", self.lab_a_clab02)
        self.assert_failed_with(
            job_result, NO_CHANGE_MESSAGE.format(lab=self.lab_a_clab02, verb="build"), self.lab_a_clab02
        )
        self.assertEqual(Lab.objects.get(pk=self.lab_a_clab02.pk).status.name, LabStatusChoices.DEPLOYED)

    def test_lab_with_no_owner_fails_for_a_non_superuser(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(owner=None)
        job_result = self.run_build("alice")
        self.assert_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="build"))

    def test_hidden_devices_fail_with_message(self):
        self.device_views["alice"].delete()
        grant_permission(self.users["alice"], Device, "view", constraints={"name": "leaf1"})
        job_result = self.run_build("alice")
        self.assert_failed_with(job_result, HIDDEN_DEVICES_MESSAGE.format(count=2, lab=self.lab_a))

    def test_empty_selection_fails_with_message(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(device_filter={})
        self.lab_a.devices.clear()
        job_result = self.run_build("alice")
        self.assert_failed_with(job_result, EMPTY_SELECTION_MESSAGE.format(lab=self.lab_a))

    def test_bad_setting_fails_with_message(self):
        with override_settings(PLUGINS_CONFIG={APP_NAME: {**DEFAULT_SETTINGS, "default_node_mapping": "linux"}}):
            job_result = self.run_build("alice")
        self.assert_failed_with(job_result, "The default_node_mapping setting must be a dict with a kind and an image.")

    def test_field_errors_keep_their_field_name(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(mgmt_subnet="172.20.21.0/30")
        job_result = self.run_build("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        failures = self.messages(job_result, LogLevelChoices.LOG_FAILURE)
        self.assertTrue(any(message.startswith("mgmt_subnet: ") for message in failures), failures)

    def test_job_log_and_result_hold_no_config_text(self):
        arista_mapping = create_node_mappings()[0]
        NodeMapping.objects.filter(pk=arista_mapping.pk).update(
            config_source=NodeMappingConfigSourceChoices.JINJA2, config_template="hostname MARKER-16-{{ device.name }}"
        )
        leaf1 = self.lab_devices["leaf1"]
        arista1 = Device.objects.create(
            name="arista1",
            device_type=arista_mapping.device_type,
            location=leaf1.location,
            status=leaf1.status,
            role=leaf1.role,
        )
        self.lab_a.devices.add(arista1)
        job_result = self.run_build("alice")
        self.assertJobResultStatus(job_result)
        self.assertIn("MARKER-16-arista1", Lab.objects.get(pk=self.lab_a.pk).topology_yaml)
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("MARKER-16", entry.message)
        self.assertNotIn("MARKER-16", str(job_result.result))


class LabJobTestCase(TransactionTestCase):
    """Base class that runs a lab job on lab-a of clab01, with a mocked client whose host has each image.

    A subclass sets the job class name, the client to mock, and the status that lab-a gets after its build. The job writes its log
    through a second database connection, so these tests commit their data.
    """

    job_class_name = ""
    patch_target = ""
    start_status = LabStatusChoices.BUILT

    def setUp(self):
        """Create the labs and the permissions of alice and bob, build lab-a of clab01, and mock the client."""
        super().setUp()
        create_lab_statuses()
        self.lab_devices = create_lab_devices()
        self.lab_a, self.lab_b, self.lab_a_clab02 = create_labs(create_containerlab_hosts(), self.lab_devices)
        self.users = {user.username: user for user in get_user_model().objects.all()}
        for name in ("alice", "bob"):
            grant_permission(self.users[name], Lab, "view")
            grant_permission(self.users[name], Lab, "change")
        self.lab_a = self.build(self.lab_a)
        patcher = mock.patch(self.patch_target, autospec=True)
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.client_mock = self.client_class.return_value
        self.client_mock.deploy.return_value = {self.lab_a.name: []}
        self.client_mock.inspect.return_value = [
            {"nodeName": name, "state": "running", "container_id": f"id-{name}"} for name in self.node_names()
        ]
        self.host_has_each_image()

    def build(self, lab):
        """Build the lab, set the start status, and return the saved lab."""
        build_lab(lab)
        Lab.objects.filter(pk=lab.pk).update(status=Status.objects.get(name=self.start_status))
        return Lab.objects.get(pk=lab.pk)

    def node_names(self):
        """Return the node names of lab-a of clab01."""
        return sorted(LabNode.objects.filter(lab=self.lab_a).values_list("node_name", flat=True))

    def host_has_each_image(self):
        """Make the image list of the host hold each image of the node rows of lab-a of clab01."""
        images = sorted(set(LabNode.objects.filter(lab=self.lab_a).values_list("image", flat=True)))
        self.client_mock.list_images.return_value = {"runtime": "docker", "images": [{"repoTags": images}]}

    def run_job(self, username, lab=None, **kwargs):
        """Run the job as the user on the lab, which is lab-a of clab01 by default, and return the job result."""
        lab = lab or self.lab_a
        return create_job_result_and_run_job(JOB_MODULE, self.job_class_name, username=username, lab=lab.pk, **kwargs)

    def status_of(self, lab=None):
        """Return the status name of the lab row, which is lab-a of clab01 by default."""
        return Lab.objects.get(pk=(lab or self.lab_a).pk).status.name

    def assert_access_failed_with(self, job_result, message, lab=None):
        """Check that the job failed with the message, made no API call, and did not change the lab."""
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertIn(message, BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE))
        self.client_class.assert_not_called()
        self.assertEqual(self.status_of(lab), self.start_status)


class DeployLabJobTestCase(LabJobTestCase):
    """Run the Deploy Lab job on the built lab-a of clab01."""

    job_class_name = "DeployLab"
    patch_target = "containerlab_orchestrator.deploy.ClabApiClient"
    start_status = LabStatusChoices.BUILT

    def test_deploy_job_is_registered_with_time_limits(self):
        job_model = Job.objects.get(module_name=JOB_MODULE, job_class_name="DeployLab")
        self.assertEqual((job_model.soft_time_limit, job_model.time_limit), (1800, 1900))
        self.assertFalse(job_model.has_sensitive_variables)
        self.assertEqual(job_model.grouping, "Containerlab Orchestrator")
        replace_field = DeployLab.replace_existing.as_field()
        self.assertEqual(replace_field.label, "Replace a lab that exists on the host")
        self.assertFalse(replace_field.initial)
        self.assertEqual(replace_field.help_text, REPLACE_HELP)

    def test_owner_deploy_succeeds_with_summary(self):
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)
        self.client_mock.deploy.assert_called_once_with(self.lab_a.name, self.lab_a.topology_yaml, reconfigure=False)
        summary = DEPLOY_SUMMARY.format(lab=self.lab_a, host=self.lab_a.host, nodes=3, states=3, warnings=0)
        successes = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_SUCCESS)
        self.assertEqual(successes.count(summary), 1)
        self.assertEqual(job_result.result, {"nodes": 3, "states": 3, "warnings": 0})

    def test_superuser_can_deploy_a_lab_of_another_owner(self):
        self.client_mock.inspect.side_effect = ClabApiError("Host clab01: cannot reach https://clab01: Timeout.")
        job_result = self.run_job("deploy-admin", replace_existing=True)
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)
        self.assertEqual(self.client_mock.deploy.call_args.kwargs, {"reconfigure": True})
        warning = INSPECT_WARNING.format(lab=self.lab_a, error="Host clab01: cannot reach https://clab01: Timeout.")
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_WARNING), [warning])
        self.assertEqual(job_result.result, {"nodes": 3, "states": 0, "warnings": 1})

    def test_non_owner_fails(self):
        job_result = self.run_job("bob")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="deploy"))

    def test_non_owner_without_change_permission_gets_the_owner_message(self):
        grant_permission(get_user_model().objects.create_user(username="dave"), Lab, "view")
        job_result = self.run_job("dave")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="deploy"))

    def test_user_who_cannot_view_the_lab_gets_a_message_with_no_name(self):
        grant_permission(self.users["carol"], Lab, "view", constraints={"name": "lab-b"})
        job_result = self.run_job("carol")
        self.assert_access_failed_with(job_result, NO_VIEW_MESSAGE.format(verb="deploy"))
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("lab-a", entry.message)
                self.assertNotIn("lab-a", entry.log_object)

    def test_owner_without_change_permission_fails(self):
        grant_permission(self.users["carol"], Lab, "view")
        lab = self.build(self.lab_a_clab02)
        job_result = self.run_job("carol", lab)
        self.assert_access_failed_with(job_result, NO_CHANGE_MESSAGE.format(lab=lab, verb="deploy"), lab)

    def test_lab_with_no_owner_fails_for_a_non_superuser(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(owner=None)
        job_result = self.run_job("alice")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="deploy"))

    def test_missing_image_fails_and_lists_images(self):
        self.client_mock.list_images.return_value = {"runtime": "docker", "images": []}
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        message = MISSING_IMAGES_MESSAGE.format(host=self.lab_a.host, images="alpine:latest (leaf1, leaf2, spine1)")
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE), [message])
        self.client_mock.deploy.assert_not_called()
        self.assertEqual(self.status_of(), LabStatusChoices.BUILT)

    def test_api_error_is_in_job_log_and_lab_is_failed(self):
        message = "Host clab01: POST /api/v1/labs returned HTTP 500: deploy failed"
        self.client_mock.deploy.side_effect = ClabApiError(message, status_code=500, server_message="deploy failed")
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE), [message])
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)

    def test_job_log_and_result_hold_no_yaml_or_config_of_the_job(self):
        arista_mapping = create_node_mappings()[0]
        NodeMapping.objects.filter(pk=arista_mapping.pk).update(
            config_source=NodeMappingConfigSourceChoices.JINJA2, config_template="hostname MARKER-18-{{ device.name }}"
        )
        leaf1 = self.lab_devices["leaf1"]
        arista1 = Device.objects.create(
            name="arista1",
            device_type=arista_mapping.device_type,
            location=leaf1.location,
            status=leaf1.status,
            role=leaf1.role,
        )
        self.lab_a.devices.add(arista1)
        self.lab_a = self.build(self.lab_a)
        self.assertIn("MARKER-18-arista1", self.lab_a.topology_yaml)
        self.host_has_each_image()
        server_text = "a" * 590 + "MARKER-18"
        self.client_mock.deploy.side_effect = ClabApiError(
            f"Host clab01: POST /api/v1/labs returned HTTP 500: {server_text}",
            status_code=500,
            server_message=server_text,
        )
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("MARKER-18", entry.message)
        self.assertNotIn("MARKER-18", str(job_result.result))

    def test_time_limit_in_deploy_logs_unknown_state(self):
        self.client_mock.deploy.side_effect = SoftTimeLimitExceeded()
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        failures = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE)
        self.assertIn(TIME_LIMIT_MESSAGE.format(lab=self.lab_a), failures)
        self.assertIn(DeployTimeLimit.__name__, job_result.traceback or "")
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)


class DestroyLabJobTestCase(LabJobTestCase):
    """Run the Destroy Lab job on the Deployed lab-a of clab01."""

    job_class_name = "DestroyLab"
    patch_target = "containerlab_orchestrator.destroy.ClabApiClient"
    start_status = LabStatusChoices.DEPLOYED

    def destroy_fails_with(self, status_code, server_message):
        """Make the destroy call of lab-a fail with this HTTP status and server text, and return the client text."""
        message = f"Host clab01: DELETE /api/v1/labs/lab-a returned HTTP {status_code}: {server_message}"
        self.client_mock.destroy.side_effect = ClabApiError(
            message, status_code=status_code, server_message=server_message
        )
        return message

    def test_destroy_job_is_registered_with_time_limits_and_keep_option(self):
        job_model = Job.objects.get(module_name=JOB_MODULE, job_class_name="DestroyLab")
        self.assertEqual((job_model.soft_time_limit, job_model.time_limit), (1800, 1900))
        self.assertFalse(job_model.has_sensitive_variables)
        self.assertEqual(job_model.grouping, "Containerlab Orchestrator")
        self.assertTrue(job_model.description.startswith("WARNING:"))
        self.assertEqual(list(DestroyLab._get_vars()), ["lab", "keep_lab_directory"])  # pylint: disable=protected-access
        keep_field = DestroyLab.keep_lab_directory.as_field()
        self.assertEqual(keep_field.label, "Keep the lab directory")
        self.assertFalse(keep_field.initial)
        self.assertEqual(keep_field.help_text, KEEP_DIRECTORY_HELP)

    def test_owner_destroy_succeeds_with_summary(self):
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DESTROYED)
        self.client_mock.login.assert_called_once_with()
        self.client_mock.destroy.assert_called_once_with("lab-a", cleanup=True)
        summary = DESTROY_SUMMARY.format(lab=self.lab_a, host=self.lab_a.host, nodes=3, warnings=0)
        successes = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_SUCCESS)
        self.assertEqual(successes.count(summary), 1)
        self.assertEqual(job_result.result, {"nodes": 3, "warnings": 0})

    def test_keep_option_reaches_the_service(self):
        job_result = self.run_job("alice", keep_lab_directory=True)
        self.assertJobResultStatus(job_result)
        self.client_mock.destroy.assert_called_once_with("lab-a", cleanup=False)

    def test_superuser_can_destroy_a_lab_of_another_owner(self):
        get_user_model().objects.create_user(username="destroy-admin", is_superuser=True)
        job_result = self.run_job("destroy-admin")
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DESTROYED)
        self.client_mock.destroy.assert_called_once_with("lab-a", cleanup=True)

    def test_non_owner_fails(self):
        job_result = self.run_job("bob")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="destroy"))

    def test_non_owner_without_change_permission_gets_the_owner_message(self):
        grant_permission(get_user_model().objects.create_user(username="dave"), Lab, "view")
        job_result = self.run_job("dave")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="destroy"))

    def test_user_who_cannot_view_the_lab_gets_a_message_with_no_name(self):
        grant_permission(self.users["carol"], Lab, "view", constraints={"name": "lab-b"})
        job_result = self.run_job("carol")
        self.assert_access_failed_with(job_result, NO_VIEW_MESSAGE.format(verb="destroy"))
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("lab-a", entry.message)
                self.assertNotIn("lab-a", entry.log_object)

    def test_owner_without_change_permission_fails(self):
        grant_permission(self.users["carol"], Lab, "view")
        lab = self.build(self.lab_a_clab02)
        job_result = self.run_job("carol", lab)
        self.assert_access_failed_with(job_result, NO_CHANGE_MESSAGE.format(lab=lab, verb="destroy"), lab)

    def test_lab_with_no_owner_fails_for_a_non_superuser(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(owner=None)
        job_result = self.run_job("alice")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="destroy"))

    def test_lab_not_on_host_succeeds_with_warning(self):
        self.destroy_fails_with(404, "lab 'lab-a' not found")
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DESTROYED)
        warning = NOT_FOUND_WARNING.format(host=self.lab_a.host, lab=self.lab_a)
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_WARNING), [warning])
        summary = DESTROY_SUMMARY.format(lab=self.lab_a, host=self.lab_a.host, nodes=3, warnings=1)
        successes = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_SUCCESS)
        self.assertEqual(successes.count(summary), 1)
        self.assertEqual(job_result.result, {"nodes": 3, "warnings": 1})

    def test_api_error_is_in_job_log_and_lab_is_failed(self):
        message = self.destroy_fails_with(500, "destroy failed")
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE), [message])
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)

    def test_busy_host_logs_both_lines_and_keeps_the_status(self):
        message = self.destroy_fails_with(409, "lab busy")
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        busy = DESTROY_BUSY_MESSAGE.format(host=self.lab_a.host, lab=self.lab_a)
        failures = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE)
        self.assertEqual(failures, [message, busy])
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)

    def test_job_log_and_result_hold_no_yaml_or_config_of_the_job(self):
        arista_mapping = create_node_mappings()[0]
        NodeMapping.objects.filter(pk=arista_mapping.pk).update(
            config_source=NodeMappingConfigSourceChoices.JINJA2, config_template="hostname MARKER-19-{{ device.name }}"
        )
        leaf1 = self.lab_devices["leaf1"]
        arista1 = Device.objects.create(
            name="arista1",
            device_type=arista_mapping.device_type,
            location=leaf1.location,
            status=leaf1.status,
            role=leaf1.role,
        )
        self.lab_a.devices.add(arista1)
        self.lab_a = self.build(self.lab_a)
        self.assertIn("MARKER-19-arista1", self.lab_a.topology_yaml)
        self.destroy_fails_with(500, "a" * 590 + "MARKER-19")
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("MARKER-19", entry.message)
        self.assertNotIn("MARKER-19", str(job_result.result))

    def test_time_limit_in_destroy_logs_unknown_state(self):
        self.client_mock.destroy.side_effect = SoftTimeLimitExceeded()
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        failures = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE)
        self.assertIn(DESTROY_TIME_LIMIT_MESSAGE.format(lab=self.lab_a), failures)
        self.assertIn(DestroyTimeLimit.__name__, job_result.traceback or "")
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)

    def test_time_limit_in_failed_write_logs_unknown_state(self):
        self.destroy_fails_with(500, "destroy failed")
        with mock.patch("containerlab_orchestrator.destroy.write_failed", side_effect=SoftTimeLimitExceeded()):
            job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        failures = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE)
        self.assertIn(DESTROY_TIME_LIMIT_MESSAGE.format(lab=self.lab_a), failures)
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)


class RedeployLabJobTestCase(LabJobTestCase):
    """Run the Redeploy Lab job on the Deployed lab-a of clab01."""

    job_class_name = "RedeployLab"
    patch_target = "containerlab_orchestrator.redeploy.ClabApiClient"
    start_status = LabStatusChoices.DEPLOYED

    def test_redeploy_job_is_registered_with_time_limits(self):
        job_model = Job.objects.get(module_name=JOB_MODULE, job_class_name="RedeployLab")
        self.assertEqual((job_model.soft_time_limit, job_model.time_limit), (2600, 2700))
        self.assertFalse(job_model.has_sensitive_variables)
        self.assertEqual(job_model.grouping, "Containerlab Orchestrator")
        self.assertEqual(job_model.name, "Redeploy Lab")
        self.assertTrue(job_model.description.startswith("WARNING:"))
        self.assertEqual(list(RedeployLab._get_vars()), ["lab"])  # pylint: disable=protected-access

    def test_owner_redeploy_succeeds_with_summary(self):
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)
        self.client_mock.destroy.assert_called_once_with("lab-a", cleanup=True)
        self.client_mock.deploy.assert_called_once_with(self.lab_a.name, self.lab_a.topology_yaml, reconfigure=False)
        container_ids = sorted(LabNode.objects.filter(lab=self.lab_a).values_list("container_id", flat=True))
        self.assertEqual(container_ids, [f"id-{name}" for name in self.node_names()])
        summary = REDEPLOY_SUMMARY.format(lab=self.lab_a, host=self.lab_a.host, nodes=3, states=3, warnings=0)
        successes = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_SUCCESS)
        self.assertEqual(successes.count(summary), 1)
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_WARNING), [])
        self.assertEqual(job_result.result, {"nodes": 3, "states": 3, "warnings": 0})

    def test_superuser_can_redeploy_a_lab_of_another_owner(self):
        get_user_model().objects.create_user(username="redeploy-admin", is_superuser=True)
        self.client_mock.destroy.side_effect = ClabApiError(
            "Host clab01: DELETE /api/v1/labs/lab-a returned HTTP 404: lab 'lab-a' not found",
            status_code=404,
            server_message="lab 'lab-a' not found",
        )
        self.client_mock.inspect.return_value = [{"nodeName": "leaf1", "state": "running", "container_id": "id-leaf1"}]
        job_result = self.run_job("redeploy-admin")
        self.assertJobResultStatus(job_result)
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)
        warning = NOT_FOUND_WARNING.format(host=self.lab_a.host, lab=self.lab_a)
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_WARNING), [warning])
        summary = REDEPLOY_SUMMARY.format(lab=self.lab_a, host=self.lab_a.host, nodes=3, states=1, warnings=1)
        successes = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_SUCCESS)
        self.assertEqual(successes.count(summary), 1)
        self.assertEqual(job_result.result, {"nodes": 3, "states": 1, "warnings": 1})

    def test_non_owner_fails(self):
        job_result = self.run_job("bob")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="redeploy"))

    def test_non_owner_without_change_permission_gets_the_owner_message(self):
        grant_permission(get_user_model().objects.create_user(username="dave"), Lab, "view")
        job_result = self.run_job("dave")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="redeploy"))

    def test_user_who_cannot_view_the_lab_gets_a_message_with_no_name(self):
        grant_permission(self.users["carol"], Lab, "view", constraints={"name": "lab-b"})
        job_result = self.run_job("carol")
        self.assert_access_failed_with(job_result, NO_VIEW_MESSAGE.format(verb="redeploy"))
        for entry in job_result.job_log_entries.all():
            with self.subTest(message=entry.message):
                self.assertNotIn("lab-a", entry.message)
                self.assertNotIn("lab-a", entry.log_object)

    def test_owner_without_change_permission_fails(self):
        grant_permission(self.users["carol"], Lab, "view")
        lab = self.build(self.lab_a_clab02)
        job_result = self.run_job("carol", lab)
        self.assert_access_failed_with(job_result, NO_CHANGE_MESSAGE.format(lab=lab, verb="redeploy"), lab)

    def test_lab_with_no_owner_fails_for_a_non_superuser(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(owner=None)
        job_result = self.run_job("alice")
        self.assert_access_failed_with(job_result, NOT_OWNER_MESSAGE.format(lab=self.lab_a, verb="redeploy"))

    def test_missing_image_fails_and_lists_images(self):
        self.client_mock.list_images.return_value = {"runtime": "docker", "images": []}
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        message = MISSING_IMAGES_MESSAGE.format(host=self.lab_a.host, images="alpine:latest (leaf1, leaf2, spine1)")
        self.assertEqual(BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE), [message])
        self.client_mock.destroy.assert_not_called()
        self.client_mock.deploy.assert_not_called()
        self.assertEqual(self.status_of(), LabStatusChoices.DEPLOYED)

    def test_deploy_error_after_destroy_is_in_job_log(self):
        message = "Host clab01: POST /api/v1/labs returned HTTP 500: deploy failed"
        self.client_mock.deploy.side_effect = ClabApiError(message, status_code=500, server_message="deploy failed")
        job_result = self.run_job("alice")
        self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
        half_done = HALF_DONE_MESSAGE.format(lab=self.lab_a, host=self.lab_a.host)
        failures = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE)
        self.assertEqual(failures, [message, half_done])
        self.assertEqual(self.status_of(), LabStatusChoices.FAILED)
        self.assertIsNone(job_result.result)

    def test_time_limit_in_redeploy_logs_unknown_state(self):
        half_done = HALF_DONE_MESSAGE.format(lab=self.lab_a, host=self.lab_a.host)
        cases = (
            ("destroy", DestroyTimeLimit, [DESTROY_TIME_LIMIT_MESSAGE.format(lab=self.lab_a)]),
            ("deploy", DeployTimeLimit, [TIME_LIMIT_MESSAGE.format(lab=self.lab_a), half_done]),
        )
        for call, raised, expected in cases:
            with self.subTest(call=call):
                Lab.objects.filter(pk=self.lab_a.pk).update(status=Status.objects.get(name=self.start_status))
                self.client_mock.reset_mock()
                getattr(self.client_mock, call).side_effect = SoftTimeLimitExceeded()
                job_result = self.run_job("alice")
                getattr(self.client_mock, call).side_effect = None
                self.assertJobResultStatus(job_result, JobResultStatusChoices.STATUS_FAILURE)
                failures = BuildLabTopologyJobTestCase.messages(job_result, LogLevelChoices.LOG_FAILURE)
                self.assertEqual(failures, expected)
                self.assertIn(raised.__name__, job_result.traceback or "")
                self.assertEqual(self.status_of(), LabStatusChoices.FAILED)
                self.assertEqual(self.client_mock.deploy.call_count, int(call == "deploy"))
