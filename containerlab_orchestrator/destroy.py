"""Destroy the lab of a Nautobot lab on its host, and record the status and the node state."""

from http import HTTPStatus
from typing import NamedTuple

from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ValidationError
from django.db import transaction
from nautobot.extras.models import Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.deploy import error_text, is_host_change, lab_status, lock_lab, write_failed
from containerlab_orchestrator.models import Lab, LabNode

DESTROY_STATUSES = frozenset({LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED})
"""The lab statuses that a destroy starts from."""

DESTROY_STATUS_MESSAGE = "Lab {lab} is {status}. Only a Deployed or Failed lab can destroy."
"""The error for a lab whose status cannot destroy."""

PLAIN_NOT_FOUND = "lab '{lab}' not found"
"""The server text of a 404 for a lab that no container of the host has."""

NOT_FOUND_WARNING = "Host {host} has no lab named {lab}."
"""The warning for a destroy call that found no lab of this name on the host."""


class DestroyStatuses(NamedTuple):
    """The statuses that a destroy sets."""

    destroyed: Status
    failed: Status


class DestroyResult(NamedTuple):
    """The saved lab, the count of cleared node rows, and the warnings of a destroy."""

    lab: Lab
    node_count: int
    warnings: list


class DestroyTimeLimit(SoftTimeLimitExceeded):
    """The Celery soft time limit stopped the destroy call."""


def destroy_lab(lab, keep_lab_directory=False):
    """Destroy the lab on its host, clear the node state, and set Destroyed.

    Args:
        lab: The lab to destroy.
        keep_lab_directory: True to keep the lab directory on the host.

    Raises:
        ValidationError: If the lab cannot destroy, or a client call fails.
        DestroyTimeLimit: If the Celery soft time limit stops the destroy call.
    """
    current = Lab.objects.select_related("status", "host").get(pk=lab.pk)
    if current.status.name not in DESTROY_STATUSES:
        raise ValidationError(DESTROY_STATUS_MESSAGE.format(lab=current, status=current.status))
    statuses = destroy_statuses()
    client = ClabApiClient(current.host)
    try:
        try:
            client.login()
        except ClabApiError as error:
            raise ValidationError(error_text(error)) from error
        return destroy_on_host(client, current, statuses, cleanup=not keep_lab_directory)
    finally:
        client.close()


def destroy_statuses():
    """Return the Destroyed and Failed statuses of labs.

    Raises:
        ValidationError: If labs cannot have one of the statuses.
    """
    available = Status.objects.get_for_model(Lab)
    return DestroyStatuses(
        lab_status(available, LabStatusChoices.DESTROYED), lab_status(available, LabStatusChoices.FAILED)
    )


def destroy_on_host(client, lab, statuses, cleanup):
    """Call the destroy with a client that is logged in, and write the result.

    Raises:
        ValidationError: If the destroy call fails, or the job cannot write the result.
    """
    warnings = []
    try:
        client.destroy(lab.name, cleanup=cleanup)
    except ClabApiError as error:
        warnings = _destroy_error(lab, statuses, error)
    return DestroyResult(*_write_destroyed(lab, statuses.destroyed), warnings)


def _destroy_error(lab, statuses, error):
    """Return the warnings of a destroy error that removed the lab, or raise for each other error."""
    status_code = error.status_code or 0
    if status_code == HTTPStatus.NOT_FOUND and error.server_message == PLAIN_NOT_FOUND.format(lab=lab.name):
        return [NOT_FOUND_WARNING.format(host=lab.host, lab=lab)]
    if HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
        return []
    if is_host_change(error):
        write_failed(lab, statuses.failed)
    raise ValidationError(error_text(error)) from error


def _write_destroyed(lab, destroyed):
    """Clear the node state of each row and set the Destroyed status in one transaction.

    Returns:
        The saved lab, and the count of node rows.
    """
    with transaction.atomic():
        locked = lock_lab(lab)
        rows = list(locked.lab_nodes.all())
        for row in rows:
            row.state = ""
            row.container_id = ""
        LabNode.objects.bulk_update(rows, ["state", "container_id"])
        locked.status = destroyed
        locked.save(update_fields=["status", "last_updated"])
    return locked, len(rows)
