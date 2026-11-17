"""Tests for the deploy service that runs the stored topology of a lab on its host."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from containerlab_orchestrator.clab_client import ClabApiError
from containerlab_orchestrator.deploy import (
    IMAGE_LIST_MESSAGE,
    error_text,
    host_images,
    missing_images,
    normalize_image,
)

DIGEST = "sha256:" + "a1" * 32
SERVER_ERROR_PREFIX = "Host clab01: POST /api/v1/labs returned HTTP 500: "


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
