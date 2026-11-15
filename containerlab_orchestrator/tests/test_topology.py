"""Tests for the topology builder."""

import json
from pathlib import Path
from unittest import mock, skipUnless

import yaml
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import override_settings
from jsonschema import Draft7Validator
from nautobot.apps.testing import TestCase
from nautobot.circuits.models import Circuit, CircuitTermination, CircuitType, Provider
from nautobot.dcim import models as dcim_models
from nautobot.dcim.models import (
    Cable,
    Device,
    FrontPort,
    Interface,
    Location,
    LocationType,
    Module,
    ModuleBay,
    ModuleType,
    RearPort,
)
from nautobot.extras.models import Role, Status

from containerlab_orchestrator import topology
from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.models import InterfaceMapping, Lab, NodeMapping
from containerlab_orchestrator.tests.fixtures import (
    create_interface_mappings,
    create_lab_devices,
    create_node_mappings,
)
from containerlab_orchestrator.tests.test_app_config import validated_config
from containerlab_orchestrator.topology import (
    CLAB_NAME_PATTERN,
    EMPTY_DEVICES_MESSAGE,
    KIND_VARIABLE_MESSAGE,
    SUBNET_ROOM_MESSAGE,
    W_BREAKOUT,
    W_MGMT_ONLY,
    W_NAME_VARIABLE,
    W_NO_FAR_DEVICE,
    W_NO_FAR_END,
    W_NO_NAME,
    W_NOT_INTERFACE,
    W_OVERRIDE,
    W_VARIABLE,
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
        cls.arista2 = cls.add_device("arista2", cls.arista_mapping.device_type)
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

    @classmethod
    def add_interfaces(cls, device, *names, mgmt_only=False):
        """Return a saved interface of the device for each name."""
        status = Status.objects.get_for_model(Interface).get(name="Active")
        return [
            Interface.objects.create(device=device, name=name, type="1000base-t", status=status, mgmt_only=mgmt_only)
            for name in names
        ]

    @staticmethod
    def connect(termination_a, termination_b, status_name="Connected"):
        """Return a saved cable between the two terminations, with the status name."""
        cable = Cable(
            termination_a=termination_a,
            termination_b=termination_b,
            status=Status.objects.get_for_model(Cable).get(name=status_name),
        )
        cable.validated_save()
        return cable

    @classmethod
    def add_module_interface(cls, device, name, bay=True):
        """Return a saved interface on a new module, which is in a bay of the device, or in no bay."""
        module_type = ModuleType.objects.create(manufacturer=device.device_type.manufacturer, model=f"Module {name}")
        if bay:
            place = {"parent_module_bay": ModuleBay.objects.create(parent_device=device, name=f"bay {name}")}
        else:
            place = {"location": cls.site1}
        module = Module.objects.create(module_type=module_type, status=cls.active, **place)
        status = Status.objects.get_for_model(Interface).get(name="Active")
        return Interface.objects.create(module=module, name=name, type="1000base-t", status=status)

    def cable_warning(self, interface, text):
        """Return the warning that the builder gives for a cable of the interface."""
        return f"Device {interface.parent}, interface {interface.name}: {text}"

    def links(self, devices):
        """Return the links and the warnings of a topology of the devices."""
        built, warnings, _ = build_topology(new_lab(), devices)
        return [link["endpoints"] for link in built["topology"].get("links", [])], warnings

    def use_full_kind_names(self):
        """Change the Arista mapping kind from the alias ceos to arista_ceos, which the schema accepts."""
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(kind="arista_ceos")

    def set_arista_overrides(self, overrides):
        """Set the overrides of the Arista mapping row."""
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(overrides=overrides)

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


class BuildTopologyOverridesTestCase(TopologyTestCase):
    """Test the overrides allow-list of build_topology."""

    def test_overrides_merge_deeply_for_allowed_keys(self):
        self.set_arista_overrides({"env": {"B": "2"}, "labels": {"x": "y"}, "image": "ceos:4.33"})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        self.assertEqual(
            list(built["topology"]["nodes"]["arista1"].items()),
            [
                ("kind", "ceos"),
                ("image", "ceos:4.33"),
                ("cpu", 2.0),
                ("memory", "2Gb"),
                ("env", {"CLAB_MGMT_VRF": "MGMT", "B": "2"}),
                ("labels", {"x": "y"}),
                ("mgmt-ipv4", "172.20.21.2"),
            ],
        )
        self.assertEqual(warnings, [])

    def test_overrides_drop_other_keys_with_one_warning_for_each_mapping(self):
        self.set_arista_overrides(
            {"exec": ["touch /tmp/x"], "binds": ["/etc:/host-etc"], "kind": "x", "mgmt-ipv4": "1.1.1.1"}
        )
        built, warnings, _ = build_topology(new_lab(), [self.arista1, self.arista2])
        nodes = built["topology"]["nodes"]
        for name, address in (("arista1", "172.20.21.2"), ("arista2", "172.20.21.3")):
            self.assertEqual(nodes[name]["kind"], "ceos")
            self.assertEqual(nodes[name]["mgmt-ipv4"], address)
            self.assertNotIn("exec", nodes[name])
            self.assertNotIn("binds", nodes[name])
        self.assertEqual(
            warnings, [W_OVERRIDE.format(mapping=self.arista_mapping, keys="binds, exec, kind, mgmt-ipv4")]
        )

    def test_default_mapping_adds_no_override_warning(self):
        built, warnings, _ = build_topology(new_lab(), [self.leaf1, self.leaf2])
        self.assertEqual(warnings, [])
        self.assertEqual(
            built["topology"]["nodes"]["leaf1"],
            {"kind": "linux", "image": "alpine:latest", "mgmt-ipv4": "172.20.21.2"},
        )

    def test_build_leaves_the_mapping_unchanged(self):
        overrides = {"env": {"A": "$HOME", "B": "2"}, "labels": {"x": "y"}}
        self.set_arista_overrides(overrides)
        built, _, _ = build_topology(new_lab(), [self.arista1])
        stored = NodeMapping.objects.get(pk=self.arista_mapping.pk)
        self.assertEqual(stored.overrides, overrides)
        self.assertEqual(stored.env, {"CLAB_MGMT_VRF": "MGMT"})
        built["topology"]["nodes"]["arista1"]["env"]["B"] = "changed"
        built["topology"]["nodes"]["arista1"]["labels"]["x"] = "changed"
        second, _, _ = build_topology(new_lab(), [self.arista1])
        node = second["topology"]["nodes"]["arista1"]
        self.assertEqual((node["env"], node["labels"]), ({"CLAB_MGMT_VRF": "MGMT", "B": "2"}, {"x": "y"}))


class BuildTopologyVariablesTestCase(TopologyTestCase):
    """Test that build_topology drops each value that containerlab fills from the host environment."""

    def variable_warning(self, keys):
        """Return the W_VARIABLE warning of the Arista mapping for the keys."""
        return W_VARIABLE.format(mapping=self.arista_mapping, keys=keys)

    def test_override_with_a_variable_is_dropped(self):
        self.set_arista_overrides(
            {"image": "registry.example/x:${X}", "labels": {"$k": "v"}, "env": {"A": "$HOME"}, "cpu-set": "0-1"}
        )
        built, warnings, _ = build_topology(new_lab(), [self.arista1, self.arista2])
        node = built["topology"]["nodes"]["arista1"]
        self.assertEqual(node["image"], "ceos:4.32.0F")
        self.assertEqual(node["env"], {"CLAB_MGMT_VRF": "MGMT"})
        self.assertEqual(node["cpu-set"], "0-1")
        self.assertNotIn("labels", node)
        self.assertEqual(warnings, [self.variable_warning("env.A, image, labels")])

    def test_mapping_value_with_a_variable_is_dropped(self):
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(
            image="ceos:${TAG}", env={"A": "${SECRET}", "B": "2"}
        )
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        node = built["topology"]["nodes"]["arista1"]
        self.assertNotIn("image", node)
        self.assertEqual(node["env"], {"B": "2"})
        self.assertEqual(warnings, [self.variable_warning("env.A, image")])

    def test_nested_variable_drops_the_key(self):
        self.set_arista_overrides({"labels": {"team": "lab", "rack": {"row": ["${ROW}"]}}, "env": {"B": {"C": "$X"}}})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        node = built["topology"]["nodes"]["arista1"]
        self.assertNotIn("labels", node)
        self.assertEqual(node["env"], {"CLAB_MGMT_VRF": "MGMT"})
        self.assertEqual(warnings, [self.variable_warning("env.B, labels")])

    def test_import_envs_drops_the_env_entry(self):
        self.set_arista_overrides({"env": {"__IMPORT_ENVS": True, "B": "2"}})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        self.assertEqual(built["topology"]["nodes"]["arista1"]["env"], {"CLAB_MGMT_VRF": "MGMT", "B": "2"})
        self.assertEqual(warnings, [self.variable_warning("env.__IMPORT_ENVS")])

    def test_import_envs_alone_drops_the_env_key(self):
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(env={"__IMPORT_ENVS": "true"})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        self.assertNotIn("env", built["topology"]["nodes"]["arista1"])
        self.assertEqual(warnings, [self.variable_warning("env.__IMPORT_ENVS")])

    def test_mapping_type_and_memory_with_a_variable_are_dropped(self):
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(node_type="${TYPE}", memory="${MEMORY}")
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        node = built["topology"]["nodes"]["arista1"]
        self.assertNotIn("type", node)
        self.assertNotIn("memory", node)
        self.assertEqual(warnings, [self.variable_warning("memory, type")])

    def test_override_with_a_template_action_is_dropped(self):
        self.set_arista_overrides({"labels": {"x": '{{ "\'\n      binds: ["/:/host"]\n      y: \'" }}'}})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        self.assertNotIn("labels", built["topology"]["nodes"]["arista1"])
        self.assertEqual(warnings, [self.variable_warning("labels")])

    def test_mapping_value_with_a_template_action_is_dropped(self):
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(
            image="ceos:{{.Tag}}", env={"A": r'{{"\x24{API_SECRET}"}}', "B": "2"}
        )
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        node = built["topology"]["nodes"]["arista1"]
        self.assertNotIn("image", node)
        self.assertEqual(node["env"], {"B": "2"})
        self.assertEqual(warnings, [self.variable_warning("env.A, image")])

    def test_kind_with_a_variable_raises(self):
        for kind in ("ceos${X}", "ceos{{.X}}"):
            with self.subTest(kind=kind):
                NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(kind=kind)
                with self.assertRaises(ValidationError) as context:
                    build_topology(new_lab(), [self.arista1])
                self.assertEqual(
                    context.exception.messages, [KIND_VARIABLE_MESSAGE.format(mapping=self.arista_mapping)]
                )


class BuildTopologyLinksTestCase(TopologyTestCase):
    """Test the links between the selected devices and the cables that build_topology skips."""

    def test_cable_between_selected_devices_gives_link(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        (far,) = self.add_interfaces(self.arista2, "GigabitEthernet0/2")
        self.connect(local, far)
        self.assertEqual(self.links([self.arista2, self.arista1]), ([["arista1:eth1", "arista2:eth2"]], []))

    def test_circuit_cable_is_skipped_with_warning(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        circuit = Circuit.objects.create(
            cid="circuit-1",
            provider=Provider.objects.create(name="Provider 1"),
            circuit_type=CircuitType.objects.create(name="Transit"),
            status=Status.objects.get_for_model(Circuit).first(),
        )
        self.connect(local, CircuitTermination.objects.create(circuit=circuit, term_side="A", location=self.site1))
        self.assertEqual(
            self.links([self.arista1]),
            ([], [self.cable_warning(local, W_NOT_INTERFACE.format(kind="circuit termination"))]),
        )

    def test_front_port_cable_is_skipped_with_warning(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        rear_port = RearPort.objects.create(device=self.arista2, name="rear1", type="8p8c", positions=1)
        front_port = FrontPort.objects.create(
            device=self.arista2, name="front1", type="8p8c", rear_port=rear_port, rear_port_position=1
        )
        self.connect(local, front_port)
        self.assertEqual(
            self.links([self.arista1, self.arista2]),
            ([], [self.cable_warning(local, W_NOT_INTERFACE.format(kind="front port"))]),
        )

    def test_mgmt_only_cable_is_skipped_with_one_warning(self):
        local_mgmt, local_data = self.add_interfaces(self.arista1, "GigabitEthernet0/1", "GigabitEthernet0/2")
        local_mgmt.mgmt_only = True
        local_mgmt.save()
        (far_data,) = self.add_interfaces(self.arista2, "GigabitEthernet0/1")
        (far_mgmt,) = self.add_interfaces(self.arista2, "GigabitEthernet0/2", mgmt_only=True)
        self.connect(local_mgmt, far_data)
        self.connect(local_data, far_mgmt)
        self.assertEqual(
            self.links([self.arista1, self.arista2]),
            ([], [self.cable_warning(local_mgmt, W_MGMT_ONLY), self.cable_warning(local_data, W_MGMT_ONLY)]),
        )

    def test_planned_and_decommissioning_cables_are_linked(self):
        local = self.add_interfaces(self.arista1, "GigabitEthernet0/1", "GigabitEthernet0/2")
        far = self.add_interfaces(self.arista2, "GigabitEthernet0/1", "GigabitEthernet0/2")
        self.connect(local[0], far[0], status_name="Planned")
        self.connect(local[1], far[1], status_name="Decommissioning")
        self.assertEqual(
            self.links([self.arista1, self.arista2]),
            ([["arista1:eth1", "arista2:eth1"], ["arista1:eth2", "arista2:eth2"]], []),
        )

    def test_cable_on_one_device_keeps_link(self):
        self.connect(*self.add_interfaces(self.arista1, "GigabitEthernet0/2", "GigabitEthernet0/1"))
        self.assertEqual(self.links([self.arista1]), ([["arista1:eth1", "arista1:eth2"]], []))

    def test_module_interface_cable_gives_link(self):
        module_interface = self.add_module_interface(self.arista1, "Ethernet1/1")
        self.connect(module_interface, self.add_interfaces(self.arista2, "GigabitEthernet0/1")[0])
        links, warnings = self.links([self.arista1, self.arista2])
        self.assertEqual(links, [["arista1:eth3", "arista2:eth1"]])
        self.assertEqual(len(warnings), 1)
        self.assertIn("interface Ethernet1/1: the interface table has no row", warnings[0])

    def test_interface_without_container_name_skips_link(self):
        (device_interface,) = self.add_interfaces(self.arista1, "Ethernet1/1")
        module_interface = self.add_module_interface(self.arista1, "Ethernet1/1")
        far = self.add_interfaces(self.arista2, "GigabitEthernet0/1", "GigabitEthernet0/2")
        self.connect(device_interface, far[0])
        self.connect(module_interface, far[1])
        links, warnings = self.links([self.arista1, self.arista2])
        self.assertEqual(links, [["arista1:eth3", "arista2:eth1"]])
        self.assertIn("This interface gets no container name.", warnings[-2])
        self.assertEqual(warnings[-1], self.cable_warning(module_interface, W_NO_NAME))

    def test_container_name_with_a_variable_skips_link(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        (far,) = self.add_interfaces(self.arista2, "GigabitEthernet0/2")
        self.connect(local, far)
        rows = InterfaceMapping.objects.filter(node_mapping=self.arista_mapping, physical_name="GigabitEthernet0/2")
        for container_name in ("eth${X}", "eth{{.X}}"):
            with self.subTest(container_name=container_name):
                rows.update(container_name=container_name)
                self.assertEqual(
                    self.links([self.arista1, self.arista2]), ([], [self.cable_warning(far, W_NAME_VARIABLE)])
                )

    def test_more_than_one_far_end_is_skipped(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        (far,) = self.add_interfaces(self.arista2, "GigabitEthernet0/1")
        self.connect(local, far)
        cases = (
            ({local.pk: [far, far], far.pk: [local]}, W_BREAKOUT),
            ({local.pk: [far], far.pk: [local, local]}, W_BREAKOUT),
            ({local.pk: [], far.pk: [local]}, W_NO_FAR_END),
        )
        for peers, text in cases:
            with self.subTest(text=text, peers=len(peers[local.pk])):
                with mock.patch.object(topology, "_cable_peers", side_effect=lambda end, peers=peers: peers[end.pk]):
                    self.assertEqual(self.links([self.arista1, self.arista2]), ([], [self.cable_warning(local, text)]))

    def test_multi_connector_cable_is_skipped(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        self.connect(local, self.add_interfaces(self.arista2, "GigabitEthernet0/1")[0])
        with mock.patch.object(topology, "_is_multi_connector", return_value=True):
            self.assertEqual(self.links([self.arista1, self.arista2]), ([], [self.cable_warning(local, W_BREAKOUT)]))

    @skipUnless(hasattr(Interface, "get_cable_peers"), "Multi-connector cables exist on Nautobot 3.2 and later.")
    def test_two_by_two_cable_is_skipped(self):
        # pylint: disable=no-member
        mesh_type = dcim_models.CableType(
            name="Topology 2x2",
            a_connectors=2,
            b_connectors=2,
            total_lanes=4,
            mapping=[
                {"a_connector": 1, "a_position": 1, "b_connector": 1, "b_position": 1},
                {"a_connector": 1, "a_position": 2, "b_connector": 2, "b_position": 1},
                {"a_connector": 2, "a_position": 1, "b_connector": 1, "b_position": 2},
                {"a_connector": 2, "a_position": 2, "b_connector": 2, "b_position": 2},
            ],
        )
        mesh_type.validated_save()
        a1, a2 = self.add_interfaces(self.arista1, "GigabitEthernet0/1", "GigabitEthernet0/2")
        b1, b2 = self.add_interfaces(self.arista2, "GigabitEthernet0/1", "GigabitEthernet0/2")
        cable = Cable(
            termination_a=a1, termination_b=b1, cable_type=mesh_type, status=Status.objects.get_for_model(Cable).first()
        )
        cable.save()
        cable.add_termination(a2, "A", connector=2)
        cable.add_termination(b2, "B", connector=2)
        self.assertEqual(self.links([self.arista1, self.arista2]), ([], [self.cable_warning(a1, W_BREAKOUT)]))

    def test_far_interface_with_no_device_is_skipped(self):
        (local,) = self.add_interfaces(self.arista1, "GigabitEthernet0/1")
        self.connect(local, self.add_module_interface(self.arista2, "Ethernet9/1", bay=False))
        self.assertEqual(self.links([self.arista1]), ([], [self.cable_warning(local, W_NO_FAR_DEVICE)]))


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

    def test_topology_with_overrides_passes_schema(self):
        self.use_full_kind_names()
        self.set_arista_overrides(
            {
                "env": {"B": "2"},
                "labels": {"team": "lab", "rack": 4},
                "cpu-set": "0-1",
                "shm-size": "1gb",
                "image-pull-policy": "IfNotPresent",
                "startup-delay": 5,
            }
        )
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2, self.nokia1, self.leaf1])
        self.assertEqual(built["topology"]["nodes"]["arista1"]["startup-delay"], 5)
        self.assertEqual([error.message for error in load_clab_schema().iter_errors(built)], [])

    def test_links_pass_schema_and_repeat(self):
        self.use_full_kind_names()
        self.connect(
            self.add_interfaces(self.arista1, "GigabitEthernet0/1")[0],
            self.add_interfaces(self.arista2, "GigabitEthernet0/2")[0],
        )
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2])
        second, _, _ = build_topology(new_lab(), [self.arista2, self.arista1])
        self.assertEqual(built["topology"]["links"], [{"endpoints": ["arista1:eth1", "arista2:eth2"]}])
        self.assertEqual(to_yaml(built), to_yaml(second))
        self.assertEqual([error.message for error in load_clab_schema().iter_errors(built)], [])


class BuildTopologyStubsTestCase(TopologyTestCase):
    """Test the stub nodes that build_topology adds for the devices outside the selection."""

    @classmethod
    def setUpTestData(cls):
        """Add the outside devices wan1 and wan2."""
        super().setUpTestData()
        cls.wan1 = cls.add_device("wan1")
        cls.wan2 = cls.add_device("wan2")

    def cable_to_outside(self, local_device, local_name, far_device, far_name):
        """Return the local and the far interface of a new cable between the two devices."""
        (local,) = self.add_interfaces(local_device, local_name)
        (far,) = self.add_interfaces(far_device, far_name)
        self.connect(local, far)
        return local, far

    def cable_three_stub_links(self):
        """Cable two arista1 interfaces to wan1, and one arista2 interface to wan2."""
        self.cable_to_outside(self.arista1, "GigabitEthernet0/1", self.wan1, "GigabitEthernet0/2")
        self.cable_to_outside(self.arista1, "GigabitEthernet0/2", self.wan1, "GigabitEthernet0/1")
        self.cable_to_outside(self.arista2, "GigabitEthernet0/1", self.wan2, "GigabitEthernet0/1")

    def test_outside_devices_give_one_stub_each(self):
        self.cable_three_stub_links()
        built, warnings, node_devices = build_topology(new_lab(), [self.arista2, self.arista1])
        nodes = built["topology"]["nodes"]
        self.assertEqual(list(nodes), ["arista1", "arista2", "stub-wan1", "stub-wan2"])
        for name, address in (("stub-wan1", "172.20.21.4"), ("stub-wan2", "172.20.21.5")):
            self.assertEqual(nodes[name], {"kind": "linux", "image": "alpine:latest", "mgmt-ipv4": address})
        links = [link["endpoints"] for link in built["topology"]["links"]]
        self.assertEqual(
            links,
            [
                ["arista1:eth1", "stub-wan1:eth2"],
                ["arista1:eth2", "stub-wan1:eth1"],
                ["arista2:eth1", "stub-wan2:eth1"],
            ],
        )
        for _, stub_end in links:
            self.assertRegex(stub_end, r"^stub-wan[12]:eth[1-9][0-9]*$")
        self.assertEqual(
            node_devices, {"arista1": self.arista1, "arista2": self.arista2, "stub-wan1": None, "stub-wan2": None}
        )
        self.assertEqual(warnings, [])

    def test_stub_names_follow_the_containerlab_rule(self):
        far_devices = [self.add_device("WAN 1/A"), self.add_device("///")]
        for number, far_device in enumerate(far_devices, start=1):
            self.cable_to_outside(self.arista1, f"GigabitEthernet0/{number}", far_device, "GigabitEthernet0/1")
        self.cable_to_outside(self.arista2, "GigabitEthernet0/1", self.wan1, "GigabitEthernet0/1")
        lookalike = self.add_device("stub-wan1")
        _, _, node_devices = build_topology(new_lab(), [self.arista1, self.arista2, lookalike])
        self.assertEqual(
            list(node_devices),
            [
                "arista1",
                "arista2",
                "stub-wan1",
                f"stub-device-{far_devices[1].pk.hex[:8]}",
                "stub-WAN-1-A",
                "stub-wan1-2",
            ],
        )
        self.assertEqual(node_devices["stub-wan1"], lookalike)

    def test_stub_uses_stub_node_image_setting(self):
        self.cable_to_outside(self.arista1, "GigabitEthernet0/1", self.wan1, "GigabitEthernet0/1")
        with override_settings(
            PLUGINS_CONFIG={APP_NAME: validated_config({"stub_node_image": "registry.local/alpine:3"})}
        ):
            built, _, _ = build_topology(new_lab(), [self.arista1])
        self.assertEqual(built["topology"]["nodes"]["stub-wan1"]["image"], "registry.local/alpine:3")
        for image in ("bad image", "alpine:${TAG}", "alpine:{{.Tag}}"):
            with self.subTest(image=image):
                config = validated_config({"stub_node_image": image})
                with override_settings(PLUGINS_CONFIG={APP_NAME: config}):
                    with self.assertRaises(ImproperlyConfigured) as context:
                        build_topology(new_lab(), [self.arista1])
                    self.assertTrue(str(context.exception).startswith("The stub_node_image setting is not valid: "))
                    built, _, _ = build_topology(new_lab(), [self.arista2])
                self.assertEqual(list(built["topology"]["nodes"]), ["arista2"])

    def test_hidden_far_device_is_skipped_without_its_name(self):
        (hidden_local, _) = self.cable_to_outside(self.arista1, "Ethernet1", self.wan1, "Uplink-secret")
        self.cable_to_outside(self.arista1, "Ethernet2", self.arista2, "GigabitEthernet0/1")
        self.add_permissions("dcim.view_device", constraints={"name__in": ["arista1", "arista2"]})
        built, warnings, node_devices = build_topology(new_lab(), [self.arista1, self.arista2], self.user)
        self.assertEqual(list(node_devices), ["arista1", "arista2"])
        self.assertEqual(built["topology"]["links"], [{"endpoints": ["arista1:eth4", "arista2:eth1"]}])
        self.assertIn(self.cable_warning(hidden_local, topology.W_HIDDEN), warnings)
        for warning in warnings:
            self.assertNotIn("wan1", warning)
            self.assertNotIn("Uplink-secret", warning)
        unrestricted, _, unrestricted_devices = build_topology(new_lab(), [self.arista1, self.arista2])
        self.assertEqual(list(unrestricted_devices), ["arista1", "arista2", "stub-wan1"])
        self.assertEqual(
            [link["endpoints"] for link in unrestricted["topology"]["links"]],
            [["arista1:eth3", "stub-wan1:eth1"], ["arista1:eth4", "arista2:eth1"]],
        )

    def test_far_device_with_all_links_skipped_gets_no_stub(self):
        local_marked, _ = self.cable_to_outside(self.arista1, "GigabitEthernet0/1", self.wan1, "GigabitEthernet0/1")
        (local_data,) = self.add_interfaces(self.arista1, "GigabitEthernet0/2")
        self.connect(local_data, self.add_interfaces(self.wan1, "GigabitEthernet0/2", mgmt_only=True)[0])
        InterfaceMapping.objects.filter(node_mapping=self.arista_mapping, physical_name="GigabitEthernet0/1").update(
            container_name="eth${X}"
        )
        built, warnings, node_devices = build_topology(new_lab(), [self.arista1])
        self.assertEqual(list(node_devices), ["arista1"])
        self.assertNotIn("links", built["topology"])
        self.assertEqual(
            warnings,
            [self.cable_warning(local_data, W_MGMT_ONLY), self.cable_warning(local_marked, W_NAME_VARIABLE)],
        )

    def test_stubs_count_against_the_subnet(self):
        self.cable_to_outside(self.arista1, "GigabitEthernet0/1", self.wan1, "GigabitEthernet0/1")
        self.cable_to_outside(self.arista2, "GigabitEthernet0/1", self.wan2, "GigabitEthernet0/1")
        self.cable_to_outside(self.nokia1, "ethernet-1/1", self.add_device("wan3"), "GigabitEthernet0/1")
        devices = [self.arista1, self.arista2, self.nokia1]
        with self.assertRaises(ValidationError) as context:
            build_topology(new_lab(mgmt_subnet="10.0.0.0/29"), devices)
        self.assertEqual(context.exception.message_dict, {"mgmt_subnet": [SUBNET_ROOM_MESSAGE.format(room=5, count=6)]})
        built, _, _ = build_topology(new_lab(mgmt_subnet="10.0.0.0/28"), devices)
        self.assertEqual(
            {name: node["mgmt-ipv4"] for name, node in built["topology"]["nodes"].items()},
            {
                "arista1": "10.0.0.2",
                "arista2": "10.0.0.3",
                "nokia1": "10.0.0.4",
                "stub-wan1": "10.0.0.5",
                "stub-wan2": "10.0.0.6",
                "stub-wan3": "10.0.0.7",
            },
        )

    def test_stubs_pass_schema_and_repeat(self):
        self.use_full_kind_names()
        self.cable_three_stub_links()
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2])
        second, _, _ = build_topology(new_lab(), [self.arista2, self.arista1])
        self.assertEqual(to_yaml(built), to_yaml(second))
        self.assertEqual([error.message for error in load_clab_schema().iter_errors(built)], [])
