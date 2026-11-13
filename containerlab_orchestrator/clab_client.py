"""Client for the clab-api-server REST API."""

import json
import re
from urllib.parse import quote

import requests
import yaml
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.apps.config import get_app_settings_or_config
from nautobot.apps.exceptions import SecretError

APP_NAME = "containerlab_orchestrator"
"""The name of this app in PLUGINS_CONFIG."""

CONNECT_TIMEOUT = 10
"""Seconds to wait for the TCP and TLS connection to the server."""

ERROR_BODY_LIMIT = 200
"""Characters of a response body that an error message shows."""

LAB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
"""The lab name rule of clab-api-server."""


class ClabApiError(Exception):
    """A clab-api-server call failed.

    Args:
        message: The text for the job log. It never holds a secret value.
        status_code: The HTTP status, or None when no response came.
        server_message: The JSON `error` text of the server, or None.
        timed_out: True when the server got the request but sent no response in time.
    """

    def __init__(self, message, status_code=None, server_message=None, timed_out=False):
        """Keep the message and the details of the failure."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.server_message = server_message
        self.timed_out = timed_out


class ClabApiClient:
    """Call the clab-api-server of one ContainerlabHost.

    One client for each job. Not thread-safe.
    """

    def __init__(self, host):
        """Prepare a session that uses the TLS, proxy, and timeout settings of the host and of Nautobot."""
        self.host = host
        self.base_url = host.remote_url.rstrip("/")
        self.token = None
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = host.verify_ssl
        self.session.proxies = dict(settings.HTTP_PROXIES or {})
        self.timeout = (CONNECT_TIMEOUT, get_app_settings_or_config(APP_NAME, "request_timeout"))

    def close(self):
        """Close the HTTP session."""
        self.session.close()

    def login(self):
        """Log in with the HTTP(S) credentials of the host and keep the token.

        Raises:
            ClabApiError: If a secret is not available, or the server returns no token.
        """
        username = self._read_secret(SecretsGroupSecretTypeChoices.TYPE_USERNAME)
        password = self._read_secret(SecretsGroupSecretTypeChoices.TYPE_PASSWORD)
        response = self._send("POST", "/login", json={"username": username, "password": password})
        self._raise_for_status(response, "POST", "/login")
        body = self._json(response, "POST", "/login")
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ClabApiError(f"Host {self.host}: login returned no token.", status_code=response.status_code)
        self.token = token

    def list_images(self):
        """Return the container images on the host."""
        return self._request("GET", "/api/v1/images")

    def deploy(self, name, topology_yaml, reconfigure=False):
        """Deploy the lab from its topology YAML and return the server result.

        Args:
            name: The lab name. It must be equal to the `name` in the topology.
            topology_yaml: The containerlab topology as YAML text.
            reconfigure: True to destroy and deploy a lab that exists.
        """
        self._lab_path(name)
        topology = self._parse_topology(name, topology_yaml)
        return self._request(
            "POST",
            "/api/v1/labs",
            params={"reconfigure": "true" if reconfigure else "false"},
            json={"topologyContent": topology},
        )

    def inspect(self, name):
        """Return the details of the lab."""
        return self._request("GET", self._lab_path(name))

    def destroy(self, name):
        """Destroy the lab and return the server result."""
        return self._request("DELETE", self._lab_path(name))

    def _read_secret(self, secret_type):
        """Return the HTTP(S) secret of this type from the Secrets Group of the host."""
        try:
            return self.host.secrets_group.get_secret_value(
                SecretsGroupAccessTypeChoices.TYPE_HTTP, secret_type, obj=self.host
            )
        except (ObjectDoesNotExist, SecretError):
            pass
        raise ClabApiError(f"Host {self.host}: cannot read the HTTP(S) {secret_type} secret.")

    def _auth_headers(self):
        """Return the header that sends the current token."""
        return {"Authorization": f"Bearer {self.token}"}

    def _send(self, method, path, **kwargs):
        """Send one request and return the response.

        Raises:
            ClabApiError: If no response came. The error does not chain the transport exception.
        """
        failure = None
        try:
            return self.session.request(method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
        except requests.RequestException as error:
            failure = error
        if isinstance(failure, requests.ReadTimeout):
            raise ClabApiError(
                f"Host {self.host}: {method} {path} was sent, but no response came in {self.timeout[1]} seconds. "
                "The lab state is unknown. Run Inspect.",
                timed_out=True,
            )
        raise ClabApiError(f"Host {self.host}: cannot reach {self.base_url}: {type(failure).__name__}.")

    def _request(self, method, path, **kwargs):
        """Send an authenticated request and return the parsed JSON.

        Log in first when the client has no token. After a 401 with an earlier token, log in again and retry once.
        """
        logged_in_now = self.token is None
        if logged_in_now:
            self.login()
        response = self._send(method, path, headers=self._auth_headers(), **kwargs)
        if response.status_code == 401 and not logged_in_now:
            self.login()
            response = self._send(method, path, headers=self._auth_headers(), **kwargs)
        self._raise_for_status(response, method, path)
        return self._json(response, method, path)

    @staticmethod
    def _server_message(response):
        """Return the JSON `error` text of the response, or None."""
        try:
            body = response.json()
        except ValueError:
            return None
        error = body.get("error") if isinstance(body, dict) else None
        return error if isinstance(error, str) and error else None

    def _raise_for_status(self, response, method, path):
        """Raise ClabApiError when the response has an HTTP error status."""
        if response.ok:
            return
        server_message = self._server_message(response)
        detail = server_message or response.text[:ERROR_BODY_LIMIT]
        raise ClabApiError(
            f"Host {self.host}: {method} {path} returned HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
            server_message=server_message,
        )

    def _json(self, response, method, path):
        """Return the parsed JSON body of the response."""
        try:
            return response.json()
        except ValueError:
            pass
        raise ClabApiError(
            f"Host {self.host}: {method} {path} returned a response that is not JSON.",
            status_code=response.status_code,
        )

    def _lab_path(self, name):
        """Return the API path of the lab.

        Raises:
            ClabApiError: If the name does not match the lab name rule of the server.
        """
        if not isinstance(name, str) or not LAB_NAME_PATTERN.fullmatch(name):
            raise ClabApiError(
                f"Host {self.host}: the lab name {name!r} is not valid. Use 1 to 128 letters, digits, '_', or '-'."
            )
        return f"/api/v1/labs/{quote(name, safe='')}"

    def _parse_topology(self, name, topology_yaml):
        """Return the topology as a mapping that JSON can encode.

        Raises:
            ClabApiError: If the YAML is not valid, is not a mapping, has another name, or holds a value
                that JSON cannot encode.
        """
        topology = None
        valid_yaml = False
        try:
            topology = yaml.safe_load(topology_yaml)
            valid_yaml = True
        except yaml.YAMLError:
            pass
        if not valid_yaml:
            raise ClabApiError(f"Host {self.host}: the topology of lab {name!r} is not valid YAML.")
        if not isinstance(topology, dict):
            raise ClabApiError(f"Host {self.host}: the topology of lab {name!r} is not a YAML mapping.")
        if topology.get("name") != name:
            raise ClabApiError(
                f"Host {self.host}: the topology name {topology.get('name')!r} is not equal to the lab name {name!r}."
            )
        try:
            json.dumps(topology, allow_nan=False)
        except (TypeError, ValueError):
            pass
        else:
            return topology
        raise ClabApiError(f"Host {self.host}: the topology of lab {name!r} holds a value that JSON cannot encode.")
