"""Test data helpers for the containerlab_orchestrator tests."""

import importlib
import os
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.dcim.models import Device, DeviceType, Location, LocationType, Manufacturer, Platform
from nautobot.extras.models import DynamicGroup, Role, Secret, SecretsGroup, SecretsGroupAssociation, Status
from nautobot.users.models import ObjectPermission

from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.constants import REQUIRED_HTTP_SECRET_TYPES
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping

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


def grant_permission(user, model, action, constraints=None):
    """Give the user an object permission for the action on the model, limited by the constraints."""
    permission = ObjectPermission.objects.create(
        name=f"{user} {action} {model._meta.model_name}", actions=[action], constraints=constraints
    )
    permission.users.add(user)
    permission.object_types.add(ContentType.objects.get_for_model(model))
    return permission


def create_lab_statuses():
    """Create the lab statuses of migration 0005 again, for a test that runs after a database flush."""
    importlib.import_module("containerlab_orchestrator.migrations.0005_lab_statuses").add_lab_statuses(apps, None)


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


def create_interface_mappings(node_mappings):
    """Return saved interface rows: three on the Arista mapping and one on the linux Platform mapping."""
    arista, _, linux = node_mappings
    pairs = (
        (arista, "GigabitEthernet0/1", "eth1"),
        (arista, "GigabitEthernet0/2", "eth2"),
        (arista, "GigabitEthernet0/10", "eth10"),
        (linux, "ens3", "eth1"),
    )
    return [
        InterfaceMapping.objects.create(node_mapping=node_mapping, physical_name=physical, container_name=container)
        for node_mapping, physical, container in pairs
    ]


def create_lab_devices():
    """Return the saved devices leaf1, leaf2, and spine1, and the Dynamic Groups leaf_group and spine_group."""
    device_content_type = ContentType.objects.get_for_model(Device)
    location_type = LocationType.objects.create(name="Lab site type")
    location_type.content_types.add(device_content_type)
    active = Status.objects.get_for_model(Device).get(name="Active")
    location = Location.objects.create(name="Lab site", location_type=location_type, status=active)
    roles = {}
    for role_name in ("leaf", "spine"):
        roles[role_name] = Role.objects.create(name=role_name)
        roles[role_name].content_types.add(device_content_type)
    device_type = DeviceType.objects.create(
        manufacturer=Manufacturer.objects.create(name="Lab vendor"), model="Lab switch"
    )
    lab_devices = {
        name: Device.objects.create(
            name=name, device_type=device_type, location=location, status=active, role=roles[role_name]
        )
        for name, role_name in (("leaf1", "leaf"), ("leaf2", "leaf"), ("spine1", "spine"))
    }
    for role_name in ("leaf", "spine"):
        lab_devices[f"{role_name}_group"] = DynamicGroup.objects.create(
            name=f"{role_name.capitalize()} devices",
            content_type=device_content_type,
            filter={"role": [role_name]},
        )
    return lab_devices


def create_labs(hosts, lab_devices):
    """Return three saved labs: lab-a and lab-b on clab01, and lab-a on clab02, each with a different owner."""
    statuses = {status.name: status for status in Status.objects.get_for_model(Lab)}
    alice, bob, carol = (get_user_model().objects.create_user(username=name) for name in ("alice", "bob", "carol"))
    lab_a = Lab.objects.create(
        name="lab-a",
        host=hosts[0],
        owner=alice,
        status=statuses[LabStatusChoices.PLANNED],
        mgmt_subnet="172.20.21.0/24",
        device_filter={"role": ["leaf"]},
        config_source=NodeMappingConfigSourceChoices.NONE,
    )
    lab_a.devices.set([lab_devices["spine1"]])
    lab_b = Lab.objects.create(
        name="lab-b",
        host=hosts[0],
        owner=bob,
        status=statuses[LabStatusChoices.BUILT],
        mgmt_subnet="172.20.22.0/28",
        dynamic_group=lab_devices["leaf_group"],
        config_source=NodeMappingConfigSourceChoices.JINJA2,
    )
    lab_b.devices.set([lab_devices["leaf2"]])
    lab_a_clab02 = Lab.objects.create(
        name="lab-a",
        host=hosts[1],
        owner=carol,
        status=statuses[LabStatusChoices.DEPLOYED],
        mgmt_subnet="10.10.0.0/30",
        dynamic_group=lab_devices["spine_group"],
        config_source=NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED,
    )
    return [lab_a, lab_b, lab_a_clab02]


def create_lab_nodes(labs, lab_devices):
    """Return five saved node rows: three on lab-a of clab01, one on lab-b, and one on lab-a of clab02."""
    lab_a, lab_b, lab_a_clab02 = labs
    ceos = ("ceos", "ceos:4.32.0F")
    rows = (
        (lab_a, "leaf1", "leaf1", ceos, "172.20.21.2", "running", "a1b2c3"),
        (lab_a, "spine1", "spine1", ceos, "172.20.21.3", "running", "d4e5f6"),
        (lab_a, "stub-wan1", None, ("linux", "alpine:latest"), "172.20.21.4", "running", "0a0b0c"),
        (lab_b, "leaf2", "leaf2", ceos, "172.20.22.2", "exited", "111111"),
        (
            lab_a_clab02,
            "leaf1",
            "leaf1",
            ("nokia_srlinux", "ghcr.io/nokia/srlinux:24.10"),
            "10.10.0.2",
            "running",
            "222222",
        ),
    )
    return [
        LabNode.objects.create(
            lab=lab,
            node_name=node_name,
            device=lab_devices.get(device_name),
            is_stub=device_name is None,
            kind=kind,
            image=image,
            mgmt_ipv4=mgmt_ipv4,
            state=state,
            container_id=container_id,
        )
        for lab, node_name, device_name, (kind, image), mgmt_ipv4, state, container_id in rows
    ]
