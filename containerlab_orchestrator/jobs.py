"""Jobs of the containerlab_orchestrator app."""

from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ImproperlyConfigured, ValidationError
from nautobot.apps.jobs import BooleanVar, Job, ObjectVar, register_jobs

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.deploy import TIME_LIMIT_MESSAGE, DeployTimeLimit, deploy_lab
from containerlab_orchestrator.destroy import DESTROY_TIME_LIMIT_MESSAGE, DestroyTimeLimit, destroy_lab
from containerlab_orchestrator.inspection import INSPECT_TIME_LIMIT_MESSAGE, inspect_lab
from containerlab_orchestrator.models import Lab
from containerlab_orchestrator.redeploy import HALF_DONE_MESSAGE, redeploy_lab

name = "Containerlab Orchestrator"  # pylint: disable=invalid-name

NO_VIEW_MESSAGE = "You cannot {verb} this lab."
"""The error for a user who cannot view the lab. It names no lab, so it shows nothing of the lab."""

NOT_OWNER_MESSAGE = "Only the owner of lab {lab} or a superuser can {verb} it."
"""The error for a user who is not the owner of the lab and not a superuser."""

NO_CHANGE_MESSAGE = "You need the change permission on lab {lab} to {verb} it."
"""The error for a user who cannot change the lab."""

SUMMARY_MESSAGE = "Built lab {lab}: {nodes} nodes, {links} links, {warnings} warnings. Status: {status}."
"""The success line of a build."""

DEPLOY_SUMMARY = "Deployed lab {lab} on host {host}: {nodes} nodes, {states} node states, {warnings} warnings."
"""The success line of a deploy."""

REPLACE_HELP = (
    "WARNING: This removes the containers and the lab directory of the lab of this name on the host, "
    "also a lab that Nautobot did not deploy. Select it only when the lab on the host is yours."
)
"""The help text of the option that replaces a lab on the host."""

DESTROY_SUMMARY = "Destroyed lab {lab} on host {host}: {nodes} node rows cleared, {warnings} warnings."
"""The success line of a destroy."""

KEEP_DIRECTORY_HELP = (
    "Keep the lab directory on the host, with the saved configs, the cisco_iol NVRAM, and the nokia_sros saved "
    "config. Only a later Deploy Lab job with the replace option off uses it again."
)
"""The help text of the option that keeps the lab directory on the host."""

REDEPLOY_SUMMARY = "Redeployed lab {lab} on host {host}: {nodes} nodes, {states} node states, {warnings} warnings."
"""The success line of a redeploy."""

INSPECT_SUMMARY = (
    "Inspected lab {lab} on host {host}: {nodes} nodes, {running} running, {warnings} warnings. Status: {status}."
)
"""The success line of an inspect."""


def lab_access_message(lab, user, verb, owner_check=True):
    """Return the reason that the user cannot do the verb on the lab, or an empty string if the user can.

    The view check comes first, so a user who cannot view the lab gets a message that names no lab. With
    `owner_check` False, the user does not need to be the owner of the lab or a superuser.
    """
    if not Lab.objects.restrict(user, "view").filter(pk=lab.pk).exists():
        return NO_VIEW_MESSAGE.format(verb=verb)
    if owner_check and not (user.is_superuser or lab.owner_id == user.pk):
        return NOT_OWNER_MESSAGE.format(lab=lab, verb=verb)
    if not Lab.objects.restrict(user, "change").filter(pk=lab.pk).exists():
        return NO_CHANGE_MESSAGE.format(lab=lab, verb=verb)
    return ""


def fail_access(job, lab, verb, owner_check=True):
    """Fail the job if its user cannot do the verb on the lab, and return True if it failed.

    The failure for a user who cannot view the lab has no log object. `owner_check` goes to `lab_access_message`.
    """
    message = lab_access_message(lab, job.user, verb, owner_check)
    if message == NO_VIEW_MESSAGE.format(verb=verb):
        job.fail(message)
    elif message:
        job.fail(message, extra={"object": lab})
    return bool(message)


def error_lines(error):
    """Return one job log line for each message of the error, as "<field>: <message>" for a field error."""
    if isinstance(error, ValidationError) and hasattr(error, "error_dict"):
        return [
            message if field == "__all__" else f"{field}: {message}"
            for field, messages in error.message_dict.items()
            for message in messages
        ]
    if isinstance(error, ValidationError):
        return error.messages
    return [str(error)]


class BuildLabTopology(Job):
    """Build the containerlab topology of a lab, and store it with the node rows of the lab."""

    lab = ObjectVar(model=Lab, description="The lab to build.")

    class Meta:  # pylint: disable=too-few-public-methods
        """Job metadata."""

        name = "Build Lab Topology"
        description = "Build the containerlab topology of a lab from Nautobot data, and store it on the lab."
        has_sensitive_variables = False

    def run(self, lab):  # pylint: disable=arguments-differ
        """Check the access of the job user, build the lab, and log the warnings and a summary.

        Returns:
            The node, link, and warning counts of the build, or None if the build failed.
        """
        if fail_access(self, lab, "build"):
            return None
        try:
            result = build_lab(lab, self.user)
        except (ValidationError, ImproperlyConfigured) as error:
            for line in error_lines(error):
                self.fail(line, extra={"object": lab})
            return None
        for warning in result.warnings:
            self.logger.warning(warning, extra={"object": result.lab})
        summary = SUMMARY_MESSAGE.format(
            lab=result.lab,
            nodes=result.node_count,
            links=result.link_count,
            warnings=len(result.warnings),
            status=result.lab.status,
        )
        self.logger.success(summary, extra={"object": result.lab})
        return {"nodes": result.node_count, "links": result.link_count, "warnings": len(result.warnings)}


class DeployLab(Job):
    """Deploy the stored topology of a lab to its containerlab host, and record the status and the node state."""

    lab = ObjectVar(model=Lab, description="The lab to deploy.")
    replace_existing = BooleanVar(
        default=False, label="Replace a lab that exists on the host", description=REPLACE_HELP
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Job metadata."""

        name = "Deploy Lab"
        description = "Deploy the stored topology of a lab to its containerlab host."
        has_sensitive_variables = False
        soft_time_limit = 1800
        time_limit = 1900

    def run(self, lab, replace_existing=False):  # pylint: disable=arguments-differ
        """Check the access of the job user, deploy the lab, and log the warnings and a summary.

        Returns:
            The node, node state, and warning counts of the deploy, or None if the deploy failed.

        Raises:
            DeployTimeLimit: If the Celery soft time limit stopped the deploy call.
        """
        if fail_access(self, lab, "deploy"):
            return None
        try:
            result = deploy_lab(lab, replace_existing)
        except ValidationError as error:
            for line in error_lines(error):
                self.fail(line, extra={"object": lab})
            return None
        except DeployTimeLimit:
            self.fail(TIME_LIMIT_MESSAGE.format(lab=lab), extra={"object": lab})
            raise
        for warning in result.warnings:
            self.logger.warning(warning, extra={"object": result.lab})
        summary = DEPLOY_SUMMARY.format(
            lab=result.lab,
            host=result.lab.host,
            nodes=result.node_count,
            states=result.state_count,
            warnings=len(result.warnings),
        )
        self.logger.success(summary, extra={"object": result.lab})
        return {"nodes": result.node_count, "states": result.state_count, "warnings": len(result.warnings)}


class DestroyLab(Job):
    """Destroy the lab of a Deployed or Failed lab on its containerlab host, and clear its node state."""

    lab = ObjectVar(model=Lab, description="The lab to destroy.")
    keep_lab_directory = BooleanVar(default=False, label="Keep the lab directory", description=KEEP_DIRECTORY_HELP)

    class Meta:  # pylint: disable=too-few-public-methods
        """Job metadata."""

        name = "Destroy Lab"
        description = (
            "WARNING: This deletes the containers of the lab of this name on the host, also a lab that Nautobot did "
            "not deploy. Destroy a Deployed or Failed lab on its containerlab host."
        )
        has_sensitive_variables = False
        soft_time_limit = 1800
        time_limit = 1900

    def run(self, lab, keep_lab_directory=False):  # pylint: disable=arguments-differ
        """Check the access of the job user, destroy the lab, and log the warnings and a summary.

        Returns:
            The node row and warning counts of the destroy, or None if the destroy failed.

        Raises:
            SoftTimeLimitExceeded: If the Celery soft time limit stopped the destroy.
        """
        if fail_access(self, lab, "destroy"):
            return None
        try:
            result = destroy_lab(lab, keep_lab_directory)
        except ValidationError as error:
            for line in error_lines(error):
                self.fail(line, extra={"object": lab})
            return None
        except SoftTimeLimitExceeded:
            self.fail(DESTROY_TIME_LIMIT_MESSAGE.format(lab=lab), extra={"object": lab})
            raise
        for warning in result.warnings:
            self.logger.warning(warning, extra={"object": result.lab})
        summary = DESTROY_SUMMARY.format(
            lab=result.lab, host=result.lab.host, nodes=result.node_count, warnings=len(result.warnings)
        )
        self.logger.success(summary, extra={"object": result.lab})
        return {"nodes": result.node_count, "warnings": len(result.warnings)}


class RedeployLab(Job):
    """Destroy the lab of a Deployed or Failed lab on its host, then deploy the stored topology again."""

    lab = ObjectVar(model=Lab, description="The lab to redeploy.")

    class Meta:  # pylint: disable=too-few-public-methods
        """Job metadata."""

        name = "Redeploy Lab"
        description = (
            "WARNING: This deletes the containers and the lab directory of the lab of this name on the host, also a "
            "lab that Nautobot did not deploy. Each change in the running lab is lost. Deploy the stored topology of a "
            "Deployed or Failed lab again."
        )
        has_sensitive_variables = False
        soft_time_limit = 2600
        time_limit = 2700

    def run(self, lab):  # pylint: disable=arguments-differ
        """Check the access of the job user, redeploy the lab, and log the warnings and a summary.

        Returns:
            The node, node state, and warning counts of the redeploy, or None if the redeploy failed.

        Raises:
            DestroyTimeLimit: If the Celery soft time limit stopped the destroy call.
            DeployTimeLimit: If the Celery soft time limit stopped the deploy call after the destroy.
        """
        if fail_access(self, lab, "redeploy"):
            return None
        try:
            result = redeploy_lab(lab)
        except ValidationError as error:
            for line in error_lines(error):
                self.fail(line, extra={"object": lab})
            return None
        except DestroyTimeLimit:
            self.fail(DESTROY_TIME_LIMIT_MESSAGE.format(lab=lab), extra={"object": lab})
            raise
        except DeployTimeLimit:
            self.fail(TIME_LIMIT_MESSAGE.format(lab=lab), extra={"object": lab})
            self.fail(HALF_DONE_MESSAGE.format(lab=lab, host=lab.host), extra={"object": lab})
            raise
        for warning in result.warnings:
            self.logger.warning(warning, extra={"object": result.lab})
        summary = REDEPLOY_SUMMARY.format(
            lab=result.lab,
            host=result.lab.host,
            nodes=result.node_count,
            states=result.state_count,
            warnings=len(result.warnings),
        )
        self.logger.success(summary, extra={"object": result.lab})
        return {"nodes": result.node_count, "states": result.state_count, "warnings": len(result.warnings)}


class InspectLab(Job):
    """Read the containers of a lab on its containerlab host, and record the node state and the lab status."""

    lab = ObjectVar(model=Lab, description="The lab to inspect.")

    class Meta:  # pylint: disable=too-few-public-methods
        """Job metadata."""

        name = "Inspect Lab"
        description = (
            "Read the containers of a lab on its containerlab host, and record the node state and the lab status. "
            "The job changes nothing on the host."
        )
        has_sensitive_variables = False
        soft_time_limit = 700
        time_limit = 800

    def run(self, lab):  # pylint: disable=arguments-differ
        """Check the access of the job user, inspect the lab, and log the warnings and a summary.

        Returns:
            The node, running, and warning counts of the inspect, or None if the inspect failed.

        Raises:
            SoftTimeLimitExceeded: If the Celery soft time limit stopped the inspect.
        """
        if fail_access(self, lab, "inspect", owner_check=False):
            return None
        try:
            result = inspect_lab(lab)
        except ValidationError as error:
            for line in error_lines(error):
                self.fail(line, extra={"object": lab})
            return None
        except SoftTimeLimitExceeded:
            self.fail(INSPECT_TIME_LIMIT_MESSAGE.format(lab=lab), extra={"object": lab})
            raise
        for warning in result.warnings:
            self.logger.warning(warning, extra={"object": result.lab})
        summary = INSPECT_SUMMARY.format(
            lab=result.lab,
            host=result.lab.host,
            nodes=result.node_count,
            running=result.running_count,
            warnings=len(result.warnings),
            status=result.lab.status,
        )
        self.logger.success(summary, extra={"object": result.lab})
        return {"nodes": result.node_count, "running": result.running_count, "warnings": len(result.warnings)}


jobs = [BuildLabTopology, DeployLab, RedeployLab, DestroyLab, InspectLab]
register_jobs(*jobs)
