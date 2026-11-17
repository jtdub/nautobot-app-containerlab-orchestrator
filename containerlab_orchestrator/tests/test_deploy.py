"""Tests for the deploy service that runs the stored topology of a lab on its host."""

from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone
from nautobot.extras.models import Status

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.deploy import (
    BAD_TOPOLOGY_MESSAGE,
    CONFLICT_MESSAGE,
    IMAGE_LIST_MESSAGE,
    INSPECT_WARNING,
    LAB_DELETED_MESSAGE,
    MISSING_IMAGES_MESSAGE,
    NEW_BUILD_WARNING,
    NO_TOPOLOGY_MESSAGE,
    RENAMED_MESSAGE,
    STATUS_CHANGED_MESSAGE,
    STATUS_MESSAGE,
    STATUS_MISSING_MESSAGE,
    DeployTimeLimit,
    deploy_lab,
    error_text,
    host_images,
    missing_images,
    normalize_image,
)
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.tests.test_build import BuildLabTestCase

DIGEST = "sha256:" + "a1" * 32
SERVER_ERROR_PREFIX = "Host clab01: POST /api/v1/labs returned HTTP 500: "


def api_error(status_code, server_message="failed"):
    """Return the ClabApiError of an HTTP error status of the deploy call."""
    return ClabApiError(
        f"Host clab01: POST /api/v1/labs returned HTTP {status_code}: {server_message}",
        status_code=status_code,
        server_message=server_message,
    )


class ImageMatchTestCase(SimpleTestCase):
    """Test the image normalization, the image list of the host, and the error text cut."""

    def test_normalize_image_adds_registry_library_and_tag(self):
        cases = {
            "alpine": "docker.io/library/alpine:latest",
            "alpine:3.20": "docker.io/library/alpine:3.20",
            "nokia/x:1": "docker.io/nokia/x:1",
            "ghcr.io/nokia/srlinux:24.10": "ghcr.io/nokia/srlinux:24.10",
            "localhost:5000/a": "localhost:5000/a:latest",
            "localhost/a:2": "localhost/a:2",
            "index.docker.io/alpine": "docker.io/library/alpine:latest",
            "index.docker.io/library/alpine:3": "docker.io/library/alpine:3",
            f"a@{DIGEST}": f"docker.io/library/a@{DIGEST}",
            f"a:1@{DIGEST}": f"docker.io/library/a@{DIGEST}",
        }
        for reference, expected in cases.items():
            with self.subTest(reference=reference):
                self.assertEqual(normalize_image(reference), expected)

    def test_host_images_reads_docker_and_podman_forms(self):
        response = {
            "runtime": "docker",
            "images": [
                {"repoTags": ["alpine:latest"], "repoDigests": [f"alpine@{DIGEST}"]},
                {"repoTags": ["docker.io/library/ceos:4.32.0F"], "repoDigests": []},
            ],
        }
        self.assertEqual(
            host_images(response, "clab01"),
            {
                "docker.io/library/alpine:latest",
                f"docker.io/library/alpine@{DIGEST}",
                "docker.io/library/ceos:4.32.0F",
            },
        )

    def test_host_images_rejects_a_list_it_cannot_read(self):
        for response in ({}, [], {"images": "x"}, {"images": ["x"]}, {"images": [{"repoTags": [5], "repoDigests": []}]}):
            with self.subTest(response=response):
                with self.assertRaises(ValidationError) as raised:
                    host_images(response, "clab01")
                self.assertEqual(raised.exception.messages, [IMAGE_LIST_MESSAGE.format(host="clab01")])
        self.assertEqual(host_images({"images": [{}]}, "clab01"), set())
        self.assertEqual(host_images({"images": []}, "clab01"), set())

    def test_missing_images_lists_each_image_with_sorted_nodes(self):
        nodes = {
            "leaf2": {"image": "ceos:4.32.0F"},
            "leaf1": {"image": "ceos:4.32.0F"},
            "stub-wan1": {"image": "alpine"},
            "x": {"image": "alpine:latest"},
            "srl1": {"image": "ghcr.io/nokia/srlinux:24.10"},
        }
        available = {"ghcr.io/nokia/srlinux:24.10"}
        self.assertEqual(missing_images(nodes, available), "alpine (stub-wan1, x); ceos:4.32.0F (leaf1, leaf2)")
        self.assertEqual(missing_images({"srl1": {"image": "ghcr.io/nokia/srlinux:24.10"}}, available), "")

    def test_error_text_cuts_only_the_server_text(self):
        server_text = "a" * 500 + "MARKER"
        error = ClabApiError(SERVER_ERROR_PREFIX + server_text, status_code=500, server_message=server_text)
        self.assertEqual(error_text(error), SERVER_ERROR_PREFIX + "a" * 500)
        short = ClabApiError(SERVER_ERROR_PREFIX + "failed", status_code=500, server_message="failed")
        self.assertEqual(error_text(short), SERVER_ERROR_PREFIX + "failed")
        transport = ClabApiError("Host clab01: cannot reach https://clab01: ConnectionError.")
        self.assertEqual(error_text(transport), transport.message)


class DeployLabTestCase(BuildLabTestCase):
    """Base class with the built lab build-lab and a mocked client that deploys it."""

    def setUp(self):
        """Build the lab, and mock a client whose host has each image of the lab."""
        super().setUp()
        build_lab(self.lab)
        self._set_status(LabStatusChoices.BUILT)
        patcher = mock.patch("containerlab_orchestrator.deploy.ClabApiClient", autospec=True)
        self.client_class = patcher.start()
        self.addCleanup(patcher.stop)
        self.client_mock = self.client_class.return_value
        rows = list(LabNode.objects.filter(lab=self.lab))
        self.client_mock.list_images.return_value = {
            "runtime": "docker",
            "images": [{"repoTags": [row.image for row in rows], "repoDigests": []}],
        }
        self.client_mock.inspect.return_value = [
            {"nodeName": row.node_name, "state": "running", "container_id": f"id-{row.node_name}"} for row in rows
        ]
        self.client_mock.deploy.return_value = {"build-lab": []}

    def _lab(self):
        """Return the saved row of the lab."""
        return Lab.objects.get(pk=self.lab.pk)

    def _node_state(self):
        """Return the state and the container ID of each node row by node name."""
        return {row.node_name: (row.state, row.container_id) for row in self._rows().values()}


class DeployLabResultTestCase(DeployLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the status, the node state, the warnings, and the client calls of a deploy."""

    def test_deploy_sets_deployed_and_writes_node_state(self):
        result = deploy_lab(self.lab)
        lab = self._lab()
        self.assertEqual(lab.status.name, LabStatusChoices.DEPLOYED)
        self.assertEqual(
            self._node_state(), {"arista1": ("running", "id-arista1"), "leaf1": ("running", "id-leaf1")}
        )
        self.assertEqual((result.lab, result.node_count, result.state_count, result.warnings), (lab, 2, 2, []))
        self.client_class.assert_called_once_with(lab.host)
        self.client_mock.inspect.assert_called_once_with("build-lab")

    def test_deploy_sends_stored_yaml_and_reconfigure_option(self):
        cases = (
            (LabStatusChoices.BUILT, False),
            (LabStatusChoices.DESTROYED, False),
            (LabStatusChoices.FAILED, False),
            (LabStatusChoices.FAILED, True),
        )
        for status, replace_existing in cases:
            with self.subTest(status=status, replace_existing=replace_existing):
                self._set_status(status)
                self.client_mock.deploy.reset_mock()
                deploy_lab(self.lab, replace_existing=replace_existing)
                self.client_mock.deploy.assert_called_once_with(
                    "build-lab", self._lab().topology_yaml, reconfigure=replace_existing
                )

    def test_missing_image_stops_before_deploy_call(self):
        rows = self._rows()
        self.client_mock.list_images.return_value = {"images": [{"repoTags": [rows["arista1"].image]}]}
        before = self._snapshot()
        with self.assertRaises(ValidationError) as raised:
            deploy_lab(self.lab)
        images = f"{rows['leaf1'].image} (leaf1)"
        self.assertEqual(
            raised.exception.messages, [MISSING_IMAGES_MESSAGE.format(host=self.lab.host, images=images)]
        )
        self.client_mock.deploy.assert_not_called()
        self.assertEqual(self._snapshot(), before)

    def test_podman_names_match_node_images(self):
        podman_tags = [normalize_image(row.image) for row in self._rows().values()]
        self.client_mock.list_images.return_value = {"runtime": "podman", "images": [{"repoTags": podman_tags}]}
        deploy_lab(self.lab)
        self.client_mock.deploy.assert_called_once()
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)

    def test_inspect_error_keeps_deployed_with_warning(self):
        error = api_error(500, "x" * 600)
        cases = ((error, error_text(error)), ({"build-lab": []}, "the response is not a list"))
        for response, detail in cases:
            with self.subTest(detail=detail[:40]):
                self._set_status(LabStatusChoices.BUILT)
                self.client_mock.inspect.side_effect = [response] if isinstance(response, dict) else response
                result = deploy_lab(self.lab)
                self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)
                self.assertEqual(result.warnings, [INSPECT_WARNING.format(lab=self.lab, error=detail)])
                self.assertEqual((result.state_count, set(self._node_state().values())), (0, {("", "")}))

    def test_rows_with_no_container_are_cleared_and_bad_values_skipped(self):
        LabNode.objects.filter(lab=self.lab, node_name="leaf1").update(state="running", container_id="old")
        self.client_mock.inspect.return_value = [
            {"nodeName": "arista1", "state": 5, "container_id": "c" * 300},
            {"nodeName": "ghost", "state": "running", "container_id": "g"},
            "not a container",
        ]
        result = deploy_lab(self.lab)
        self.assertEqual(self._node_state(), {"arista1": ("", "c" * 255), "leaf1": ("", "")})
        self.assertEqual(result.state_count, 1)

    def test_new_build_during_deploy_gives_warning(self):
        def build_during_deploy(*_args, **_kwargs):
            Lab.objects.filter(pk=self.lab.pk).update(built_at=timezone.now())
            return {}

        self.client_mock.deploy.side_effect = build_during_deploy
        result = deploy_lab(self.lab)
        self.assertEqual(result.warnings, [NEW_BUILD_WARNING.format(lab=self.lab)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)

    def test_status_writes_are_narrow(self):
        cases = ((LabStatusChoices.DEPLOYED, None), (LabStatusChoices.FAILED, api_error(500)))
        for status, deploy_error in cases:
            with self.subTest(status=status):
                self._set_status(LabStatusChoices.BUILT)
                start = self._lab().last_updated
                self.client_mock.deploy.side_effect = deploy_error
                with mock.patch.object(Lab, "save", autospec=True, side_effect=Lab.save) as save:
                    try:
                        deploy_lab(self.lab)
                    except ValidationError:
                        pass
                self.assertEqual(self._lab().status.name, status)
                self.assertEqual(save.call_count, 1)
                self.assertEqual(save.call_args.kwargs, {"update_fields": ["status", "last_updated"]})
                self.assertGreater(self._lab().last_updated, start)

    def test_client_is_closed(self):
        cases = {
            "success": lambda: None,
            "missing image": lambda: setattr(self.client_mock.list_images, "return_value", {"images": []}),
            "deploy error": lambda: setattr(self.client_mock.deploy, "side_effect", api_error(400)),
        }
        for case, prepare in cases.items():
            with self.subTest(case=case):
                self._set_status(LabStatusChoices.BUILT)
                self.client_mock.close.reset_mock()
                prepare()
                try:
                    deploy_lab(self.lab)
                except ValidationError:
                    pass
                self.client_mock.close.assert_called_once_with()


class DeployLabFailureTestCase(DeployLabTestCase):  # pylint: disable=too-many-ancestors
    """Test the checks before any API call, and the status after each failed client call."""

    def _assert_fails_with(self, messages):
        """Assert that the deploy fails with these messages."""
        with self.assertRaises(ValidationError) as raised:
            deploy_lab(self.lab)
        self.assertEqual(raised.exception.messages, messages)

    def _assert_fails_with_no_api_call(self, messages):
        """Assert that the deploy fails with these messages, makes no client, and does not change the lab."""
        before = self._snapshot()
        self._assert_fails_with(messages)
        self.client_class.assert_not_called()
        self.assertEqual(self._snapshot(), before)

    def test_status_that_cannot_deploy_fails_with_no_api_call(self):
        for status in (LabStatusChoices.PLANNED, LabStatusChoices.DEPLOYED):
            with self.subTest(status=status):
                self._set_status(status)
                self._assert_fails_with_no_api_call([STATUS_MESSAGE.format(lab=self.lab, status=status)])

    def test_lab_with_no_topology_fails(self):
        self._set_status(LabStatusChoices.DESTROYED)
        Lab.objects.filter(pk=self.lab.pk).update(topology_yaml="")
        self._assert_fails_with_no_api_call([NO_TOPOLOGY_MESSAGE.format(lab=self.lab)])

    def test_renamed_lab_fails_with_no_api_call(self):
        Lab.objects.filter(pk=self.lab.pk).update(name="renamed-lab")
        self._assert_fails_with_no_api_call([RENAMED_MESSAGE.format(lab="renamed-lab")])

    def test_topology_that_cannot_be_read_fails_with_no_api_call(self):
        cases = {
            "not valid YAML": "name: [build-lab",
            "a list": "- build-lab\n",
            "no topology.nodes": "name: build-lab\ntopology: {}\n",
            "a node with no image": "name: build-lab\ntopology:\n  nodes:\n    a: {kind: linux}\n",
            "nan": "name: build-lab\ntopology:\n  nodes:\n    a: {image: alpine}\nvalue: .nan\n",
        }
        for case, topology_yaml in cases.items():
            with self.subTest(case=case):
                Lab.objects.filter(pk=self.lab.pk).update(topology_yaml=topology_yaml)
                self._assert_fails_with_no_api_call([BAD_TOPOLOGY_MESSAGE.format(lab=self.lab)])

    def test_missing_status_fails_with_no_api_call(self):
        lab_type = ContentType.objects.get_for_model(Lab)
        for name in (LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED):
            with self.subTest(status=name):
                status = Status.objects.get(name=name)
                status.content_types.remove(lab_type)
                self._assert_fails_with_no_api_call([STATUS_MISSING_MESSAGE.format(status=name)])
                status.content_types.add(lab_type)

    def test_preflight_api_error_keeps_status(self):
        error = ClabApiError("Host clab01: cannot reach https://clab01: ConnectionError.")
        self.client_mock.list_images.side_effect = error
        before = self._snapshot()
        self._assert_fails_with([error.message])
        self.client_mock.deploy.assert_not_called()
        self.assertEqual(self._snapshot(), before)

    def test_unreadable_image_list_keeps_status(self):
        self.client_mock.list_images.return_value = {"images": "x"}
        before = self._snapshot()
        self._assert_fails_with([IMAGE_LIST_MESSAGE.format(host=self.lab.host)])
        self.client_mock.deploy.assert_not_called()
        self.assertEqual(self._snapshot(), before)

    def test_server_error_sets_failed(self):
        error = api_error(500, "x" * 600)
        self.client_mock.deploy.side_effect = error
        self._assert_fails_with([error_text(error)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.FAILED)
        self.client_mock.inspect.assert_not_called()

    def test_deploy_timeout_sets_failed_with_unknown_state(self):
        error = ClabApiError(
            "Host clab01: POST /api/v1/labs was sent, but no response came in 660 seconds. "
            "The lab state is unknown. Run Inspect.",
            timed_out=True,
        )
        self.client_mock.deploy.side_effect = error
        self._assert_fails_with([error.message])
        self.assertEqual(self._lab().status.name, LabStatusChoices.FAILED)

    def test_conflict_and_other_errors_keep_status(self):
        conflict = api_error(409, "Lab 'build-lab' already exists. Use 'reconfigure=true' to overwrite.")
        cases = {
            "409": (conflict, [conflict.message, CONFLICT_MESSAGE.format(host=self.lab.host, lab=self.lab)]),
            "400": (api_error(400), [api_error(400).message]),
            "403": (api_error(403), [api_error(403).message]),
            "connection error": (ClabApiError("Host clab01: cannot reach x: ConnectionError."), None),
            "no call": (ClabApiError("Host clab01: the topology of lab 'build-lab' is not valid YAML."), None),
        }
        for case, (error, messages) in cases.items():
            with self.subTest(case=case):
                self.client_mock.deploy.side_effect = error
                before = self._snapshot()
                self._assert_fails_with(messages or [error.message])
                self.assertEqual(self._snapshot(), before)

    def test_failed_write_skips_a_changed_status(self):
        deployed = Status.objects.get(name=LabStatusChoices.DEPLOYED)

        def deploy_then_fail(*_args, **_kwargs):
            Lab.objects.filter(pk=self.lab.pk).update(status=deployed)
            raise api_error(500)

        self.client_mock.deploy.side_effect = deploy_then_fail
        self._assert_fails_with([api_error(500).message, STATUS_CHANGED_MESSAGE.format(lab=self.lab)])
        self.assertEqual(self._lab().status.name, LabStatusChoices.DEPLOYED)

    def test_soft_time_limit_paths(self):
        cases = (
            ("deploy", DeployTimeLimit, LabStatusChoices.FAILED),
            ("list_images", SoftTimeLimitExceeded, LabStatusChoices.BUILT),
            ("inspect", SoftTimeLimitExceeded, LabStatusChoices.DEPLOYED),
        )
        for method_name, raised_class, status in cases:
            with self.subTest(method=method_name):
                self._set_status(LabStatusChoices.BUILT)
                self.client_mock.reset_mock()
                getattr(self.client_mock, method_name).side_effect = SoftTimeLimitExceeded()
                with self.assertRaises(raised_class):
                    deploy_lab(self.lab)
                getattr(self.client_mock, method_name).side_effect = None
                self.assertEqual(self._lab().status.name, status)
                self.client_mock.close.assert_called_once_with()
        self.assertEqual(set(self._node_state().values()), {("", "")})

    def test_deleted_lab_fails_with_message(self):
        lab, host = self._lab(), self._lab().host

        def delete_then_fail(*_args, **_kwargs):
            Lab.objects.filter(pk=lab.pk).delete()
            raise api_error(500)

        self.client_mock.deploy.side_effect = delete_then_fail
        self._assert_fails_with([LAB_DELETED_MESSAGE.format(lab=lab, host=host)])
        self.assertFalse(Lab.objects.filter(pk=lab.pk).exists())
