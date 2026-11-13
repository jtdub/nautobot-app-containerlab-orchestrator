"""Test data helpers for the containerlab_orchestrator tests."""

import os
from unittest import mock

from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.dcim.models import DeviceType, Manufacturer, Platform
from nautobot.extras.models import Secret, SecretsGroup, SecretsGroupAssociation

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.constants import REQUIRED_HTTP_SECRET_TYPES
from containerlab_orchestrator.models import ContainerlabHost, NodeMapping

SECRET_VALUE = "clab-secret-value-do-not-show"
"""A secret value that the REST API and GraphQL must never return."""


def secret_variable_name(secret_type):
    """Return the environment variable that the test secret of this type reads."""
    return f"CLAB_{secret_type.upper()}"


def patch_secret_variables():
    """Return a patch that sets each HTTP(S) test secret variable to SECRET_VALUE."""
    variables = {secret_variable_name(secret_type): SECRET_VALUE for secret_type in REQUIRED_HTTP_SECRET_TYPES}
    return mock.patch.dict(os.environ, variables)


def create_http_secrets_group(name, username=True, password=True):
    """Return a new Secrets Group with the requested HTTP(S) username and password secrets.

    Each secret reads an environment variable, so the tests never store a secret value.
    """
    secrets_group = SecretsGroup.objects.create(name=name)
    wanted_types = []
    if username:
        wanted_types.append(SecretsGroupSecretTypeChoices.TYPE_USERNAME)
    if password:
        wanted_types.append(SecretsGroupSecretTypeChoices.TYPE_PASSWORD)
    for secret_type in wanted_types:
        secret = Secret.objects.create(
            name=f"{name} {secret_type}",
            provider="environment-variable",
            parameters={"variable": secret_variable_name(secret_type)},
        )
        SecretsGroupAssociation.objects.create(
            secrets_group=secrets_group,
            secret=secret,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=secret_type,
        )
    return secrets_group


def create_containerlab_hosts():
    """Return three saved hosts, each with its own complete Secrets Group."""
    hosts = []
    for number in range(1, 4):
        hosts.append(
            ContainerlabHost.objects.create(
                name=f"clab{number:02d}",
                remote_url=f"https://clab{number:02d}.example.com:8080",
                verify_ssl=number != 3,
                secrets_group=create_http_secrets_group(f"Containerlab credentials {number}"),
                description=f"Lab server {number}",
            )
        )
    return hosts


def create_node_mappings():
    """Return three saved mappings: two for a Device Type and one for a Platform."""
    arista = DeviceType.objects.create(manufacturer=Manufacturer.objects.create(name="Arista"), model="DCS-7280")
    nokia = DeviceType.objects.create(manufacturer=Manufacturer.objects.create(name="Nokia"), model="7220 IXR-D3")
    linux = Platform.objects.create(name="linux")
    return [
        NodeMapping.objects.create(
            device_type=arista,
            kind="ceos",
            image="ceos:4.32.0F",
            env={"CLAB_MGMT_VRF": "MGMT"},
            cpu=2,
            memory="2Gb",
        ),
        NodeMapping.objects.create(
            device_type=nokia,
            kind="nokia_srlinux",
            image="ghcr.io/nokia/srlinux:24.10",
            node_type="ixrd3",
            config_source=NodeMappingConfigSourceChoices.NONE,
        ),
        NodeMapping.objects.create(
            platform=linux,
            kind="linux",
            image="alpine:latest",
            config_source=NodeMappingConfigSourceChoices.JINJA2,
            config_template="hostname {{ device.name }}",
        ),
    ]
