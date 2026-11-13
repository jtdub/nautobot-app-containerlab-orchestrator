"""Tests for the clab-api-server client."""

import json
import os
import re
from unittest import mock

import requests
import yaml
from nautobot.apps.choices import SecretsGroupSecretTypeChoices
from nautobot.apps.testing import TestCase

from containerlab_orchestrator import clab_client
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.models import ContainerlabHost
from containerlab_orchestrator.tests.fixtures import (
    SECRET_VALUE,
    create_http_secrets_group,
    patch_secret_variables,
    secret_variable_name,
)

HOST_NAME = "clab01"
HOST_URL = "https://clab01.example.com:8080"
TOKEN = "token-value-do-not-show"
NEW_TOKEN = "new-token-value-do-not-show"
TOPOLOGY_YAML = "name: lab1\ntopology:\n  nodes:\n    srl1:\n      kind: nokia_srlinux\n"


def fake_response(status_code, body):
    """Return a response with this status code and body."""
    response = requests.Response()
    response.status_code = status_code
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    response._content = body.encode()  # pylint: disable=protected-access
    return response


def login_response(token=TOKEN):
    """Return a successful login response with this token."""
    return fake_response(200, {"token": token})


class ClabApiClientTestCase(TestCase):
    """Test ClabApiClient against a mocked transport."""

    def setUp(self):
        super().setUp()
        self.enterContext(patch_secret_variables())
        self.host = ContainerlabHost.objects.create(
            name=HOST_NAME,
            remote_url=HOST_URL,
            secrets_group=create_http_secrets_group("clab01 credentials"),
        )
        self.send = self.enterContext(mock.patch.object(requests.adapters.HTTPAdapter, "send", autospec=True))

    def sent_requests(self):
        """Return each PreparedRequest that the transport received."""
        return [call.args[1] for call in self.send.call_args_list]

    def logged_in_client(self):
        """Return a client that holds a token from an earlier call."""
        client = ClabApiClient(self.host)
        client.token = TOKEN
        return client

    def test_login_posts_secrets_group_credentials_and_keeps_token(self):
        self.send.return_value = login_response()
        client = ClabApiClient(self.host)
        client.login()
        request = self.sent_requests()[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, f"{HOST_URL}/login")
        self.assertEqual(json.loads(request.body), {"username": SECRET_VALUE, "password": SECRET_VALUE})
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(client.token, TOKEN)

    def test_login_without_token_in_body_raises_error(self):
        for body in ({}, {"token": ""}, {"token": 5}, []):
            with self.subTest(body=body):
                self.send.return_value = fake_response(200, body)
                client = ClabApiClient(self.host)
                with self.assertRaises(ClabApiError) as context:
                    client.login()
                self.assertIn("no token", context.exception.message)
                self.assertIsNone(client.token)

    def test_login_failure_status_raises_error_without_retry(self):
        for status_code in (401, 500):
            with self.subTest(status_code=status_code):
                self.send.reset_mock()
                self.send.return_value = fake_response(status_code, {"error": "login failed"})
                with self.assertRaises(ClabApiError) as context:
                    ClabApiClient(self.host).list_images()
                self.assertEqual(context.exception.status_code, status_code)
                self.assertEqual(self.send.call_count, 1)

    def test_login_body_that_is_not_json_raises_error(self):
        self.send.return_value = fake_response(200, "<html>login</html>")
        with self.assertRaises(ClabApiError) as context:
            ClabApiClient(self.host).login()
        self.assertIn("not JSON", context.exception.message)
        self.assertEqual(context.exception.status_code, 200)

    def test_missing_password_secret_raises_error_naming_host_and_type(self):
        self.host.secrets_group = create_http_secrets_group("no password", password=False)
        with self.assertRaises(ClabApiError) as context:
            ClabApiClient(self.host).login()
        self.assertIn(HOST_NAME, context.exception.message)
        self.assertIn(SecretsGroupSecretTypeChoices.TYPE_PASSWORD, context.exception.message)
        self.send.assert_not_called()

    def test_unreadable_secret_raises_error_without_secret_value(self):
        with mock.patch.dict(os.environ):
            del os.environ[secret_variable_name(SecretsGroupSecretTypeChoices.TYPE_PASSWORD)]
            with self.assertRaises(ClabApiError) as context:
                ClabApiClient(self.host).login()
        error = context.exception
        self.assertIn(HOST_NAME, error.message)
        self.assertIn(SecretsGroupSecretTypeChoices.TYPE_PASSWORD, error.message)
        self.assertNotIn(SECRET_VALUE, str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.send.assert_not_called()

    def test_first_call_logs_in_before_request(self):
        self.send.side_effect = [login_response(), fake_response(200, [])]
        self.assertEqual(ClabApiClient(self.host).list_images(), [])
        login, images = self.sent_requests()
        self.assertEqual(login.url, f"{HOST_URL}/login")
        self.assertEqual(images.url, f"{HOST_URL}/api/v1/images")
        self.assertEqual(images.headers["Authorization"], f"Bearer {TOKEN}")

    def test_methods_send_request_with_bearer_token_and_return_json(self):
        cases = (
            ("list_images", (), "GET", "/api/v1/images"),
            ("inspect", ("lab1",), "GET", "/api/v1/labs/lab1"),
            ("destroy", ("lab1",), "DELETE", "/api/v1/labs/lab1"),
        )
        for method_name, args, method, path in cases:
            with self.subTest(method=method_name):
                self.send.reset_mock()
                self.send.return_value = fake_response(200, {"result": method_name})
                result = getattr(self.logged_in_client(), method_name)(*args)
                self.assertEqual(result, {"result": method_name})
                request = self.sent_requests()[0]
                self.assertEqual(request.method, method)
                self.assertEqual(request.url, f"{HOST_URL}{path}")
                self.assertEqual(request.headers["Authorization"], f"Bearer {TOKEN}")
                self.assertIsNone(request.body)

    def test_deploy_posts_topology_content_with_reconfigure_false(self):
        self.send.return_value = fake_response(200, {"lab1": []})
        self.assertEqual(self.logged_in_client().deploy("lab1", TOPOLOGY_YAML), {"lab1": []})
        request = self.sent_requests()[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, f"{HOST_URL}/api/v1/labs?reconfigure=false")
        self.assertEqual(request.headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(json.loads(request.body), {"topologyContent": yaml.safe_load(TOPOLOGY_YAML)})

    def test_deploy_with_reconfigure_sends_reconfigure_true(self):
        self.send.return_value = fake_response(200, {})
        self.logged_in_client().deploy("lab1", TOPOLOGY_YAML, reconfigure=True)
        self.assertEqual(self.sent_requests()[0].url, f"{HOST_URL}/api/v1/labs?reconfigure=true")

    def test_deploy_with_bad_topology_raises_error_before_request(self):
        cases = {
            "not valid YAML": "name: [lab1",
            "a list": "- lab1\n",
            "a name mismatch": "name: lab2\n",
            "a date": "name: lab1\ncreated: 2026-01-01\n",
            "binary": "name: lab1\ndata: !!binary aGVsbG8=\n",
            "a set": "name: lab1\nitems: !!set {a: null}\n",
            "nan": "name: lab1\nvalue: .nan\n",
        }
        for case, topology_yaml in cases.items():
            with self.subTest(case=case):
                with self.assertRaises(ClabApiError) as context:
                    self.logged_in_client().deploy("lab1", topology_yaml)
                self.assertIsNone(context.exception.status_code)
                self.assertIn("lab1", context.exception.message)
        self.send.assert_not_called()

    def test_invalid_lab_name_raises_error_before_request(self):
        calls = {
            "inspect": lambda client, name: client.inspect(name),
            "destroy": lambda client, name: client.destroy(name),
            "deploy": lambda client, name: client.deploy(name, f"name: {name!r}\n"),
        }
        for method_name, call in calls.items():
            for name in ("", "a/b", "a b", "a?b", "a" * 129):
                with self.subTest(method=method_name, name=name):
                    with self.assertRaises(ClabApiError) as context:
                        call(ClabApiClient(self.host), name)
                    self.assertIn("is not valid", context.exception.message)
        self.send.assert_not_called()

    def test_lab_name_is_quoted_in_path(self):
        self.send.return_value = fake_response(200, {})
        with mock.patch.object(clab_client, "LAB_NAME_PATTERN", re.compile(r"^[a-z ]+$")):
            self.logged_in_client().inspect("a b")
        self.assertEqual(self.sent_requests()[0].url, f"{HOST_URL}/api/v1/labs/a%20b")

    def test_trailing_slash_in_remote_url_is_removed(self):
        self.host.remote_url = f"{HOST_URL}/"
        self.send.return_value = login_response()
        ClabApiClient(self.host).login()
        self.assertEqual(self.sent_requests()[0].url, "https://clab01.example.com:8080/login")
