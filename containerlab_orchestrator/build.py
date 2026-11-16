"""Build the containerlab topology of a lab, and store it with the node rows of the lab."""

from typing import NamedTuple

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone
from nautobot.apps.config import get_app_settings_or_config
from nautobot.extras.models import Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.models import Lab, LabNode
from containerlab_orchestrator.selection import select_devices
from containerlab_orchestrator.topology import build_topology, to_yaml

TOO_MANY_DEVICES_MESSAGE = (
    "Lab {lab} selects {count} devices. The max_lab_devices setting allows {limit}. "
    "Select fewer devices, or ask an administrator to raise the limit."
)
"""The error for a lab that selects more devices than the max_lab_devices setting allows."""

DEVICE_LIMIT_SETTING_MESSAGE = "The max_lab_devices setting must be an integer of 1 or more."
"""The error for a max_lab_devices setting that is not valid."""

BUILT_STATUS_MESSAGE = "The Built status is not available for labs. Ask an administrator to add it."
"""The error for a build that sets the Built status when labs cannot have it."""

LAB_CHANGED_MESSAGE = "Lab {lab} changed during the build. Run the job again."
"""The error for a lab row that changed after the build read it."""

NODE_KEY_MESSAGE = "Node {node}: the topology has no {key}. Check the node mapping of the device."
"""The error for a topology node with no value for a key of its node row."""

NODE_KEYS = ("kind", "image", "mgmt-ipv4")
"""The topology node keys that each node row needs."""

KEPT_STATUSES = frozenset({LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED})
"""The lab statuses that a build keeps."""


class BuildResult(NamedTuple):
    """The saved lab, the node and link counts, and the builder warnings of a build."""

    lab: Lab
    node_count: int
    link_count: int
    warnings: list


def build_lab(lab, user=None):
    """Build the topology of a saved lab, and store the YAML, the build time, the status, and the node rows.

    The reads and the build run with no lock. The writes run in one transaction that locks the lab row, so a
    failed build changes nothing. With a user, the build uses only the devices and configs that the user can view.

    Raises:
        ValidationError: If the selection, the topology, a node row, or the status is not valid, or if the lab
            row changed during the build.
        ImproperlyConfigured: If an app setting is not valid.
        Lab.DoesNotExist: If the lab row is gone.
    """
    current = Lab.objects.get(pk=lab.pk)
    devices = select_devices(current, user)
    _check_device_count(current, devices)
    topology, warnings, node_devices = build_topology(current, devices, user)
    status = _next_status(current)
    rows, delete_pks = _checked_rows(current, topology["topology"]["nodes"], node_devices)
    saved = _write_build(current, to_yaml(topology), (rows, delete_pks), status)
    return BuildResult(saved, len(rows), len(topology["topology"].get("links", [])), warnings)


def _device_limit():
    """Return the max_lab_devices setting.

    Raises:
        ImproperlyConfigured: If the setting is not an integer of 1 or more.
    """
    limit = get_app_settings_or_config(APP_NAME, "max_lab_devices")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ImproperlyConfigured(DEVICE_LIMIT_SETTING_MESSAGE)
    return limit


def _check_device_count(lab, devices):
    """Reject a selection of more devices than the max_lab_devices setting allows."""
    limit = _device_limit()
    count = devices.count()
    if count > limit:
        raise ValidationError(TOO_MANY_DEVICES_MESSAGE.format(lab=lab, count=count, limit=limit))


def _next_status(lab):
    """Return the status of the lab after a build: its Deployed or Failed status, else Built.

    Raises:
        ValidationError: If the build sets Built, and labs cannot have the Built status.
    """
    if lab.status.name in KEPT_STATUSES:
        return lab.status
    try:
        return Status.objects.get_for_model(Lab).get(name=LabStatusChoices.BUILT)
    except Status.DoesNotExist as error:
        raise ValidationError(BUILT_STATUS_MESSAGE) from error


def _checked_rows(lab, nodes, node_devices):
    """Return the checked node rows of the topology nodes, and the pks of the rows of nodes that are gone.

    A row matches a node by its name, with a case-sensitive match, and keeps its pk, state, and container ID.
    """
    existing = {row.node_name: row for row in lab.lab_nodes.all()}
    rows = [
        _checked_row(existing.get(name) or LabNode(lab=lab, node_name=name), node, node_devices[name])
        for name, node in nodes.items()
    ]
    delete_pks = [row.pk for name, row in existing.items() if name not in nodes]
    return rows, delete_pks


def _checked_row(row, node, device):
    """Set the fields of the node row from the topology node and its device, then check the row.

    Raises:
        ValidationError: If the node has no value for a key of NODE_KEYS, or the row is not valid.
    """
    for key in NODE_KEYS:
        if not node.get(key):
            raise ValidationError(NODE_KEY_MESSAGE.format(node=row.node_name, key=key))
    row.device, row.is_stub = device, device is None
    row.kind, row.image, row.mgmt_ipv4 = node["kind"], node["image"], node["mgmt-ipv4"]
    try:
        row.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError as error:
        raise ValidationError([f"Node {row.node_name}: {message}" for message in error.messages]) from error
    return row


def _write_build(lab, yaml_text, row_plan, status):
    """Write the node rows and the job fields of the lab in one transaction, and return the saved lab.

    Raises:
        ValidationError: If the lab row changed after the build read it, or the lab is not valid.
    """
    rows, delete_pks = row_plan
    with transaction.atomic():
        locked = Lab.objects.select_for_update().get(pk=lab.pk)
        if locked.last_updated != lab.last_updated:
            raise ValidationError(LAB_CHANGED_MESSAGE.format(lab=lab))
        LabNode.objects.filter(pk__in=delete_pks).delete()
        for row in rows:
            row.save()
        locked.status, locked.topology_yaml, locked.built_at = status, yaml_text, timezone.now()
        locked.validated_save(job_fields=True)
    return locked
