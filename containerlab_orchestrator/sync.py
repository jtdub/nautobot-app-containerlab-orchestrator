"""Inspect each Deployed or Failed lab in scope on its host, with one login for each host."""

import enum
from datetime import timedelta
from itertools import groupby
from operator import attrgetter
from typing import NamedTuple

from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ValidationError
from django.utils import timezone
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.extras.models import JobResult

from containerlab_orchestrator.clab_client import ClabApiClient, ClabApiError
from containerlab_orchestrator.deploy import error_text, stored_nodes
from containerlab_orchestrator.destroy import DESTROY_STATUSES
from containerlab_orchestrator.inspection import Found, InspectResult, inspect_statuses, read_containers, write_inspect
from containerlab_orchestrator.models import Lab

SYNC_STATUSES = DESTROY_STATUSES
"""The lab statuses that a sync inspects."""

LAB_JOBS_MODULE = "containerlab_orchestrator.jobs"
"""The module of the lab jobs whose results make a lab busy."""

BUSY_RESULT_AGE = timedelta(hours=6)
"""The age after which a result that is not ready no longer makes a lab busy."""

SYNC_FAILED_LINE = "Lab {lab} on host {host}: {error}"
"""The failure line of a lab whose sync got an error."""

SYNC_ERROR_LINE = (
    "Lab {lab} on host {host}: the job got an unexpected error ({error_type}). The job continued with the next lab."
)
"""The failure line of a lab whose sync got an unexpected error. It names only the type of the error."""

SYNC_HOST_SKIPPED_MESSAGE = (
    "Lab {lab} on host {host}: the host gave no response to an earlier call of this job, so the job did not sync "
    "the lab."
)
"""The failure line of a lab after a call to its host that got no response."""

SYNC_BUSY_WARNING = (
    "Lab {lab} on host {host} has a lab job that runs, so the job did not sync the lab. "
    "Run Inspect Lab after that job ends."
)
"""The skip warning of a lab that has a lab job that runs."""

SYNC_HOST_ALL_GONE_MESSAGE = (
    "Lab {lab} on host {host}: the host reported no lab for each of its {count} labs, so the job did not change "
    "them. Check the host, then run Inspect Lab."
)
"""The failure line of each lab of a host that reported no lab for each lab that the job read."""

SYNC_CHANGED_WARNING = (
    "Lab {lab} on host {host} changed after the job started, so the job did not sync the lab. Run Inspect Lab."
)
"""The skip warning of a lab whose status or host changed after the job read the list."""

SYNC_DELETED_WARNING = "Lab {lab} on host {host}: the lab row is gone. The job did not sync the lab."
"""The skip warning of a lab whose row is gone after the job read the list."""

SYNC_TIME_LIMIT_MESSAGE = (
    "Lab {lab} on host {host}: the job time limit stopped the job before it logged a result for this lab. "
    "Run Inspect Lab to check the lab."
)
"""The failure line of a lab with no result line when the soft time limit stops the job."""


class SyncKind(enum.Enum):
    """The outcome of the sync of one lab."""

    CHANGED = "changed"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


class SyncOutcome(NamedTuple):
    """The lab, the kind of outcome, the skip or failure line, and the inspect result of a lab that synced."""

    lab: Lab
    kind: SyncKind
    line: str
    result: InspectResult | None


def sync_targets(user):
    """Return the Deployed and Failed labs that the user can view and change, in the order of host name and name."""
    return list(
        Lab.objects.restrict(user, "change")
        .filter(pk__in=Lab.objects.restrict(user, "view").values("pk"), status__name__in=SYNC_STATUSES)
        .select_related("status", "host")
        .order_by("host__name", "name")
    )


def sync_labs(labs):
    """Inspect the labs host by host, and yield one SyncOutcome for each lab.

    The labs must be in host order. Each host gets one login, then the job reads each lab of the host, then writes
    each lab that it read.

    Raises:
        ValidationError: If labs cannot have the Deployed, Failed, or Destroyed status. No call starts first.
    """
    statuses = inspect_statuses()
    for _, group in groupby(labs, key=attrgetter("host_id")):
        yield from _sync_host(list(group), statuses)


def _sync_host(labs, statuses):
    """Yield one SyncOutcome for each lab of one host, and close the client of the host at the end."""
    client = ClabApiClient(labs[0].host)
    try:
        _, error = _guarded(client.login)
        if error:
            for lab in labs:
                yield _error_outcome(lab, error)
            return
        no_response = False
        reads = []
        for lab in labs:
            if no_response:
                yield _outcome(lab, SyncKind.FAILED, SYNC_HOST_SKIPPED_MESSAGE)
                continue
            outcome, read, no_response = _read_lab(client, lab)
            if outcome:
                yield outcome
            else:
                reads.append(read)
        if _host_all_gone(reads):
            for current, _ in reads:
                yield _outcome(current, SyncKind.FAILED, SYNC_HOST_ALL_GONE_MESSAGE, count=len(reads))
            return
        for current, read in reads:
            yield _write_lab(current, statuses, read)
    finally:
        client.close()


def _read_lab(client, lab):
    """Read the lab row again and the containers of the lab on its host.

    Returns:
        The outcome of a skip or a failure, else None and the current lab with its InspectRead. The last value is
        True only for a failure whose call got no response.
    """
    current = Lab.objects.select_related("status", "host").filter(pk=lab.pk).first()
    if current is None:
        return _outcome(lab, SyncKind.SKIPPED, SYNC_DELETED_WARNING), None, False
    if current.status.name not in SYNC_STATUSES or current.host_id != lab.host_id:
        return _outcome(lab, SyncKind.SKIPPED, SYNC_CHANGED_WARNING), None, False
    if _is_busy(current):
        return _outcome(lab, SyncKind.SKIPPED, SYNC_BUSY_WARNING), None, False
    read, error = _guarded(_read_containers, client, current)
    if error:
        return _error_outcome(current, error), None, _no_response(error)
    return None, (current, read), False


def _host_all_gone(reads):
    """Return True if the job read two labs or more of the host, and the host reported no lab for each one."""
    return len(reads) >= 2 and all(read.found is Found.NONE_LEFT for _, read in reads)


def _is_busy(lab):
    """Return True if a lab job of the lab has a result that is not ready and is newer than BUSY_RESULT_AGE."""
    task_kwargs = JobResult.objects.filter(
        job_model__module_name=LAB_JOBS_MODULE,
        status__in=JobResultStatusChoices.UNREADY_STATES,
        date_created__gte=timezone.now() - BUSY_RESULT_AGE,
    ).values_list("task_kwargs", flat=True)
    lab_id = str(lab.pk)
    return any(_job_lab(kwargs) == lab_id for kwargs in task_kwargs)


def _job_lab(task_kwargs):
    """Return the lab input of the job kwargs as text, or None.

    The input can be a text, a UUID, or the dict form of a model instance with an `id` key.
    """
    value = task_kwargs.get("lab") if isinstance(task_kwargs, dict) else None
    if isinstance(value, dict):
        value = value.get("id")
    return None if value is None else str(value)


def _read_containers(client, lab):
    """Check the stored topology of the lab, then return the InspectRead of the lab on its host."""
    stored_nodes(lab)
    return read_containers(client, lab)


def _write_lab(current, statuses, read):
    """Write the inspect result of the lab, and return its SyncOutcome."""
    result, error = _guarded(write_inspect, current, statuses, read, skip_unchanged=True)
    if error:
        return _error_outcome(current, error)
    return SyncOutcome(current, SyncKind.CHANGED if result.saved else SyncKind.UNCHANGED, "", result)


def _guarded(call, *args, **kwargs):
    """Return the value of the call and None, or None and the error that the call raised.

    Raises:
        SoftTimeLimitExceeded: If the soft time limit stops the call.
    """
    try:
        return call(*args, **kwargs), None
    except SoftTimeLimitExceeded:
        raise
    except Exception as error:  # pylint: disable=broad-exception-caught
        return None, error


def _error_outcome(lab, error):
    """Return the failure outcome of the lab for the error."""
    if isinstance(error, ClabApiError):
        return _failed(lab, [error_text(error)])
    if isinstance(error, ValidationError):
        return _failed(lab, error.messages)
    return _unexpected(lab, error)


def _no_response(error):
    """Return True if the error comes from a client call that got no response."""
    cause = error.__cause__
    return isinstance(cause, ClabApiError) and (cause.timed_out or cause.unreachable)


def _failed(lab, messages):
    """Return the failure outcome of the lab with the messages on one line."""
    return _outcome(lab, SyncKind.FAILED, SYNC_FAILED_LINE, error=" ".join(messages))


def _unexpected(lab, error):
    """Return the failure outcome of the lab for an unexpected error, with only the type name of the error."""
    return _outcome(lab, SyncKind.FAILED, SYNC_ERROR_LINE, error_type=type(error).__name__)


def _outcome(lab, kind, template, **values):
    """Return the outcome of the lab with the line of the template."""
    return SyncOutcome(lab, kind, template.format(lab=lab, host=lab.host, **values), None)
