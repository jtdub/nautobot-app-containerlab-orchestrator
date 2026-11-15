"""Tests for the topology builder."""

import json
from pathlib import Path

import yaml
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from jsonschema import Draft7Validator
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Device, Location, LocationType
from nautobot.extras.models import Role, Status

from containerlab_orchestrator import topology
from containerlab_orchestrator.models import Lab, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_interface_mappings,
    create_lab_devices,
    create_node_mappings,
)
from containerlab_orchestrator.topology import (
    CLAB_NAME_PATTERN,
    EMPTY_DEVICES_MESSAGE,
    SUBNET_ROOM_MESSAGE,
    build_topology,
    to_yaml,
)

CLAB_SCHEMA_PATH = Path(__file__).parent / "vendor" / "clab.schema.json"
UNICODE_CLASS_TEXT = r"\\p{N}\\p{L}"
ASCII_CLASS_TEXT = "0-9A-Za-z_"


def load_clab_schema():
    """Return a validator for the vendored containerlab v0.83.0 schema.

    Python `re` cannot compile the `\\p{N}\\p{L}` classes of the schema, so the loader replaces them with an
    ASCII class before it parses the text.
    """
    text = CLAB_SCHEMA_PATH.read_text(encoding="utf-8")
    assert text.count(UNICODE_CLASS_TEXT) == 4
    return Draft7Validator(json.loads(text.replace(UNICODE_CLASS_TEXT, ASCII_CLASS_TEXT)))


def new_lab(name="lab-a", mgmt_subnet="172.20.21.0/24"):
    """Return an unsaved lab with the name and the management subnet."""
    return Lab(name=name, mgmt_subnet=mgmt_subnet)


class TopologyTestCase(TestCase):
    """Base class with the mapped devices and the locations of the topology tests."""

    @classmethod
    def setUpTestData(cls):
        """Create the Arista and Nokia devices, the default-mapping devices, and two locations."""
        node_mappings = create_node_mappings()
        create_interface_mappings(node_mappings)
        cls.arista_mapping, cls.nokia_mapping, _ = node_mappings
        lab_devices = create_lab_devices()
        cls.leaf1, cls.leaf2, cls.spine1 = (lab_devices[name] for name in ("leaf1", "leaf2", "spine1"))
        device_content_type = ContentType.objects.get_for_model(Device)
        location_type = LocationType.objects.create(name="Topology site type")
        location_type.content_types.add(device_content_type)
        cls.active = Status.objects.get_for_model(Device).get(name="Active")
        cls.site1, cls.site2 = (
            Location.objects.create(name=f"Topology site {number}", location_type=location_type, status=cls.active)
            for number in (1, 2)
        )
        cls.role = Role.objects.create(name="topology")
        cls.role.content_types.add(device_content_type)
        cls.default_type = cls.leaf1.device_type
        cls.arista1 = cls.add_device("arista1", cls.arista_mapping.device_type)
        cls.nokia1 = cls.add_device("nokia1", cls.nokia_mapping.device_type)

    @classmethod
    def add_device(cls, name, device_type=None, location=None):
        """Return a saved device with the name, in Topology site 1 unless the location is given."""
        return Device.objects.create(
            name=name,
            device_type=device_type or cls.default_type,
            location=location or cls.site1,
            status=cls.active,
            role=cls.role,
        )

    def use_full_kind_names(self):
        """Change the Arista mapping kind from the alias ceos to arista_ceos, which the schema accepts."""
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(kind="arista_ceos")

    def node_names(self, devices, lab=None):
        """Return the node names of a topology of the devices, in node order."""
        built, _, _ = build_topology(lab or new_lab(), devices)
        return list(built["topology"]["nodes"])


class BuildTopologyNodesTestCase(TopologyTestCase):
    """Test the nodes, the node names, and the management addresses of build_topology."""

    def test_build_topology_gives_one_node_per_device(self):
        built, warnings, _ = build_topology(new_lab(), [self.leaf1, self.leaf2, self.spine1])
        nodes = built["topology"]["nodes"]
        self.assertEqual(list(nodes), ["leaf1", "leaf2", "spine1"])
        for node in nodes.values():
            self.assertEqual((node["kind"], node["image"]), ("linux", "alpine:latest"))
        self.assertEqual(warnings, [])

    def test_node_keys_come_from_mapping(self):
        built, _, _ = build_topology(new_lab(), [self.nokia1, self.arista1])
        nodes = built["topology"]["nodes"]
        self.assertEqual(
            list(nodes["arista1"].items()),
            [
                ("kind", "ceos"),
                ("image", "ceos:4.32.0F"),
                ("cpu", 2.0),
                ("memory", "2Gb"),
                ("env", {"CLAB_MGMT_VRF": "MGMT"}),
                ("mgmt-ipv4", "172.20.21.2"),
            ],
        )
        self.assertIsInstance(nodes["arista1"]["cpu"], float)
        self.assertEqual(
            nodes["nokia1"],
            {
                "kind": "nokia_srlinux",
                "image": "ghcr.io/nokia/srlinux:24.10",
                "type": "ixrd3",
                "mgmt-ipv4": "172.20.21.3",
            },
        )

    def test_node_names_follow_the_containerlab_rule(self):
        long_name = "x" * 80
        devices = [self.add_device(name) for name in ("Leaf 1/A", "_core", long_name, "///")]
        built, _, node_devices = build_topology(new_lab(), devices)
        names = {device.name: name for name, device in node_devices.items()}
        self.assertEqual(names["Leaf 1/A"], "Leaf-1-A")
        self.assertEqual(names["_core"], "core")
        self.assertEqual(names[long_name], "x" * 63)
        self.assertEqual(names["///"], f"device-{devices[3].pk.hex[:8]}")
        for name in built["topology"]["nodes"]:
            self.assertIsNotNone(CLAB_NAME_PATTERN.fullmatch(name))
            self.assertLessEqual(len(name), 63)

    def test_node_names_are_unique_without_case(self):
        devices = [
            self.add_device("R1", location=self.site1),
            self.add_device("r1", location=self.site2),
            self.add_device("r1-2", location=self.site1),
        ]
        _, _, node_devices = build_topology(new_lab(), devices)
        names = {device.pk: name for name, device in node_devices.items()}
        self.assertEqual([names[device.pk] for device in devices], ["R1", "r1-3", "r1-2"])

    def test_suffixed_node_name_keeps_63_characters(self):
        devices = [self.add_device("X" * 80, location=self.site1), self.add_device("x" * 80, location=self.site2)]
        _, _, node_devices = build_topology(new_lab(), devices)
        names = {device.pk: name for name, device in node_devices.items()}
        self.assertEqual([names[device.pk] for device in devices], ["X" * 63, "x" * 61 + "-2"])

    def test_unnamed_device_gets_a_name_from_its_pk(self):
        unnamed = self.add_device(None)
        self.assertEqual(self.node_names([unnamed]), [f"device-{unnamed.pk.hex[:8]}"])

    def test_mgmt_section_and_addresses_follow_node_order(self):
        unnamed = self.add_device(None)
        devices = [self.add_device("leaf0"), self.add_device("Leaf 1"), unnamed]
        built, _, _ = build_topology(new_lab(), devices)
        self.assertEqual(built["mgmt"], {"network": "clab-lab-a", "ipv4-subnet": "172.20.21.0/24"})
        nodes = built["topology"]["nodes"]
        self.assertEqual(list(nodes), [f"device-{unnamed.pk.hex[:8]}", "Leaf-1", "leaf0"])
        self.assertEqual([node["mgmt-ipv4"] for node in nodes.values()], ["172.20.21.2", "172.20.21.3", "172.20.21.4"])

    def test_subnet_too_small_raises(self):
        with self.assertRaises(ValidationError) as context:
            build_topology(new_lab(mgmt_subnet="10.0.0.0/30"), [self.leaf1, self.leaf2])
        self.assertEqual(context.exception.message_dict, {"mgmt_subnet": [SUBNET_ROOM_MESSAGE.format(room=1, count=2)]})

    def test_bad_subnet_raises(self):
        with self.assertRaises(ValidationError) as context:
            build_topology(new_lab(mgmt_subnet="10.0.0.1/24"), [self.leaf1])
        self.assertEqual(list(context.exception.message_dict), ["mgmt_subnet"])

    def test_lab_name_with_leading_underscore_raises(self):
        with self.assertRaises(ValidationError) as context:
            build_topology(new_lab(name="_lab"), [self.leaf1])
        self.assertEqual(context.exception.message_dict, {"name": [topology.LAB_NAME_MESSAGE]})

    def test_empty_devices_raise(self):
        with self.assertRaises(ValidationError) as context:
            build_topology(new_lab(), [])
        self.assertEqual(context.exception.messages, [EMPTY_DEVICES_MESSAGE.format(lab="lab-a")])

    def test_same_device_twice_gives_one_node(self):
        self.assertEqual(self.node_names([self.leaf1, self.leaf1]), ["leaf1"])

    def test_topology_without_cables_has_no_links_key(self):
        built, _, _ = build_topology(new_lab(), [self.leaf1, self.leaf2])
        self.assertEqual(list(built), ["name", "mgmt", "topology"])
        self.assertEqual(built["name"], "lab-a")
        self.assertNotIn("links", built["topology"])

    def test_node_devices_maps_each_name_to_its_device(self):
        _, _, node_devices = build_topology(new_lab(), [self.spine1, self.arista1])
        self.assertEqual(node_devices, {"arista1": self.arista1, "spine1": self.spine1})


class TopologyYamlTestCase(TopologyTestCase):
    """Test the YAML text of a topology and the containerlab schema check."""

    def test_same_input_gives_same_yaml(self):
        devices = [self.leaf1, self.arista1, self.nokia1, self.spine1]
        first, _, _ = build_topology(new_lab(), devices)
        second, _, _ = build_topology(new_lab(), list(reversed(devices)))
        self.assertEqual(to_yaml(first), to_yaml(second))

    def test_to_yaml_round_trips_and_keeps_key_order(self):
        built, _, _ = build_topology(new_lab(), [self.arista1, self.leaf1])
        text = to_yaml(built)
        self.assertEqual(yaml.safe_load(text), built)
        self.assertTrue(text.startswith("name:"))

    def test_load_clab_schema_replaces_four_patterns(self):
        validator = load_clab_schema()
        self.assertNotIn(UNICODE_CLASS_TEXT, json.dumps(validator.schema))
        self.assertTrue(validator.is_valid({"name": "lab", "topology": {"nodes": {}}}))

    def test_topology_passes_containerlab_schema(self):
        self.use_full_kind_names()
        validator = load_clab_schema()
        for devices in ([self.leaf1, self.leaf2, self.spine1], [self.arista1, self.nokia1]):
            built, _, _ = build_topology(new_lab(), devices)
            self.assertEqual([error.message for error in validator.iter_errors(built)], [])
