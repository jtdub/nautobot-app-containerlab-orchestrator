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
from containerlab_orchestrator.mapper import DefaultMapping, resolve_mapping
from containerlab_orchestrator.models import InterfaceMapping, NodeMapping
from containerlab_orchestrator.tests.fixtures import create_interface_mappings, create_node_mappings


def default_mapping_setting(value):
    """Return a settings override that sets default_node_mapping to the value."""
    return override_settings(PLUGINS_CONFIG={APP_NAME: {"default_node_mapping": value}})


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
