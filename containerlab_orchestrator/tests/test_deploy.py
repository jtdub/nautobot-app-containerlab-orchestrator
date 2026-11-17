"""Tests for the deploy service that runs the stored topology of a lab on its host."""

from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.deploy import (
    IMAGE_LIST_MESSAGE,
    INSPECT_WARNING,
    MISSING_IMAGES_MESSAGE,
    NEW_BUILD_WARNING,
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
