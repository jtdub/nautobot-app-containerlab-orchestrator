"""Turn on each job of the app when the app is installed.

Nautobot adds the Job record of an app job turned off, after the last migration. This migration makes the records
earlier, with the same Nautobot function, and turns them on. It runs one time, so an administrator can turn a job off.
"""

from django.db import migrations
from nautobot.extras.utils import refresh_job_model_from_job_class


def enable_jobs(apps, schema_editor):
    """Create the Job record of each app job that has no record, and turn on each record."""
    from containerlab_orchestrator.jobs import jobs  # pylint: disable=import-outside-toplevel

    Job = apps.get_model("extras", "Job")
    JobQueue = apps.get_model("extras", "JobQueue")
    for job_class in jobs:
        job_model, _ = refresh_job_model_from_job_class(Job, job_class, JobQueue)
        if job_model is not None:
            Job.objects.filter(pk=job_model.pk).update(enabled=True)


class Migration(migrations.Migration):
    dependencies = [
        ("containerlab_orchestrator", "0001_initial"),
        ("extras", "0145_objectmetadata_assigned_object_type_cascade"),
    ]

    operations = [
        migrations.RunPython(enable_jobs, migrations.RunPython.noop),
    ]
