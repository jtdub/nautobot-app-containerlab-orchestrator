"""Tests for the node mapping resolver and the interface mapper."""

import copy

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from nautobot.apps.config import get_app_settings_or_config
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import (
    Device,
    DeviceType,
    Interface,
    Location,
    LocationType,
    Manufacturer,
    Module,
    ModuleBay,
    ModuleType,
    Platform,
)
from nautobot.extras.models import Role, Status

from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.mapper import DefaultMapping, map_interfaces, resolve_mapping
from containerlab_orchestrator.models import InterfaceMapping, NodeMapping
from containerlab_orchestrator.tests.fixtures import create_interface_mappings, create_node_mappings

PARTIAL_TABLE_NAMES = ("Ethernet10", "Ethernet3", "Ethernet2", "Ethernet1")
"""Interface names of leaf1 that the Test table mapping holds in part."""


def default_mapping_setting(value):
    """Return a settings override that sets default_node_mapping to the value."""
    return override_settings(PLUGINS_CONFIG={APP_NAME: {"default_node_mapping": value}})


def ordered_names(container_names):
    """Return the (interface name, container name) pairs of a map_interfaces result, in order."""
    return [(interface.name, name) for interface, name in container_names.items()]


class MapperTestCase(TestCase):
    """Base class with the node mappings and the Device Types that the mapper tests use."""

    @classmethod
    def setUpTestData(cls):
        """Create the Arista, Nokia, linux, and Test table mappings, and a Device Type and a Platform with none."""
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        cls.arista_mapping, cls.nokia_mapping, cls.linux_mapping = node_mappings
        manufacturer = Manufacturer.objects.create(name="Test")
        cls.unmapped_type = DeviceType.objects.create(manufacturer=manufacturer, model="Test unmapped")
        cls.table_type = DeviceType.objects.create(manufacturer=manufacturer, model="Test table")
        cls.unmapped_platform = Platform.objects.create(name="unmapped")
        cls.table_mapping = NodeMapping.objects.create(device_type=cls.table_type, kind="ceos", image="ceos:4.32.0F")
        for physical_name, container_name in (("Ethernet1", "eth1"), ("Ethernet2", "eth3"), ("Ethernet9", "eth5")):
            InterfaceMapping.objects.create(
                node_mapping=cls.table_mapping, physical_name=physical_name, container_name=container_name
            )


class ResolveMappingTestCase(MapperTestCase):
    """Test resolve_mapping with unsaved devices."""

    def test_resolve_mapping_returns_device_type_mapping_before_platform_mapping(self):
        device = Device(name="leaf1", device_type=self.arista_mapping.device_type, platform=self.linux_mapping.platform)
        self.assertEqual(resolve_mapping(device), self.arista_mapping)

    def test_resolve_mapping_returns_platform_mapping_without_device_type_mapping(self):
        device = Device(name="srv1", device_type=self.unmapped_type, platform=self.linux_mapping.platform)
        self.assertEqual(resolve_mapping(device), self.linux_mapping)

    def test_resolve_mapping_returns_default_mapping_without_match(self):
        device = Device(name="srv1", device_type=self.unmapped_type, platform=self.unmapped_platform)
        with default_mapping_setting({"kind": "nokia_srlinux", "image": "ghcr.io/nokia/srlinux:latest"}):
            mapping = resolve_mapping(device)
        self.assertIsInstance(mapping, DefaultMapping)
        self.assertEqual(
            (
                mapping.kind,
                mapping.image,
                mapping.node_type,
                mapping.env,
                mapping.cpu,
                mapping.memory,
                mapping.overrides,
                mapping.config_source,
                mapping.config_template,
            ),
            ("nokia_srlinux", "ghcr.io/nokia/srlinux:latest", "", {}, None, "", {}, "", ""),
        )
        self.assertIsNone(mapping.device_type)
        self.assertIsNone(mapping.platform)

    def test_resolve_mapping_for_device_without_platform_ignores_device_type_mappings(self):
        device = Device(name="srv1", device_type=self.unmapped_type)
        self.assertIsInstance(resolve_mapping(device), DefaultMapping)

    def test_resolve_mapping_for_device_without_device_type_ignores_platform_mappings(self):
        device = Device(name="srv1", platform=None)
        self.assertIsInstance(resolve_mapping(device), DefaultMapping)

    def test_default_mapping_has_empty_interface_table_and_text(self):
        mapping = resolve_mapping(Device(name="srv1", device_type=self.unmapped_type))
        self.assertEqual(list(mapping.interface_mappings.all()), [])
        self.assertEqual(str(mapping), "Default mapping")

    def test_default_mapping_does_not_share_or_change_the_setting(self):
        setting_before = copy.deepcopy(get_app_settings_or_config(APP_NAME, "default_node_mapping"))
        device = Device(name="srv1", device_type=self.unmapped_type, platform=self.unmapped_platform)
        first = resolve_mapping(device)
        first.env["CLAB_MGMT_VRF"] = "MGMT"
        second = resolve_mapping(device)
        self.assertEqual(second.env, {})
        self.assertEqual(get_app_settings_or_config(APP_NAME, "default_node_mapping"), setting_before)

    def test_resolve_mapping_raises_improperly_configured_for_bad_setting(self):
        device = Device(name="srv1", device_type=self.unmapped_type)
        bad_settings = {
            "list": ["linux", "alpine:latest"],
            "no kind": {"image": "alpine:latest"},
            "no image": {"kind": "linux"},
            "kind 1": {"kind": 1, "image": "alpine:latest"},
            "kind Linux": {"kind": "Linux", "image": "alpine:latest"},
            "empty image": {"kind": "linux", "image": ""},
            "space image": {"kind": "linux", "image": " "},
            "image with a space": {"kind": "linux", "image": "alpine latest"},
            "unknown key": {"kind": "linux", "image": "alpine:latest", "imgae": "alpine:3"},
        }
        for label, value in bad_settings.items():
            with self.subTest(label), default_mapping_setting(value):
                with self.assertRaises(ImproperlyConfigured) as context:
                    resolve_mapping(device)
                if label == "unknown key":
                    self.assertIn("imgae", str(context.exception))


class MapInterfacesTestCase(MapperTestCase):
    """Test map_interfaces with unsaved interfaces of the unsaved device leaf1."""

    def setUp(self):
        """Make the unsaved device leaf1."""
        self.leaf1 = Device(name="leaf1", device_type=self.table_type)

    def interfaces(self, *names):
        """Return unsaved interfaces of leaf1 with the names, in the same order."""
        return [Interface(device=self.leaf1, name=name) for name in names]

    def test_map_interfaces_with_full_table_returns_table_names_and_no_warnings(self):
        interfaces = self.interfaces("GigabitEthernet0/10", "GigabitEthernet0/1", "GigabitEthernet0/2")
        container_names, warnings = map_interfaces(self.arista_mapping, interfaces)
        self.assertEqual(
            ordered_names(container_names),
            [("GigabitEthernet0/1", "eth1"), ("GigabitEthernet0/2", "eth2"), ("GigabitEthernet0/10", "eth10")],
        )
        self.assertEqual(warnings, [])
        self.assertEqual({id(key) for key in container_names}, {id(interface) for interface in interfaces})

    def test_map_interfaces_with_partial_table_gives_next_free_eth_names(self):
        container_names, _ = map_interfaces(self.table_mapping, self.interfaces(*PARTIAL_TABLE_NAMES))
        self.assertEqual(
            ordered_names(container_names),
            [("Ethernet1", "eth1"), ("Ethernet2", "eth3"), ("Ethernet3", "eth2"), ("Ethernet10", "eth4")],
        )

    def test_map_interfaces_with_empty_table_numbers_from_eth1_in_natural_order(self):
        for mapping in (self.nokia_mapping, DefaultMapping(kind="linux", image="alpine:latest")):
            with self.subTest(str(mapping)):
                container_names, _ = map_interfaces(mapping, self.interfaces("Ethernet10", "Ethernet2", "Ethernet1"))
                self.assertEqual(
                    ordered_names(container_names),
                    [("Ethernet1", "eth1"), ("Ethernet2", "eth2"), ("Ethernet10", "eth3")],
                )

    def test_map_interfaces_writes_one_warning_for_each_fallback(self):
        _, warnings = map_interfaces(self.table_mapping, self.interfaces(*PARTIAL_TABLE_NAMES))
        self.assertEqual(
            warnings,
            [
                "Device leaf1, interface Ethernet3: the interface table has no row. "
                "The ceos node uses eth2. Mapping: Device Type: Test Test table.",
                "Device leaf1, interface Ethernet10: the interface table has no row. "
                "The ceos node uses eth4. Mapping: Device Type: Test Test table.",
            ],
        )

    def test_map_interfaces_output_does_not_depend_on_input_order(self):
        ethernet10, ethernet3, ethernet2, ethernet1 = self.interfaces(*PARTIAL_TABLE_NAMES)
        results = [
            map_interfaces(self.table_mapping, order)
            for order in (
                [ethernet10, ethernet3, ethernet2, ethernet1],
                [ethernet1, ethernet2, ethernet3, ethernet10],
                [ethernet3, ethernet1, ethernet10, ethernet2],
            )
        ]
        first_names, first_warnings = results[0]
        for container_names, warnings in results[1:]:
            self.assertEqual(list(container_names.items()), list(first_names.items()))
            self.assertEqual(warnings, first_warnings)

    def test_map_interfaces_matches_physical_names_exactly(self):
        container_names, warnings = map_interfaces(self.arista_mapping, self.interfaces("gigabitethernet0/1"))
        self.assertEqual(ordered_names(container_names), [("gigabitethernet0/1", "eth3")])
        self.assertEqual(len(warnings), 1)

    def test_map_interfaces_with_no_interfaces_returns_empty_result(self):
        self.assertEqual(map_interfaces(self.table_mapping, []), ({}, []))

    def test_map_interfaces_sorts_names_with_one_natural_key_by_name(self):
        container_names, _ = map_interfaces(self.nokia_mapping, self.interfaces("eth1", "eth01"))
        self.assertEqual(ordered_names(container_names), [("eth01", "eth1"), ("eth1", "eth2")])

    def test_map_interfaces_raises_value_error_for_more_than_one_device_or_no_device(self):
        leaf2 = Device(name="leaf2", device_type=self.table_type)
        inputs = {
            "two devices": [*self.interfaces("Ethernet1"), Interface(device=leaf2, name="Ethernet2")],
            "no device": [Interface(name="eth9")],
        }
        for label, interfaces in inputs.items():
            with self.subTest(label):
                with self.assertRaisesMessage(ValueError, "map_interfaces needs the interfaces of one device."):
                    map_interfaces(self.table_mapping, interfaces)


class MapperDatabaseTestCase(MapperTestCase):
    """Test the resolver and the mapper with a saved device, saved interfaces, and a module interface."""

    @classmethod
    def setUpTestData(cls):
        """Create the saved device leaf1 with three interfaces and one module interface."""
        super().setUpTestData()
        device_content_type = ContentType.objects.get_for_model(Device)
        location_type = LocationType.objects.create(name="Test site type")
        location_type.content_types.add(device_content_type)
        active = Status.objects.get_for_model(Device).get(name="Active")
        location = Location.objects.create(name="Test site", location_type=location_type, status=active)
        role = Role.objects.create(name="Test leaf")
        role.content_types.add(device_content_type)
        cls.device = Device.objects.create(
            name="leaf1", device_type=cls.arista_mapping.device_type, location=location, status=active, role=role
        )
        cls.interface_status = Status.objects.get_for_model(Interface).get(name="Active")
        for name in ("GigabitEthernet0/10", "GigabitEthernet0/3", "GigabitEthernet0/1"):
            Interface.objects.create(device=cls.device, name=name, type="1000base-t", status=cls.interface_status)
        module_type = ModuleType.objects.create(manufacturer=cls.arista_mapping.device_type.manufacturer, model="Test")
        module_bay = ModuleBay.objects.create(parent_device=cls.device, name="slot1")
        module = Module.objects.create(module_type=module_type, parent_module_bay=module_bay, status=active)
        cls.module_interface = Interface.objects.create(
            module=module, name="Ethernet1/1", type="1000base-t", status=cls.interface_status
        )

    def test_resolve_mapping_query_count(self):
        """Count the Device Type lookup with its joins and the prefetch of the interface rows."""
        with self.assertNumQueries(2):
            mapping = resolve_mapping(self.device)
            str(mapping)
            list(mapping.interface_mappings.all())
        self.assertEqual(mapping, self.arista_mapping)

    def test_resolve_and_map_saved_device_interfaces(self):
        mapping = resolve_mapping(self.device)
        container_names, warnings = map_interfaces(mapping, self.device.all_interfaces)
        self.assertEqual(mapping, self.arista_mapping)
        self.assertEqual(
            ordered_names(container_names),
            [
                ("GigabitEthernet0/1", "eth1"),
                ("GigabitEthernet0/3", "eth3"),
                ("GigabitEthernet0/10", "eth10"),
                ("Ethernet1/1", "eth4"),
            ],
        )
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(warning.startswith("Device leaf1, ") for warning in warnings))

    def test_map_interfaces_skips_second_interface_with_same_name(self):
        """Map the device interface and skip the module interface with the same name.

        The device interface is not in the Arista table, so it also gets a fallback warning.
        """
        device_interface = Interface.objects.create(
            device=self.device, name="Ethernet1/1", type="1000base-t", status=self.interface_status
        )
        expected_warnings = [
            "Device leaf1, interface Ethernet1/1: the interface table has no row. "
            "The ceos node uses eth3. Mapping: Device Type: Arista DCS-7280.",
            f"Device leaf1, interface Ethernet1/1 (id {self.module_interface.pk}): another interface of the device "
            "has the same name. This interface gets no container name.",
        ]
        orders = {
            "device first": [device_interface, self.module_interface],
            "module first": [self.module_interface, device_interface],
        }
        for label, order in orders.items():
            with self.subTest(label):
                container_names, warnings = map_interfaces(self.arista_mapping, order)
                self.assertEqual(list(container_names), [device_interface])
                self.assertEqual(warnings, expected_warnings)
