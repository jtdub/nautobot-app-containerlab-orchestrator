"""Jobs of the containerlab_orchestrator app."""

from django.core.exceptions import ImproperlyConfigured, ValidationError
from nautobot.apps.jobs import Job, ObjectVar, register_jobs

from containerlab_orchestrator.build import build_lab
from containerlab_orchestrator.models import Lab

name = "Containerlab Orchestrator"  # pylint: disable=invalid-name

NO_VIEW_MESSAGE = "You cannot build this lab."
"""The error for a user who cannot view the lab. It names no lab, so it shows nothing of the lab."""

NOT_OWNER_MESSAGE = "Only the owner of lab {lab} or a superuser can build it."
"""The error for a user who is not the owner of the lab and not a superuser."""

NO_CHANGE_MESSAGE = "You need the change permission on lab {lab} to build it."
"""The error for a user who cannot change the lab."""

SUMMARY_MESSAGE = "Built lab {lab}: {nodes} nodes, {links} links, {warnings} warnings. Status: {status}."
"""The success line of a build."""


def build_access_message(lab, user):
    """Return the reason that the user cannot build the lab, or an empty string if the user can build it.

    The view check comes first, so a user who cannot view the lab gets a message that names no lab.
    """
    if not Lab.objects.restrict(user, "view").filter(pk=lab.pk).exists():
        return NO_VIEW_MESSAGE
    if not (user.is_superuser or lab.owner_id == user.pk):
        return NOT_OWNER_MESSAGE.format(lab=lab)
    if not Lab.objects.restrict(user, "change").filter(pk=lab.pk).exists():
        return NO_CHANGE_MESSAGE.format(lab=lab)
    return ""


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
        message = build_access_message(lab, self.user)
        if message == NO_VIEW_MESSAGE:
            self.fail(message)
            return None
        if message:
            self.fail(message, extra={"object": lab})
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


jobs = [BuildLabTopology]
register_jobs(*jobs)
