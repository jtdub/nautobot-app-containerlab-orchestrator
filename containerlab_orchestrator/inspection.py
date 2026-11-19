"""Read the containers of a Nautobot lab on its host, and record the node state and the lab status."""

import enum
from http import HTTPStatus
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from nautobot.extras.models import Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.deploy import error_text, lab_status, lock_lab, write_node_state
from containerlab_orchestrator.destroy import DESTROY_STATUSES, NOT_FOUND_WARNING, PLAIN_NOT_FOUND
from containerlab_orchestrator.models import Lab

INSPECT_STATUSES = frozenset(
    {LabStatusChoices.BUILT, LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED, LabStatusChoices.DESTROYED}
)
"""The lab statuses that an inspect starts from."""

MATCH_STATUSES = frozenset({LabStatusChoices.BUILT, LabStatusChoices.DESTROYED})
"""The start statuses that need a strong match before a status write."""

INSPECT_STATUS_MESSAGE = "Lab {lab} is {status}. The job inspects only a Built, Deployed, Failed, or Destroyed lab."
"""The error for a lab whose status cannot inspect."""

HELPERS_NOT_FOUND = "Lab '{lab}' not found."
"""The server text of a 404 for a lab that has only internal helper containers on the host."""

HELPERS_WARNING = (
    "Host {host} has only helper containers of lab {lab}. Run Inspect Lab again. "
    "If the result does not change, run Destroy Lab."
)
"""The warning for an inspect that found only the internal helper containers of the lab."""

NOT_RUNNING_WARNING = "Lab {lab} has nodes with no running container: {nodes}."
"""The warning for node rows whose container is missing or does not run."""

NO_MATCH_WARNING = (
    "Host {host} runs a lab named {lab} that does not match the build of lab {lab}. The job did not change the status."
)
"""The warning for a container list that does not match the node rows of a Built or Destroyed lab."""

INSPECT_NOT_OWNED_MESSAGE = (
    "Host {host} has a lab named {lab} that another user of the host owns. The job did not read it. "
    "Check the Secrets Group of host {host}."
)
"""The error for an inspect that found a lab of another user of the host."""

INSPECT_RESULT_MESSAGE = "Host {host} returned an inspect result for lab {lab} that the job cannot read."
"""The error for an inspect result that is not in the expected shape."""

INSPECT_GUARD_MESSAGE = "Lab {lab} changed while the job ran, so the job wrote nothing. Run the job again."
"""The error for a lab row that changed after the job read it."""

INSPECT_DELETED_MESSAGE = "Lab {lab} was deleted while the job ran."
"""The error for a lab row that was deleted before the inspect write."""

INSPECT_TIME_LIMIT_MESSAGE = "Lab {lab}: the job time limit stopped the inspect. The lab did not change."
"""The message for an inspect that the Celery soft time limit stopped."""

RUNNING = "running"
"""The container state of a node that runs."""


class Found(enum.Enum):
    """What the inspect call found on the host."""

    LISTED = "listed"
    NONE_LEFT = "none left"
    HELPERS_LEFT = "helpers left"


class InspectStatuses(NamedTuple):
    """The statuses that an inspect sets."""

    deployed: Status
    failed: Status
    destroyed: Status


class InspectResult(NamedTuple):
    """The saved lab, the node and running counts, and the warnings of an inspect."""

    lab: Lab
    node_count: int
    running_count: int
    warnings: list


def inspect_lab(lab):
    """Read the containers of the lab on its host, and write the node rows, the stored result, and the status.

    Raises:
        ValidationError: If the lab cannot inspect, a client call fails, or the lab changed during the job.
    """
    current = Lab.objects.select_related("status", "host").get(pk=lab.pk)
    if current.status.name not in INSPECT_STATUSES:
        raise ValidationError(INSPECT_STATUS_MESSAGE.format(lab=current, status=current.status))
    statuses = inspect_statuses()
    client = ClabApiClient(current.host)
    try:
        client.login()
        return inspect_on_host(client, current, statuses)
    except ClabApiError as error:
        raise ValidationError(error_text(error)) from error
    finally:
        client.close()


def inspect_statuses():
    """Return the Deployed, Failed, and Destroyed statuses of labs.

    Raises:
        ValidationError: If labs cannot have one of the statuses.
    """
    available = Status.objects.get_for_model(Lab)
    return InspectStatuses(
        lab_status(available, LabStatusChoices.DEPLOYED),
        lab_status(available, LabStatusChoices.FAILED),
        lab_status(available, LabStatusChoices.DESTROYED),
    )


def inspect_on_host(client, lab, statuses):
    """Inspect the lab with a client that is logged in, and write the result."""
    containers, found, warnings = _read_containers(client, lab)
    return _write_inspect(lab, statuses, containers, found, warnings)


def _read_containers(client, lab):
    """Return the container list, what the call found, and the warnings of the inspect call.

    Raises:
        ValidationError: If the inspect call fails with an error that does not tell what the host has.
    """
    try:
        containers = client.inspect(lab.name)
    except ClabApiError as error:
        if error.status_code == HTTPStatus.NOT_FOUND:
            if error.server_message == PLAIN_NOT_FOUND.format(lab=lab.name):
                return [], Found.NONE_LEFT, [NOT_FOUND_WARNING.format(host=lab.host, lab=lab)]
            if error.server_message == HELPERS_NOT_FOUND.format(lab=lab.name):
                return [], Found.HELPERS_LEFT, [HELPERS_WARNING.format(host=lab.host, lab=lab)]
        raise ValidationError(error_text(error)) from error
    return containers, Found.LISTED if containers else Found.NONE_LEFT, []


def _write_inspect(lab, statuses, containers, found, warnings):
    """Write the node rows, the stored inspect result, and the status in one transaction on the locked lab row.

    Raises:
        ValidationError: If the lab row is gone, or its status or last update time changed after the job read it.
    """
    with transaction.atomic():
        locked = lock_lab(lab, INSPECT_DELETED_MESSAGE)
        if locked.status_id != lab.status_id or locked.last_updated != lab.last_updated:
            raise ValidationError(INSPECT_GUARD_MESSAGE.format(lab=lab))
        written = write_node_state(locked, locked.topology_yaml, containers, warnings)
        running = [row.node_name for row in written.rows if row.state == RUNNING]
        not_running = sorted({row.node_name for row in written.rows} - set(running))
        new_status = _new_status(lab, statuses, found, not_running)
        if new_status == statuses.failed and found is Found.LISTED and not_running:
            warnings.append(NOT_RUNNING_WARNING.format(lab=lab, nodes=", ".join(not_running)))
        if new_status:
            locked.status = new_status
        locked.save(update_fields=["status", "last_inspect", "last_updated"])
    return InspectResult(locked, len(written.rows), len(running), warnings)


def _new_status(lab, statuses, found, not_running):
    """Return the status that the inspect result sets, or None to keep the status."""
    if found is Found.HELPERS_LEFT:
        return statuses.failed
    if found is Found.NONE_LEFT:
        return statuses.destroyed if lab.status.name in DESTROY_STATUSES else None
    return statuses.failed if not_running else statuses.deployed
