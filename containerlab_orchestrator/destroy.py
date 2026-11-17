"""Destroy the lab of a Nautobot lab on its host, and record the status and the node state."""

from contextlib import suppress
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

NOT_OWNED_NOT_FOUND = "lab '{lab}' not found or not owned by user"
"""The server text of a 404 for a lab that another user of the host owns."""

NOT_FOUND_WARNING = "Host {host} has no lab named {lab}."
"""The warning for a destroy call that found no lab of this name on the host."""

NOT_OWNED_MESSAGE = (
    "Host {host} has a lab named {lab} that another user of the host owns. The job did not destroy it. "
    "Check the Secrets Group of host {host}."
)
"""The error for a destroy call that found a lab of another user of the host."""

DESTROY_BUSY_MESSAGE = (
    "Host {host} did not destroy lab {lab}, because another operation of the lab runs. "
    "Wait for it to end, then run the job again."
)
"""The message for a destroy call that returned HTTP 409."""

DESTROY_STATUS_CHANGED_MESSAGE = "Lab {lab} changed its status during the destroy, so the job did not set Failed."
"""The message for a Failed write that the job skipped because the status changed."""

DESTROYED_GUARD_MESSAGE = (
    "Lab {lab} changed its status while the job ran, so the job did not set Destroyed. "
    "Host {host} no longer has lab {lab}."
)
"""The error for a Destroyed write that the job skipped because the status changed."""

DESTROYED_DELETED_MESSAGE = "Lab {lab} was deleted while the job ran. Host {host} no longer has lab {lab}."
"""The error for a lab row that was deleted before the Destroyed write."""

DESTROY_LAB_DELETED_MESSAGE = "Lab {lab} was deleted while the job ran. The lab can still run on host {host}."
"""The error for a lab row that was deleted before the Failed write of a destroy."""

DESTROY_TIME_LIMIT_MESSAGE = (
    "Lab {lab}: the job time limit stopped the destroy. The lab state is unknown. "
    "Check the host before you run the job again."
)
"""The message for a destroy call that the Celery soft time limit stopped."""


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
        DestroyTimeLimit: If the Celery soft time limit stops the destroy call. The lab is Failed first.
    """
    warnings = []
    try:
        client.destroy(lab.name, cleanup=cleanup)
    except ClabApiError as error:
        warnings = _destroy_error(lab, statuses, error)
    except SoftTimeLimitExceeded:
        with suppress(ValidationError):
            write_failed(lab, statuses.failed, DESTROY_LAB_DELETED_MESSAGE)
        raise DestroyTimeLimit() from None
    return DestroyResult(*_write_destroyed(lab, statuses.destroyed), warnings)


def _destroy_error(lab, statuses, error):
    """Return the warnings of a destroy error that removed the lab, or raise for each other error."""
    status_code = error.status_code or 0
    if status_code == HTTPStatus.NOT_FOUND and error.server_message == PLAIN_NOT_FOUND.format(lab=lab.name):
        return [NOT_FOUND_WARNING.format(host=lab.host, lab=lab)]
    if HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
        return []
    if status_code == HTTPStatus.NOT_FOUND and error.server_message == NOT_OWNED_NOT_FOUND.format(lab=lab.name):
        raise ValidationError(NOT_OWNED_MESSAGE.format(host=lab.host, lab=lab)) from error
    messages = [error_text(error)]
    if is_host_change(error) and not write_failed(lab, statuses.failed, DESTROY_LAB_DELETED_MESSAGE):
        messages.append(DESTROY_STATUS_CHANGED_MESSAGE.format(lab=lab))
    if status_code == HTTPStatus.CONFLICT:
        messages.append(DESTROY_BUSY_MESSAGE.format(host=lab.host, lab=lab))
    raise ValidationError(messages) from error


def _write_destroyed(lab, destroyed):
    """Clear the node state of each row and set the Destroyed status in one transaction.

    Returns:
        The saved lab, and the count of node rows.

    Raises:
        ValidationError: If the lab row is gone, or its status changed after the job read it.
    """
    with transaction.atomic():
        locked = lock_lab(lab, DESTROYED_DELETED_MESSAGE)
        if locked.status_id != lab.status_id:
            raise ValidationError(DESTROYED_GUARD_MESSAGE.format(lab=lab, host=lab.host))
        rows = list(locked.lab_nodes.all())
        for row in rows:
            row.state = ""
            row.container_id = ""
        LabNode.objects.bulk_update(rows, ["state", "container_id"])
        locked.status = destroyed
        locked.save(update_fields=["status", "last_updated"])
    return locked, len(rows)
