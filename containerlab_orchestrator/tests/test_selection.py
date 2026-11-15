"""Tests for the device selection service of a lab."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import override_settings
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Device, Location
from nautobot.extras.models import DynamicGroup, Status

from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.models import Lab
from containerlab_orchestrator.selection import select_devices, validate_device_filter
from containerlab_orchestrator.tests.fixtures import create_containerlab_hosts, create_lab_devices, create_labs

BAD_ROLE_MESSAGE = "role: Select a valid choice. nope is not one of the available choices."
SHAPE_MESSAGE = "role: Use a value or a list of values."


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class SelectionTestCase(TestCase):
    """Base class with the data and the helpers for the device selection tests."""

    @classmethod
    def setUpTestData(cls):
        cls.hosts = create_containerlab_hosts()
        cls.lab_devices = create_lab_devices()
        cls.lab_a, cls.lab_b, cls.lab_a_clab02 = create_labs(cls.hosts, cls.lab_devices)
        cls.planned = Status.objects.get(name=LabStatusChoices.PLANNED)
        cls.device_content_type = ContentType.objects.get_for_model(Device)

    def make_lab(self, devices=(), **kwargs):
        """Return a saved Planned lab on clab03 with a free name and subnet, and with these devices."""
        number = Lab.objects.count() + 1
        lab = Lab.objects.create(
            name=f"select-{number}",
            host=self.hosts[2],
            mgmt_subnet=f"192.168.{number}.0/24",
            status=self.planned,
            **kwargs,
        )
        lab.devices.set(devices)
        return lab

    def make_unchecked_lab(self, **fields):
        """Return a saved lab whose fields are written with no clean(), so that they can be bad."""
        lab = self.make_lab()
        Lab.objects.filter(pk=lab.pk).update(**fields)
        lab.refresh_from_db()
        return lab

    def make_set_group(self, name, children):
        """Return a saved dynamic-set group of devices with these (child, operator) pairs."""
        group = self.make_group(name, group_type="dynamic-set")
        for weight, (child, operator) in enumerate(children, start=1):
            group.add_child(child, operator, weight * 10)
        return group

    def assert_selects(self, lab, names, user=None):
        """Assert that select_devices gives exactly the devices with these names."""
        self.assertEqual(set(select_devices(lab, user=user).values_list("name", flat=True)), names)

    def assert_select_errors(self, lab, errors, user=None):
        """Assert that select_devices raises exactly these errors."""
        with self.assertRaises(ValidationError) as context:
            select_devices(lab, user=user)
        self.assertEqual(context.exception.message_dict, errors)

    def assert_select_messages(self, lab, messages, user=None):
        """Assert that select_devices raises an error with exactly these messages."""
        with self.assertRaises(ValidationError) as context:
            select_devices(lab, user=user)
        self.assertEqual(context.exception.messages, messages)

    def make_group(self, name, **kwargs):
        """Return a saved Dynamic Group of devices."""
        return DynamicGroup.objects.create(name=name, content_type=self.device_content_type, **kwargs)


class SelectDevicesTestCase(SelectionTestCase):  # pylint: disable=too-many-ancestors
    """Test the devices that select_devices gives for each source and each user."""

    def test_device_filter_alone_selects_its_devices(self):
        self.assert_selects(self.make_lab(device_filter={"role": ["leaf"]}), {"leaf1", "leaf2"})

    def test_dynamic_group_alone_selects_its_members(self):
        self.assert_selects(self.lab_a_clab02, {"spine1"})

    def test_device_list_alone_selects_its_devices(self):
        self.assert_selects(self.make_lab(devices=[self.lab_devices["spine1"]]), {"spine1"})

    def test_empty_device_filter_adds_no_device(self):
        self.assert_selects(self.make_lab(device_filter={}, devices=[self.lab_devices["spine1"]]), {"spine1"})

    def test_all_sources_give_their_union(self):
        lab = self.make_lab(
            device_filter={"name": ["leaf1"]},
            dynamic_group=self.lab_devices["spine_group"],
            devices=[self.lab_devices["leaf2"]],
        )
        self.assert_selects(lab, {"leaf1", "leaf2", "spine1"})
        self.assert_selects(self.lab_a, {"leaf1", "leaf2", "spine1"})

    def test_device_in_two_sources_occurs_once(self):
        self.assert_selects(self.lab_b, {"leaf1", "leaf2"})
        self.assertEqual(select_devices(self.lab_b).count(), 2)

    def test_dynamic_group_members_are_refreshed(self):
        leaf1 = self.lab_devices["leaf1"]
        leaf3 = Device.objects.create(
            name="leaf3", device_type=leaf1.device_type, location=leaf1.location, status=leaf1.status, role=leaf1.role
        )
        self.assertNotIn(leaf3, self.lab_devices["leaf_group"].members)
        self.assert_selects(self.lab_b, {"leaf1", "leaf2", "leaf3"})

    def test_concurrent_refresh_uses_cached_members(self):
        with mock.patch.object(DynamicGroup, "update_cached_members", side_effect=IntegrityError):
            self.assert_selects(self.lab_b, {"leaf1", "leaf2"})

    def test_user_sees_only_devices_they_can_view(self):
        self.add_permissions("dcim.view_device")
        self.assert_selects(self.make_lab(devices=[self.lab_devices["leaf1"]]), {"leaf1"}, user=self.user)

    def test_hidden_devices_raise_error(self):
        self.add_permissions("dcim.view_device", constraints={"name": "leaf1"})
        self.assert_select_messages(
            self.lab_a, ["You cannot view 2 of the devices that lab lab-a selects."], user=self.user
        )

    def test_user_without_view_permission_gets_empty_selection_error(self):
        self.assert_select_messages(
            self.lab_a,
            ["The device filter, the Dynamic Group, and the devices of lab lab-a select no device."],
            user=self.user,
        )

    def test_no_user_applies_no_limit(self):
        self.assert_selects(self.lab_a, {"leaf1", "leaf2", "spine1"})

    def test_superuser_and_exempt_user_see_all_devices(self):
        superuser = get_user_model().objects.create_user(username="root", is_superuser=True)
        self.assert_selects(self.lab_a, {"leaf1", "leaf2", "spine1"}, user=superuser)
        with override_settings(EXEMPT_VIEW_PERMISSIONS=["dcim.device"]):
            self.assert_selects(self.lab_a, {"leaf1", "leaf2", "spine1"}, user=self.user)

    def test_static_dynamic_group_selects_its_members(self):
        group = self.make_group("Static devices", group_type="static")
        group.add_members([self.lab_devices["spine1"]])
        self.assert_selects(self.make_lab(dynamic_group=group), {"spine1"})

    def test_empty_child_that_restricts_selection_passes(self):
        group = self.make_set_group(
            "Leaf set",
            [(self.lab_devices["leaf_group"], "union"), (self.make_group("All restrict", filter={}), "intersection")],
        )
        self.assert_selects(self.make_lab(dynamic_group=group), {"leaf1", "leaf2"})


class SelectionErrorTestCase(SelectionTestCase):  # pylint: disable=too-many-ancestors
    """Test the errors of validate_device_filter and select_devices."""

    def test_bad_device_filter_raises_filter_errors(self):
        cases = (
            ({"bogus": ["1"]}, ["bogus: Unknown filter field."]),
            ({"role": ["nope"]}, [BAD_ROLE_MESSAGE]),
            ({"role": "leaf"}, ["role: Enter a list of values."]),
            ({"has_primary_ip": [True]}, ["has_primary_ip: Use one value, not a list."]),
            ({"role": {"name": "leaf"}}, [SHAPE_MESSAGE]),
            ({"role": [["leaf"]]}, [SHAPE_MESSAGE]),
            ({"role": [{"name": "leaf"}]}, [SHAPE_MESSAGE]),
            ({"role": []}, ["role: Enter a value."]),
            ({"role": None}, ["role: Enter a value."]),
            ({"q": ""}, ["q: Enter a value."]),
            ({"has_primary_ip": "maybe"}, ["has_primary_ip: Enter a value."]),
            ({"name": 5}, ["name: Use a value or a list of values."]),
            ({"name": True}, ["name: Use a value or a list of values."]),
            ({"position": 1.5}, ["position: Use a value or a list of values."]),
            (
                {"zeta": ["1"], "bogus": ["1"], "role": ["nope"]},
                ["bogus: Unknown filter field.", BAD_ROLE_MESSAGE, "zeta: Unknown filter field."],
            ),
        )
        for device_filter, messages in cases:
            with self.subTest(device_filter=device_filter):
                with self.assertRaises(ValidationError) as context:
                    validate_device_filter(device_filter)
                self.assertEqual(context.exception.message_dict, {"device_filter": messages})
                lab = self.make_unchecked_lab(device_filter=device_filter)
                self.assert_select_errors(lab, {"device_filter": messages})

    def test_unknown_key_fails_without_strict_filtering(self):
        with override_settings(STRICT_FILTERING=False):
            with self.assertRaises(ValidationError) as context:
                validate_device_filter({"bogus": ["1"]})
        self.assertEqual(context.exception.message_dict, {"device_filter": ["bogus: Unknown filter field."]})

    def test_saved_filter_that_is_not_object_raises_error(self):
        lab = self.make_unchecked_lab(device_filter=["leaf"])
        self.assert_select_errors(lab, {"device_filter": ['Enter a JSON object, for example {"key": "value"}.']})

    def test_empty_selection_raises_error(self):
        for kwargs in ({}, {"device_filter": {"name": ["no-such-device"]}}):
            with self.subTest(kwargs=kwargs):
                lab = self.make_lab(**kwargs)
                self.assert_select_messages(
                    lab,
                    [f"The device filter, the Dynamic Group, and the devices of lab {lab.name} select no device."],
                )

    def test_bad_dynamic_group_raises_group_errors(self):
        bad_group = self.make_group("Bad devices", filter={"role": ["leaf"]})
        DynamicGroup.objects.filter(pk=bad_group.pk).update(filter={"bogus": ["1"]})
        location_group = DynamicGroup.objects.create(
            name="Lab locations", content_type=ContentType.objects.get_for_model(Location)
        )
        cases = (
            (bad_group, ["Bad devices: bogus: Unknown filter field."]),
            (location_group, ["Select a Dynamic Group of devices."]),
        )
        for group, messages in cases:
            with self.subTest(group=group.name):
                lab = self.make_unchecked_lab(dynamic_group=group)
                self.assert_select_errors(lab, {"dynamic_group": messages})

    def test_match_all_dynamic_group_raises_error(self):
        all_child = self.make_group("All child", filter={})
        cases = (
            (self.make_group("All by filter", filter={}), "All by filter"),
            (self.make_group("Empty set", group_type="dynamic-set"), "Empty set"),
            (self.make_set_group("Union set", [(all_child, "union")]), "All child"),
        )
        for group, flagged_name in cases:
            with self.subTest(group=group.name):
                lab = self.make_lab(dynamic_group=group)
                self.assert_select_errors(lab, {"dynamic_group": [f"{flagged_name}: This group selects all devices."]})

    def test_bad_child_group_raises_error_once(self):
        child = self.make_group("Bad child", filter={"role": ["leaf"]})
        group = self.make_set_group("Bad set", [(child, "union"), (child, "union")])
        DynamicGroup.objects.filter(pk=child.pk).update(filter={"bogus": ["1"]})
        lab = self.make_lab(dynamic_group=group)
        self.assert_select_errors(lab, {"dynamic_group": ["Bad child: bogus: Unknown filter field."]})
