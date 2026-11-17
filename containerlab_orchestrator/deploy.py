"""Deploy the stored topology of a lab to its host, and record the status and the node state."""

import json
from contextlib import suppress
from http import HTTPStatus
from typing import NamedTuple

import yaml
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ValidationError
from django.db import transaction
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.extras.models import Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.models import Lab, LabNode

DEPLOY_STATUSES = frozenset({LabStatusChoices.BUILT, LabStatusChoices.DESTROYED, LabStatusChoices.FAILED})
"""The lab statuses that a deploy starts from."""

STATUS_MESSAGE = "Lab {lab} is {status}. Only a Built, Destroyed, or Failed lab can deploy."
"""The error for a lab whose status cannot deploy."""

NO_TOPOLOGY_MESSAGE = "Lab {lab} has no topology. Run the Build Lab Topology job first."
"""The error for a lab with no stored topology YAML."""

RENAMED_MESSAGE = "Lab {lab} changed its name after the build. Run the Build Lab Topology job again."
"""The error for a stored topology whose name is not the lab name."""

BAD_TOPOLOGY_MESSAGE = "Lab {lab} has a topology that the job cannot read. Run the Build Lab Topology job again."
"""The error for a stored topology that is not in the expected shape."""

STATUS_MISSING_MESSAGE = "The {status} status is not available for labs. Ask an administrator to add it."
"""The error for a status that a deploy sets and that labs cannot have."""

MISSING_IMAGES_MESSAGE = (
    "Host {host} does not have these images: {images}. Pull each image on the host, then run the job again."
)
"""The error for node images that the host does not have."""

IMAGE_LIST_MESSAGE = "Host {host} returned an image list that the job cannot read."
"""The error for an image list of the host that is not in the expected shape."""

CONFLICT_MESSAGE = (
    "Host {host} already has a lab named {lab}, or another operation of the lab runs. The job did not replace it. "
    "Wait for the other operation, or select Replace a lab that exists on the host."
)
"""The message for a deploy call that returned HTTP 409."""

INSPECT_WARNING = "Lab {lab} is deployed, but the job cannot read the node state: {error}"
"""The warning for an inspect call that failed after a deploy."""

NOT_A_LIST = "the response is not a list"

NEW_BUILD_WARNING = (
    "Lab {lab} got a new build during the deploy. The host runs the topology of the earlier build. Redeploy the lab."
)
"""The warning for a build that wrote a new topology during the deploy."""

STATUS_CHANGED_MESSAGE = "Lab {lab} changed its status during the deploy, so the job did not set Failed."
"""The message for a Failed write that the job skipped because the status changed."""

STATUS_GUARD_DEPLOYED_MESSAGE = (
    "Lab {lab} changed its status while the job ran, so the job did not set Deployed. The lab can run on host {host}."
)
"""The error for a Deployed write that the job skipped because the status changed."""

TIME_LIMIT_MESSAGE = (
    "Lab {lab}: the job time limit stopped the deploy. The lab state is unknown. "
    "Check the host before you run the job again."
)
"""The message for a deploy call that the Celery soft time limit stopped."""

LAB_DELETED_MESSAGE = "Lab {lab} was deleted during the deploy. The lab can still run on host {host}."
"""The error for a lab row that was deleted during the deploy."""

SERVER_TEXT_LIMIT = 500

DOCKER_REGISTRY = "docker.io/"
DOCKER_INDEX = "index.docker.io/"
DOCKER_LIBRARY = "library/"


class LabStatuses(NamedTuple):
    """The statuses that a deploy sets."""

    deployed: Status
    failed: Status


class DeployResult(NamedTuple):
    """The saved lab, the node and node state counts, and the warnings of a deploy."""

    lab: Lab
    node_count: int
    state_count: int
    warnings: list


class DeployTimeLimit(SoftTimeLimitExceeded):
    """The Celery soft time limit stopped the deploy call."""


def normalize_image(reference):
    """Return the image reference in the form `<registry>/<path>:<tag>` or `<registry>/<path>@<digest>`.

    A reference with a digest drops its tag. A reference with no tag and no digest gets the tag `latest`.
    """
    if reference.startswith(DOCKER_INDEX):
        reference = DOCKER_REGISTRY + reference.removeprefix(DOCKER_INDEX)
    name, _, digest = reference.partition("@")
    tag = "latest"
    colon = name.rfind(":")
    if colon > name.rfind("/"):
        name, tag = name[:colon], name[colon + 1 :]
    first, _, rest = name.partition("/")
    if not rest or ("." not in first and ":" not in first and first != "localhost"):
        name = DOCKER_REGISTRY + name
    path = name.removeprefix(DOCKER_REGISTRY)
    if name.startswith(DOCKER_REGISTRY) and "/" not in path:
        name = DOCKER_REGISTRY + DOCKER_LIBRARY + path
    return f"{name}@{digest}" if digest else f"{name}:{tag}"


def host_images(response, host):
    """Return the normalized tags and digests of the image list of the host.

    Raises:
        ValidationError: If the image list is not in the expected shape.
    """
    images = response.get("images") if isinstance(response, dict) else None
    if not isinstance(images, list) or not all(isinstance(image, dict) for image in images):
        raise ValidationError(IMAGE_LIST_MESSAGE.format(host=host))
    available = set()
    for image in images:
        for key in ("repoTags", "repoDigests"):
            values = image.get(key, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValidationError(IMAGE_LIST_MESSAGE.format(host=host))
            available.update(normalize_image(value) for value in values)
    return available


def missing_images(nodes, available):
    """Return the images of the topology nodes that are not available, each with its nodes, or an empty string.

    An entry shows the image text of its first node in name order. The entries are sorted by that text.
    """
    groups = {}
    for name in sorted(nodes):
        image = nodes[name]["image"]
        key = normalize_image(image)
        if key not in available:
            groups.setdefault(key, (image, []))[1].append(name)
    return "; ".join(f"{image} ({', '.join(names)})" for image, names in sorted(groups.values()))


def error_text(error):
    """Return the message of the ClabApiError, with the server text at its end cut to SERVER_TEXT_LIMIT."""
    server_text = error.server_message
    if server_text and len(server_text) > SERVER_TEXT_LIMIT and error.message.endswith(server_text):
        return error.message[: -len(server_text)] + server_text[:SERVER_TEXT_LIMIT]
    return error.message


def is_host_change(error):
    """Return True if the host can hold changes after the failed call."""
    status_code = error.status_code or 0
    return (
        error.timed_out
        or status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        or HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES
    )


def deploy_lab(lab, replace_existing=False):
    """Deploy the stored topology of a saved lab, and write the status and the node state.

    The checks run before any API call. A failure before the deploy call does not change the lab.

    Args:
        lab: The lab to deploy.
        replace_existing: True to replace a lab of the same name on the host.

    Raises:
        ValidationError: If the lab cannot deploy, an image is missing, or a client call fails.
        DeployTimeLimit: If the Celery soft time limit stops the deploy call.
    """
    current = Lab.objects.select_related("status", "host").get(pk=lab.pk)
    if current.status.name not in DEPLOY_STATUSES:
        raise ValidationError(STATUS_MESSAGE.format(lab=current, status=current.status))
    statuses = lab_statuses()
    nodes = stored_nodes(current)
    client = ClabApiClient(current.host)
    try:
        return _run_on_host(client, current, nodes, statuses, replace_existing)
    finally:
        client.close()


def lab_statuses():
    """Return the Deployed and Failed statuses of labs.

    Raises:
        ValidationError: If labs cannot have one of the statuses.
    """
    available = Status.objects.get_for_model(Lab)
    return LabStatuses(lab_status(available, LabStatusChoices.DEPLOYED), lab_status(available, LabStatusChoices.FAILED))


def lab_status(available, name):
    """Return the lab status of this name.

    Raises:
        ValidationError: If labs cannot have the status.
    """
    try:
        return available.get(name=name)
    except Status.DoesNotExist as error:
        raise ValidationError(STATUS_MISSING_MESSAGE.format(status=name)) from error


def stored_nodes(lab):
    """Return the `topology.nodes` mapping of the stored topology of the lab.

    Raises:
        ValidationError: If the lab has no topology, the topology has another name, or the job cannot read it.
    """
    if not lab.topology_yaml:
        raise ValidationError(NO_TOPOLOGY_MESSAGE.format(lab=lab))
    unreadable = ValidationError(BAD_TOPOLOGY_MESSAGE.format(lab=lab))
    try:
        topology = yaml.safe_load(lab.topology_yaml)
    except yaml.YAMLError as error:
        raise unreadable from error
    if not isinstance(topology, dict):
        raise unreadable
    if topology.get("name") != lab.name:
        raise ValidationError(RENAMED_MESSAGE.format(lab=lab))
    section = topology.get("topology")
    nodes = section.get("nodes") if isinstance(section, dict) else None
    if not isinstance(nodes, dict) or not all(_has_image(node) for node in nodes.values()):
        raise unreadable
    try:
        json.dumps(topology, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise unreadable from error
    return nodes


def _has_image(node):
    """Return True if the topology node is a mapping with an image text."""
    return isinstance(node, dict) and isinstance(node.get("image"), str) and bool(node["image"])


def _run_on_host(client, lab, nodes, statuses, replace_existing):
    """Check the images, deploy the lab, read its node state, and return the result."""
    preflight(client, lab, nodes)
    deploy_step(client, lab, statuses, replace_existing)
    containers, warnings = inspect_step(client, lab, statuses)
    saved, state_count = write_deployed(lab, statuses.deployed, containers, warnings)
    return DeployResult(saved, len(nodes), state_count, warnings)


def preflight(client, lab, nodes):
    """Stop the deploy when the host does not have an image of a node.

    Raises:
        ValidationError: If the image list call fails, the job cannot read the list, or an image is missing.
    """
    try:
        response = client.list_images()
    except ClabApiError as error:
        raise ValidationError(error_text(error)) from error
    missing = missing_images(nodes, host_images(response, lab.host))
    if missing:
        raise ValidationError(MISSING_IMAGES_MESSAGE.format(host=lab.host, images=missing))


def deploy_step(client, lab, statuses, replace_existing):
    """Call the deploy, and set Failed when the failed call can have changed the host.

    Raises:
        ValidationError: If the deploy call fails.
        DeployTimeLimit: If the Celery soft time limit stops the deploy call.
    """
    try:
        client.deploy(lab.name, lab.topology_yaml, reconfigure=replace_existing)
    except ClabApiError as error:
        messages = [error_text(error)]
        if is_host_change(error) and not write_failed(lab, statuses.failed):
            messages.append(STATUS_CHANGED_MESSAGE.format(lab=lab))
        if error.status_code == HTTPStatus.CONFLICT:
            messages.append(CONFLICT_MESSAGE.format(host=lab.host, lab=lab))
        raise ValidationError(messages) from error
    except SoftTimeLimitExceeded:
        with suppress(ValidationError):
            write_failed(lab, statuses.failed)
        raise DeployTimeLimit() from None


def inspect_step(client, lab, statuses):
    """Return the containers of the lab and the warnings of the inspect call.

    Raises:
        SoftTimeLimitExceeded: If the Celery soft time limit stops the call. The lab is Deployed first.
    """
    try:
        containers = client.inspect(lab.name)
    except ClabApiError as error:
        return [], [INSPECT_WARNING.format(lab=lab, error=error_text(error))]
    except SoftTimeLimitExceeded:
        with suppress(ValidationError):
            write_deployed(lab, statuses.deployed, [], [])
        raise
    if not isinstance(containers, list):
        return [], [INSPECT_WARNING.format(lab=lab, error=NOT_A_LIST)]
    return containers, []


def lock_lab(lab, deleted_message=LAB_DELETED_MESSAGE):
    """Return the lab row with a row lock.

    Args:
        lab: The lab that the job read at its start.
        deleted_message: The error text for a lab row that is gone. It can use `{lab}` and `{host}`.

    Raises:
        ValidationError: If the lab row is gone.
    """
    try:
        return Lab.objects.select_for_update().get(pk=lab.pk)
    except Lab.DoesNotExist as error:
        raise ValidationError(deleted_message.format(lab=lab, host=lab.host)) from error


def write_failed(lab, failed, deleted_message=LAB_DELETED_MESSAGE):
    """Set the Failed status, and return False if the status changed after the job read it.

    Args:
        lab: The lab that the job read at its start.
        failed: The Failed status.
        deleted_message: The error text for a lab row that is gone. It can use `{lab}` and `{host}`.
    """
    with transaction.atomic():
        locked = lock_lab(lab, deleted_message)
        if locked.status_id != lab.status_id:
            return False
        locked.status = failed
        locked.save(update_fields=["status", "last_updated"])
    return True


def write_deployed(lab, deployed, containers, warnings):
    """Write the node state of each row and the Deployed status in one transaction.

    A row with no container gets an empty state and container ID. A new build during the deploy adds a warning.

    Returns:
        The saved lab, and the count of rows that got a container.

    Raises:
        ValidationError: If the lab row is gone, or its status changed after the job read it.
    """
    with transaction.atomic():
        locked = lock_lab(lab)
        if locked.status_id != lab.status_id:
            raise ValidationError(STATUS_GUARD_DEPLOYED_MESSAGE.format(lab=lab, host=lab.host))
        if locked.built_at != lab.built_at:
            warnings.append(NEW_BUILD_WARNING.format(lab=lab))
        by_node = {
            container["nodeName"]: container
            for container in containers
            if isinstance(container, dict) and isinstance(container.get("nodeName"), str)
        }
        rows = list(locked.lab_nodes.all())
        for row in rows:
            container = by_node.get(row.node_name, {})
            row.state = _container_text(container.get("state"))
            row.container_id = _container_text(container.get("container_id"))
        LabNode.objects.bulk_update(rows, ["state", "container_id"])
        state_count = sum(row.node_name in by_node for row in rows)
        locked.status = deployed
        locked.save(update_fields=["status", "last_updated"])
    return locked, state_count


def _container_text(value):
    """Return the inspect value cut to CHARFIELD_MAX_LENGTH, or an empty string for a value that is not a text."""
    return value[:CHARFIELD_MAX_LENGTH] if isinstance(value, str) else ""
