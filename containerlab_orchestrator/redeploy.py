"""Destroy the lab of a Nautobot lab on its host, then deploy its stored topology again."""

from django.core.exceptions import ValidationError
from nautobot.extras.models import Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient
from containerlab_orchestrator.deploy import (
    DeployResult,
    deploy_step,
    inspect_step,
    lab_status,
    lab_statuses,
    preflight,
    stored_nodes,
    write_deployed,
)
from containerlab_orchestrator.destroy import DestroyStatuses, destroy_on_host
from containerlab_orchestrator.models import Lab

REDEPLOY_STATUSES = frozenset({LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED})
"""The lab statuses that a redeploy starts from."""

REDEPLOY_STATUS_MESSAGE = "Lab {lab} is {status}. Only a Deployed or Failed lab can redeploy."
"""The error for a lab whose status cannot redeploy."""

HALF_DONE_MESSAGE = (
    "The job removed lab {lab} from host {host} before the deploy failed. Run Deploy Lab after you fix the cause."
)
"""The message for a deploy call that failed after the destroy removed the lab."""


def redeploy_lab(lab):
    """Destroy the lab on its host with its lab directory, deploy the stored topology, and write the result.

    The checks and the image check run before any call that changes the host. The deploy sends the topology YAML that
    the job read at its start.

    Raises:
        ValidationError: If the lab cannot redeploy, an image is missing, or a client call fails.
        DestroyTimeLimit: If the Celery soft time limit stops the destroy call.
        DeployTimeLimit: If the Celery soft time limit stops the deploy call.
    """
    current = Lab.objects.select_related("status", "host").get(pk=lab.pk)
    if current.status.name not in REDEPLOY_STATUSES:
        raise ValidationError(REDEPLOY_STATUS_MESSAGE.format(lab=current, status=current.status))
    deploy_statuses = lab_statuses()
    destroyed = lab_status(Status.objects.get_for_model(Lab), LabStatusChoices.DESTROYED)
    nodes = stored_nodes(current)
    client = ClabApiClient(current.host)
    try:
        return _redeploy_on_host(client, current, nodes, deploy_statuses, destroyed)
    finally:
        client.close()


def _redeploy_on_host(client, lab, nodes, deploy_statuses, destroyed):
    """Check the images, destroy the lab, deploy it again, read its node state, and return the result."""
    preflight(client, lab, nodes)
    removed = destroy_on_host(client, lab, DestroyStatuses(destroyed, deploy_statuses.failed), cleanup=True)
    lab.status = removed.lab.status
    try:
        deploy_step(client, lab, deploy_statuses, replace_existing=False)
    except ValidationError as error:
        raise ValidationError([*error.messages, HALF_DONE_MESSAGE.format(lab=lab, host=lab.host)]) from error
    containers, inspect_warnings = inspect_step(client, lab, deploy_statuses)
    warnings = removed.warnings + inspect_warnings
    saved, state_count = write_deployed(lab, deploy_statuses.deployed, containers, warnings)
    return DeployResult(saved, len(nodes), state_count, warnings)
