"""Tests for the redeploy service that destroys the lab on its host, then deploys its stored topology again."""

from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone
from nautobot.extras.models import Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.deploy import (
    CONFLICT_MESSAGE,
    INSPECT_WARNING,
    MISSING_IMAGES_MESSAGE,
    NEW_BUILD_WARNING,
    NO_TOPOLOGY_MESSAGE,
    STATUS_MISSING_MESSAGE,
    DeployTimeLimit,
    error_text,
)
from containerlab_orchestrator.destroy import (
    DESTROY_BUSY_MESSAGE,
    NOT_FOUND_WARNING,
    NOT_OWNED_MESSAGE,
    DestroyTimeLimit,
)
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.redeploy import HALF_DONE_MESSAGE, REDEPLOY_STATUS_MESSAGE, redeploy_lab
from containerlab_orchestrator.tests.test_deploy import OLD_INSPECT, DeployLabTestCase, api_error
from containerlab_orchestrator.tests.test_destroy import destroy_error, plain_not_found

NEW_NODE_STATE = {"arista1": ("running", "id-arista1"), "leaf1": ("running", "id-leaf1")}
CLEARED_NODES = {"arista1": ("", ""), "leaf1": ("", "")}


class RedeployLabTestCase(DeployLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the order of the calls, the status, and the node state of a redeploy."""

    def setUp(self):
        """Set Deployed with old node state, and mock a client that destroys and deploys the lab."""
        super().setUp()
        self._set_status(LabStatusChoices.DEPLOYED)
        self._seed_old_node_state()
        deploy_client = self.client_mock
        patcher = mock.patch("containerlab_orchestrator.redeploy.ClabApiClient", autospec=True)
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.client_mock = self.client_class.return_value
        for name in ("list_images", "deploy", "inspect"):
            getattr(self.client_mock, name).return_value = getattr(deploy_client, name).return_value
        self.client_mock.destroy.return_value = {"message": "Lab 'build-lab' destroyed successfully"}
        self.images = deploy_client.list_images.return_value

    def _reset(self, status=LabStatusChoices.DEPLOYED):
        """Set the status and the old node state again, and clear the calls and the side effects of the client."""
        self._set_status(status)
        self._seed_old_node_state()
        self.client_mock.reset_mock(side_effect=True)
        self.client_mock.list_images.return_value = self.images

    def _assert_fails_with(self, messages):
        """Assert that the redeploy fails with these messages."""
        with self.assertRaises(ValidationError) as raised:
            redeploy_lab(self.lab)
        self.assertEqual(raised.exception.messages, messages)

    def _assert_fails_with_no_api_call(self, messages):
        """Assert that the redeploy fails with these messages, makes no client, and does not change the lab."""
        before = self._snapshot(), self._node_state()
        self._assert_fails_with(messages)
        self.client_class.assert_not_called()
        self.assertEqual((self._snapshot(), self._node_state()), before)

    def test_redeploy_destroys_then_deploys_the_current_stored_yaml(self):
        new_yaml = self._lab().topology_yaml + "# MARKER-19-new\n"
        Lab.objects.filter(pk=self.lab.pk).update(topology_yaml=new_yaml)
        redeploy_lab(self.lab)
        self.assertEqual(
            self.client_mock.method_calls,
            [
                mock.call.list_images(),
                mock.call.destroy("build-lab", cleanup=True),
                mock.call.deploy("build-lab", new_yaml, reconfigure=False),
                mock.call.inspect("build-lab"),
                mock.call.close(),
            ],
        )

    def test_redeploy_sets_deployed_and_writes_node_state(self):
        for status in (LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED):
            with self.subTest(status=status):
                self._reset(status)
                Lab.objects.filter(pk=self.lab.pk).update(last_inspect=OLD_INSPECT)
                result = redeploy_lab(self.lab)
                lab = self._lab()
                self.assertEqual(lab.status.name, LabStatusChoices.DEPLOYED)
                self.assertEqual(self._node_state(), NEW_NODE_STATE)
                self.assertEqual(lab.last_inspect, self.client_mock.inspect.return_value)
                self.assertEqual((result.lab, result.node_count, result.state_count, result.warnings), (lab, 2, 2, []))
                self.assertEqual(result.lab.status.name, LabStatusChoices.DEPLOYED)
        with self.subTest(case="counts"):
            self._reset()
            LabNode.objects.create(lab=self.lab, node_name="gone1", kind="linux", image="alpine", state="running")
            self.client_mock.inspect.return_value = [{"nodeName": "arista1", "state": "running", "container_id": "a"}]
            result = redeploy_lab(self.lab)
            self.assertEqual((result.node_count, result.state_count), (2, 1))
            self.assertEqual(self._node_state(), {"arista1": ("running", "a"), "gone1": ("", ""), "leaf1": ("", "")})

    def test_status_that_cannot_redeploy_fails_with_no_api_call(self):
        Lab.objects.filter(pk=self.lab.pk).update(topology_yaml="")
        for status in (LabStatusChoices.PLANNED, LabStatusChoices.BUILT, LabStatusChoices.DESTROYED):
            with self.subTest(status=status):
                self._set_status(status)
                self._assert_fails_with_no_api_call([REDEPLOY_STATUS_MESSAGE.format(lab=self.lab, status=status)])
        with self.subTest(case="no topology"):
            self._set_status(LabStatusChoices.DEPLOYED)
            self._assert_fails_with_no_api_call([NO_TOPOLOGY_MESSAGE.format(lab=self.lab)])

    def test_missing_status_fails_with_no_api_call(self):
        lab_type = ContentType.objects.get_for_model(Lab)
        Lab.objects.filter(pk=self.lab.pk).update(topology_yaml="")
        names = (LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED, LabStatusChoices.DESTROYED)
        for status in Status.objects.filter(name__in=names):
            with self.subTest(status=status.name):
                status.content_types.remove(lab_type)
                try:
                    self._set_status(LabStatusChoices.BUILT)
                    self._assert_fails_with_no_api_call(
                        [REDEPLOY_STATUS_MESSAGE.format(lab=self.lab, status=LabStatusChoices.BUILT)]
                    )
                    self._set_status(LabStatusChoices.DEPLOYED)
                    self._assert_fails_with_no_api_call([STATUS_MISSING_MESSAGE.format(status=status.name)])
                finally:
                    status.content_types.add(lab_type)

    def test_missing_image_stops_before_the_destroy(self):
        rows = self._rows()
        self.client_mock.list_images.return_value = {"images": [{"repoTags": [rows["arista1"].image]}]}
        before = self._snapshot(), self._node_state()
        images = f"{rows['leaf1'].image} (leaf1)"
        self._assert_fails_with([MISSING_IMAGES_MESSAGE.format(host=self.lab.host, images=images)])
        self.assertEqual(self.client_mock.method_calls, [mock.call.list_images(), mock.call.close()])
        self.assertEqual((self._snapshot(), self._node_state()), before)

    def test_destroy_failure_stops_before_the_deploy(self):
        server_error = destroy_error(500, "x" * 600)
        busy = destroy_error(409, "lab 'build-lab' is busy")
        not_owned = destroy_error(404, "lab 'build-lab' not found or not owned by user")
        cases = {
            "500": (server_error, [error_text(server_error)], LabStatusChoices.FAILED),
            "409": (busy, [busy.message, DESTROY_BUSY_MESSAGE.format(host=self.lab.host, lab=self.lab)], None),
            "not owned": (not_owned, [NOT_OWNED_MESSAGE.format(host=self.lab.host, lab=self.lab)], None),
        }
        for case, (error, messages, status) in cases.items():
            with self.subTest(case=case):
                self._reset()
                self.client_mock.destroy.side_effect = error
                self._assert_fails_with(messages)
                self.assertEqual(self._lab().status.name, status or LabStatusChoices.DEPLOYED)
                self.assertEqual(set(self._node_state().values()), {("running", "old")})
                self.client_mock.deploy.assert_not_called()
                self.client_mock.inspect.assert_not_called()

    def test_lab_not_on_host_continues_to_the_deploy(self):
        self.client_mock.destroy.side_effect = plain_not_found()
        result = redeploy_lab(self.lab)
        self.assertEqual(result.warnings, [NOT_FOUND_WARNING.format(host=self.lab.host, lab=self.lab)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)
        self.assertEqual(self._node_state(), NEW_NODE_STATE)
        self.client_mock.deploy.assert_called_once_with("build-lab", self._lab().topology_yaml, reconfigure=False)

    def test_destroy_warning_comes_before_the_inspect_warning(self):
        self.client_mock.destroy.side_effect = plain_not_found()
        self.client_mock.inspect.side_effect = api_error(500)
        result = redeploy_lab(self.lab)
        self.assertEqual(
            result.warnings,
            [
                NOT_FOUND_WARNING.format(host=self.lab.host, lab=self.lab),
                INSPECT_WARNING.format(lab=self.lab, error=error_text(api_error(500))),
            ],
        )

    def test_deploy_failure_after_the_destroy(self):
        half_done = HALF_DONE_MESSAGE.format(lab=self.lab, host=self.lab.host)
        conflict = api_error(409, "Lab 'build-lab' already exists.")
        cases = {
            "500": (api_error(500), [api_error(500).message, half_done], LabStatusChoices.FAILED),
            "400": (api_error(400), [api_error(400).message, half_done], LabStatusChoices.DESTROYED),
            "409": (
                conflict,
                [conflict.message, CONFLICT_MESSAGE.format(host=self.lab.host, lab=self.lab), half_done],
                LabStatusChoices.DESTROYED,
            ),
        }
        for case, (error, messages, status) in cases.items():
            with self.subTest(case=case):
                self._reset()
                self.client_mock.deploy.side_effect = error
                self._assert_fails_with(messages)
                self.assertEqual(self._lab().status.name, status)
                self.assertEqual(self._node_state(), CLEARED_NODES)
                self.client_mock.inspect.assert_not_called()

    def test_new_build_during_the_redeploy_gives_warning(self):
        start_yaml = self._lab().topology_yaml

        def build_during_destroy(*_args, **_kwargs):
            Lab.objects.filter(pk=self.lab.pk).update(built_at=timezone.now(), topology_yaml=start_yaml + "# new\n")
            raise plain_not_found()

        self.client_mock.destroy.side_effect = build_during_destroy
        result = redeploy_lab(self.lab)
        self.assertEqual(
            result.warnings,
            [NOT_FOUND_WARNING.format(host=self.lab.host, lab=self.lab), NEW_BUILD_WARNING.format(lab=self.lab)],
        )
        self.client_mock.deploy.assert_called_once_with("build-lab", start_yaml, reconfigure=False)
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)

    def test_soft_time_limit_paths(self):
        cases = (
            ("list_images", SoftTimeLimitExceeded, LabStatusChoices.DEPLOYED, {("running", "old")}, OLD_INSPECT),
            ("destroy", DestroyTimeLimit, LabStatusChoices.FAILED, {("running", "old")}, {}),
            ("deploy", DeployTimeLimit, LabStatusChoices.FAILED, {("", "")}, {}),
            ("inspect", SoftTimeLimitExceeded, LabStatusChoices.DEPLOYED, {("", "")}, {}),
        )
        for method_name, raised_class, status, node_state, last_inspect in cases:
            with self.subTest(method=method_name):
                self._reset()
                Lab.objects.filter(pk=self.lab.pk).update(last_inspect=OLD_INSPECT)
                getattr(self.client_mock, method_name).side_effect = SoftTimeLimitExceeded()
                with self.assertRaises(SoftTimeLimitExceeded) as raised:
                    redeploy_lab(self.lab)
                getattr(self.client_mock, method_name).side_effect = None
                self.assertIs(type(raised.exception), raised_class)
                self.assertEqual(self._lab().status.name, status)
                self.assertEqual(set(self._node_state().values()), node_state)
                self.assertEqual(self._lab().last_inspect, last_inspect)
                self.client_mock.close.assert_called_once_with()

    def test_client_is_closed(self):
        cases = {
            "success": lambda: None,
            "missing image": lambda: setattr(self.client_mock.list_images, "return_value", {"images": []}),
            "destroy error": lambda: setattr(self.client_mock.destroy, "side_effect", destroy_error(400)),
            "deploy error": lambda: setattr(self.client_mock.deploy, "side_effect", api_error(400)),
        }
        for case, prepare in cases.items():
            with self.subTest(case=case):
                self._reset()
                prepare()
                try:
                    redeploy_lab(self.lab)
                except ValidationError:
                    pass
                self.client_class.assert_called_with(self._lab().host)
                self.client_mock.close.assert_called_once_with()
