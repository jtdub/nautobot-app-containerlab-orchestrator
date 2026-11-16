"""Tests for the build service that stores the topology YAML and the node rows of a lab."""

from unittest import mock

import yaml
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import override_settings
from django.utils import timezone
from nautobot.dcim.models import Device
from nautobot.extras.models import Status

from containerlab_orchestrator.build import (
    BUILT_STATUS_MESSAGE,
    DEVICE_LIMIT_SETTING_MESSAGE,
    LAB_CHANGED_MESSAGE,
    NODE_KEY_MESSAGE,
    TOO_MANY_DEVICES_MESSAGE,
    build_lab,
)
from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.models import Lab, LabNode, NodeMapping
from containerlab_orchestrator.selection import HIDDEN_DEVICES_MESSAGE
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts, grant_permission
from containerlab_orchestrator.tests.test_app_config import DEFAULT_SETTINGS
from containerlab_orchestrator.tests.test_topology import TopologyTestCase
from containerlab_orchestrator.topology import (
    CABLE_WARNING,
    SUBNET_ROOM_MESSAGE,
    W_MGMT_ONLY,
    build_topology,
    to_yaml,
)
from containerlab_orchestrator.validators import subnet_node_room


class BuildLabTestCase(TopologyTestCase):
    """Test build_lab on a lab that selects arista1 and leaf1."""

    @classmethod
    def setUpTestData(cls):
        """Create the hosts, the outside device wan1, and the Planned lab build-lab."""
        super().setUpTestData()
        cls.wan1 = cls.add_device("wan1")
        cls.lab = Lab.objects.create(
            name="build-lab",
            host=create_containerlab_hosts()[0],
            owner=get_user_model().objects.create_user(username="alice"),
            status=Status.objects.get_for_model(Lab).get(name=LabStatusChoices.PLANNED),
            mgmt_subnet="172.20.30.0/24",
            config_source=NodeMappingConfigSourceChoices.NONE,
        )
        cls.lab.devices.set([cls.arista1, cls.leaf1])

    def _set_status(self, name):
        """Set the status of the lab row, with no model save."""
        Lab.objects.filter(pk=self.lab.pk).update(status=Status.objects.get(name=name))

    def _snapshot(self):
        """Return the job fields of the lab row and the fields of its node rows."""
        lab = Lab.objects.get(pk=self.lab.pk)
        rows = LabNode.objects.filter(lab=lab).order_by("node_name")
        return (
            lab.topology_yaml,
            lab.built_at,
            lab.status.name,
            list(rows.values_list("pk", "node_name", "device", "kind", "image", "mgmt_ipv4", "state")),
        )

    def _rows(self):
        """Return the node rows of the lab by node name."""
        return {row.node_name: row for row in LabNode.objects.filter(lab=self.lab)}

    def test_build_stores_yaml_built_at_and_status(self):
        before = timezone.now()
        result = build_lab(self.lab)
        lab = Lab.objects.get(pk=self.lab.pk)
        expected, _, _ = build_topology(lab, [self.arista1, self.leaf1])
        self.assertEqual(lab.topology_yaml, to_yaml(expected))
        self.assertGreaterEqual(lab.built_at, before)
        self.assertEqual(lab.status.name, LabStatusChoices.BUILT)
        self.assertEqual((result.lab, result.node_count, result.link_count), (lab, 2, 0))

    def test_build_writes_one_row_for_each_node(self):
        build_lab(self.lab)
        nodes = yaml.safe_load(Lab.objects.get(pk=self.lab.pk).topology_yaml)["topology"]["nodes"]
        rows = self._rows()
        self.assertEqual(set(rows), {"arista1", "leaf1"})
        for name, device in (("arista1", self.arista1), ("leaf1", self.leaf1)):
            with self.subTest(name=name):
                row = rows[name]
                self.assertEqual((row.device, row.is_stub), (device, False))
                self.assertEqual(
                    (row.kind, row.image, row.mgmt_ipv4),
                    (nodes[name]["kind"], nodes[name]["image"], nodes[name]["mgmt-ipv4"]),
                )

    def test_stub_row_has_no_device(self):
        self.connect(self.add_interfaces(self.leaf1, "Ethernet1")[0], self.add_interfaces(self.wan1, "Ethernet1")[0])
        result = build_lab(self.lab)
        stub = self._rows()["stub-wan1"]
        self.assertEqual((stub.device, stub.is_stub, stub.kind, stub.image), (None, True, "linux", "alpine:latest"))
        self.assertEqual((result.node_count, result.link_count), (3, 1))

    def test_second_build_replaces_yaml_and_rows(self):
        build_lab(self.lab)
        self.lab.devices.set([self.arista1, self.leaf2])
        build_lab(self.lab)
        lab = Lab.objects.get(pk=self.lab.pk)
        expected, _, _ = build_topology(lab, [self.arista1, self.leaf2])
        self.assertEqual(lab.topology_yaml, to_yaml(expected))
        self.assertEqual(set(self._rows()), {"arista1", "leaf2"})

    def test_second_build_keeps_pk_state_and_container_id(self):
        build_lab(self.lab)
        kept = self._rows()["arista1"]
        LabNode.objects.filter(pk=kept.pk).update(state="running", container_id="abc123")
        build_lab(self.lab)
        row = self._rows()["arista1"]
        self.assertEqual((row.pk, row.state, row.container_id), (kept.pk, "running", "abc123"))

    def test_deployed_and_failed_labs_keep_status(self):
        for name in (LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED):
            with self.subTest(status=name):
                self._set_status(name)
                build_lab(self.lab)
                self.assertEqual(Lab.objects.get(pk=self.lab.pk).status.name, name)

    def test_planned_built_destroyed_become_built(self):
        for name in (LabStatusChoices.PLANNED, LabStatusChoices.BUILT, LabStatusChoices.DESTROYED):
            with self.subTest(status=name):
                self._set_status(name)
                build_lab(self.lab)
                self.assertEqual(Lab.objects.get(pk=self.lab.pk).status.name, LabStatusChoices.BUILT)

    def test_build_returns_builder_warnings(self):
        (mgmt,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1", mgmt_only=True)
        self.connect(mgmt, self.add_interfaces(self.leaf1, "Ethernet1")[0])
        result = build_lab(self.lab)
        self.assertEqual(
            result.warnings, [CABLE_WARNING.format(device=self.arista1, interface=mgmt.name, text=W_MGMT_ONLY)]
        )

    def test_too_many_devices_fails_before_build(self):
        with override_settings(PLUGINS_CONFIG={APP_NAME: {**DEFAULT_SETTINGS, "max_lab_devices": 1}}):
            with mock.patch("containerlab_orchestrator.build.build_topology") as builder:
                with self.assertRaises(ValidationError) as raised:
                    build_lab(self.lab)
        builder.assert_not_called()
        self.assertEqual(raised.exception.messages, [TOO_MANY_DEVICES_MESSAGE.format(lab=self.lab, count=2, limit=1)])

    def test_stub_nodes_do_not_count_toward_device_limit(self):
        self.connect(self.add_interfaces(self.leaf1, "Ethernet1")[0], self.add_interfaces(self.wan1, "Ethernet1")[0])
        with override_settings(PLUGINS_CONFIG={APP_NAME: {**DEFAULT_SETTINGS, "max_lab_devices": 2}}):
            result = build_lab(self.lab)
        self.assertEqual(result.node_count, 3)
        self.assertEqual(set(self._rows()), {"arista1", "leaf1", "stub-wan1"})

    def test_bad_device_limit_setting_fails(self):
        for value in (True, "100", None, 0):
            with self.subTest(value=value):
                with override_settings(PLUGINS_CONFIG={APP_NAME: {**DEFAULT_SETTINGS, "max_lab_devices": value}}):
                    with mock.patch("containerlab_orchestrator.build.select_devices") as selection:
                        with self.assertRaises(ImproperlyConfigured) as raised:
                            build_lab(self.lab)
                selection.assert_not_called()
                self.assertEqual(str(raised.exception), DEVICE_LIMIT_SETTING_MESSAGE)

    def test_failed_build_changes_nothing(self):
        build_lab(self.lab)
        before = self._snapshot()
        Lab.objects.filter(pk=self.lab.pk).update(mgmt_subnet="172.20.30.0/30")
        with self.assertRaises(ValidationError) as raised:
            build_lab(self.lab)
        room = subnet_node_room("172.20.30.0/30")
        self.assertEqual(
            raised.exception.message_dict, {"mgmt_subnet": [SUBNET_ROOM_MESSAGE.format(room=room, count=2)]}
        )
        self.assertEqual(self._snapshot(), before)

    def test_failure_after_row_writes_rolls_back(self):
        build_lab(self.lab)
        before = self._snapshot()
        self.lab.devices.set([self.arista1, self.leaf2])
        with mock.patch.object(Lab, "validated_save", side_effect=ValidationError("stop")) as save:
            with self.assertRaises(ValidationError):
                build_lab(self.lab)
        save.assert_called_once()
        self.assertEqual(self._snapshot(), before)

    def test_missing_built_status_fails_before_write(self):
        Status.objects.get(name=LabStatusChoices.BUILT).content_types.remove(ContentType.objects.get_for_model(Lab))
        before = self._snapshot()
        with self.assertRaises(ValidationError) as raised:
            build_lab(self.lab)
        self.assertEqual(raised.exception.messages, [BUILT_STATUS_MESSAGE])
        self.assertEqual(self._snapshot(), before)

    def test_deployed_lab_builds_with_no_built_status(self):
        Status.objects.get(name=LabStatusChoices.BUILT).content_types.remove(ContentType.objects.get_for_model(Lab))
        self._set_status(LabStatusChoices.DEPLOYED)
        build_lab(self.lab)
        lab = Lab.objects.get(pk=self.lab.pk)
        self.assertEqual(lab.status.name, LabStatusChoices.DEPLOYED)
        self.assertEqual(set(self._rows()), {"arista1", "leaf1"})

    def test_build_passes_user_to_selection(self):
        user = get_user_model().objects.create_user(username="viewer")
        grant_permission(user, Device, "view", constraints={"name": "leaf1"})
        with self.assertRaises(ValidationError) as raised:
            build_lab(self.lab, user)
        self.assertEqual(raised.exception.messages, [HIDDEN_DEVICES_MESSAGE.format(count=1, lab=self.lab)])

    def test_build_passes_user_to_topology(self):
        user = get_user_model().objects.create_user(username="viewer")
        grant_permission(user, Device, "view", constraints={"name__in": ["arista1", "leaf1"]})
        with mock.patch("containerlab_orchestrator.build.build_topology", wraps=build_topology) as builder:
            build_lab(self.lab, user)
        self.assertEqual(builder.call_args.args[2], user)

    def test_changed_lab_fails_with_message(self):
        before = self._snapshot()

        def change_lab_then_build(*args):
            Lab.objects.filter(pk=self.lab.pk).update(last_updated=timezone.now())
            return build_topology(*args)

        with mock.patch("containerlab_orchestrator.build.build_topology", side_effect=change_lab_then_build):
            with self.assertRaises(ValidationError) as raised:
                build_lab(self.lab)
        self.assertEqual(raised.exception.messages, [LAB_CHANGED_MESSAGE.format(lab=self.lab)])
        self.assertEqual(self._snapshot(), before)

    def test_node_with_no_image_fails_with_node_name(self):
        build_lab(self.lab)
        before = self._snapshot()
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(image="ceos:$TAG")
        with self.assertRaises(ValidationError) as raised:
            build_lab(self.lab)
        self.assertEqual(raised.exception.messages, [NODE_KEY_MESSAGE.format(node="arista1", key="image")])
        self.assertEqual(self._snapshot(), before)

    def test_rejected_row_fails_with_node_name(self):
        build_lab(self.lab)
        before = self._snapshot()
        with mock.patch.object(LabNode, "clean", side_effect=ValidationError({"image": "bad"})):
            with self.assertRaises(ValidationError) as raised:
                build_lab(self.lab)
        self.assertEqual(raised.exception.messages, ["Node arista1: bad"])
        self.assertEqual(self._snapshot(), before)

    def test_case_only_rename_replaces_the_row(self):
        build_lab(self.lab)
        old = self._rows()["leaf1"]
        LabNode.objects.filter(pk=old.pk).update(state="running", container_id="abc123")
        Device.objects.filter(pk=self.leaf1.pk).update(name="Leaf1")
        build_lab(self.lab)
        rows = self._rows()
        self.assertEqual(set(rows), {"arista1", "Leaf1"})
        self.assertNotEqual(rows["Leaf1"].pk, old.pk)
        self.assertEqual((rows["Leaf1"].state, rows["Leaf1"].container_id), ("", ""))
