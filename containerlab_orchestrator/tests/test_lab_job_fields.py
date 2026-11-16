"""Tests for the Lab.save() rule that keeps an edit from writing the job fields."""

from datetime import timedelta

from django.utils import timezone
from nautobot.apps.change_logging import web_request_context
from nautobot.apps.choices import ObjectChangeActionChoices
from nautobot.apps.testing import TestCase
from nautobot.extras.models import ObjectChange, Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.models import Lab
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts, create_lab_devices, create_labs

BUILT_YAML = "name: lab-a\ntopology:\n  nodes: {}\n"
INSPECT_DATA = {"containers": [{"name": "clab-lab-a-leaf1", "state": "running"}]}


class LabJobFieldsTestCase(TestCase):
    """Test which lab fields a save writes."""

    @classmethod
    def setUpTestData(cls):
        cls.hosts = create_containerlab_hosts()
        cls.labs = create_labs(cls.hosts, create_lab_devices())
        cls.built = Status.objects.get_for_model(Lab).get(name=LabStatusChoices.BUILT)
        cls.built_at = timezone.now() - timedelta(minutes=5)

    def _write_build(self, lab):
        """Store the job fields of a build on the row, with no model save."""
        Lab.objects.filter(pk=lab.pk).update(
            status=self.built, topology_yaml=BUILT_YAML, built_at=self.built_at, last_inspect=INSPECT_DATA
        )

    def _assert_build_fields(self, lab):
        """Check that the row of the lab holds the job fields of the build."""
        lab.refresh_from_db()
        self.assertEqual(lab.status, self.built)
        self.assertEqual(lab.topology_yaml, BUILT_YAML)
        self.assertEqual(lab.built_at, self.built_at)
        self.assertEqual(lab.last_inspect, INSPECT_DATA)

    def test_edit_save_does_not_write_job_fields(self):
        stale = Lab.objects.get(pk=self.labs[0].pk)
        self._write_build(stale)
        last_updated = Lab.objects.get(pk=stale.pk).last_updated

        stale.mgmt_subnet = "172.20.30.0/24"
        with web_request_context(self.user):
            stale.validated_save()

        self._assert_build_fields(stale)
        self.assertEqual(stale.mgmt_subnet, "172.20.30.0/24")
        self.assertGreater(stale.last_updated, last_updated)
        change = ObjectChange.objects.filter(changed_object_id=stale.pk).latest("time")
        self.assertEqual(change.action, ObjectChangeActionChoices.ACTION_UPDATE)
        self.assertEqual(change.object_data_v2["mgmt_subnet"], "172.20.30.0/24")

    def test_job_fields_save_writes_job_fields(self):
        lab = Lab.objects.get(pk=self.labs[0].pk)
        lab.status = self.built
        lab.topology_yaml = BUILT_YAML
        lab.built_at = self.built_at
        lab.last_inspect = INSPECT_DATA

        lab.validated_save(job_fields=True)

        self._assert_build_fields(lab)

    def test_explicit_update_fields_are_kept(self):
        lab = Lab.objects.get(pk=self.labs[0].pk)
        lab.mgmt_subnet = "172.20.30.0/24"
        lab.name = "lab-renamed"
        lab.topology_yaml = BUILT_YAML

        lab.save(update_fields=["mgmt_subnet"])

        lab.refresh_from_db()
        self.assertEqual(lab.mgmt_subnet, "172.20.30.0/24")
        self.assertEqual(lab.name, "lab-a")
        self.assertEqual(lab.topology_yaml, "")

    def test_create_writes_all_fields(self):
        lab = Lab(name="new-lab", host=self.hosts[2], mgmt_subnet="192.168.3.0/24")
        lab.set_create_defaults(self.user)

        lab.validated_save()

        lab.refresh_from_db()
        self.assertEqual(lab.owner, self.user)
        self.assertEqual(lab.status.name, LabStatusChoices.PLANNED)
