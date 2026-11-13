"""Tests for the clab-api-server client."""

import json
import os
from unittest import mock

import requests
from nautobot.apps.choices import SecretsGroupSecretTypeChoices
from nautobot.apps.testing import TestCase

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
