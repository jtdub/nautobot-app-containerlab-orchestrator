"""Tests for the data migration that turns on the jobs of the app."""

import importlib

from django.apps import apps
from nautobot.apps.testing import TestCase
from nautobot.extras.models import Job

from containerlab_orchestrator.jobs import jobs
from containerlab_orchestrator.sync import LAB_JOBS_MODULE

enable_jobs = importlib.import_module("containerlab_orchestrator.migrations.0002_enable_jobs").enable_jobs

JOB_CLASS_NAMES = sorted(job_class.__name__ for job_class in jobs)
"""The class name of each job of the app."""


def enabled_job_class_names():
    """Return the sorted class names of the app jobs that are turned on."""
    return sorted(
        Job.objects.filter(module_name=LAB_JOBS_MODULE, enabled=True).values_list("job_class_name", flat=True)
    )


class EnableJobsMigrationTestCase(TestCase):
    """Test the 0002_enable_jobs migration."""

    def test_migrated_database_has_each_job_turned_on(self):
        self.assertEqual(len(JOB_CLASS_NAMES), 6)
        self.assertEqual(enabled_job_class_names(), JOB_CLASS_NAMES)

    def test_enable_jobs_turns_on_a_job_that_is_off(self):
        Job.objects.filter(module_name=LAB_JOBS_MODULE).update(enabled=False)

        enable_jobs(apps, None)

        self.assertEqual(enabled_job_class_names(), JOB_CLASS_NAMES)

    def test_enable_jobs_creates_a_missing_job_record(self):
        Job.objects.filter(module_name=LAB_JOBS_MODULE).delete()

        enable_jobs(apps, None)

        self.assertEqual(enabled_job_class_names(), JOB_CLASS_NAMES)
        for job_model in Job.objects.filter(module_name=LAB_JOBS_MODULE):
            self.assertTrue(job_model.installed)
            self.assertTrue(job_model.job_queues.exists())
