"""Build a containerlab topology from a lab and its devices."""

import ipaddress
import re

import yaml
from django.core.exceptions import ValidationError

from containerlab_orchestrator.mapper import resolve_mapping
from containerlab_orchestrator.validators import subnet_node_room, validate_ipv4_network

CLAB_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
"""A node name or a topology name that the containerlab schema accepts."""

CLAB_NAME_BAD_CHARACTERS = re.compile(r"[^A-Za-z0-9_.-]+")
"""A run of characters that a containerlab node name cannot hold."""

MAX_NODE_NAME_LENGTH = 63
"""The longest node name, so that the container name and its host name stay short."""

MGMT_NETWORK_PREFIX = "clab-"
"""The prefix of the management network name. The lab name follows it."""

FIRST_NODE_OFFSET = 2
"""The offset of the first node address in the subnet. Docker gives the address at offset 1 to the gateway."""

EMPTY_DEVICES_MESSAGE = "Lab {lab} has no device to build."
"""The error for a build with no device."""

LAB_NAME_MESSAGE = "Start the lab name with a letter or a digit, so that containerlab accepts it."
"""The error for a lab name that the containerlab schema does not accept."""

SUBNET_ROOM_MESSAGE = (
    "Node addresses in the subnet: {room}. Nodes in the topology: {count}. Use a larger subnet."
)
"""The error for a subnet that has no address for each node."""


def _node_order(device):
    """Return the key that puts devices in node order."""
    return (device.name or "", str(device.pk))


def _base_name(name, pk):
    """Return a node name that the containerlab schema accepts, or a name from the pk if no character is left."""
    cleaned = CLAB_NAME_BAD_CHARACTERS.sub("-", name or "").lstrip("_.-")[:MAX_NODE_NAME_LENGTH]
    return cleaned or f"device-{pk.hex[:8]}"


def _allocate_names(bases):
    """Return a unique node name for each base name, in the same order, with no regard to case.

    A base name that is still free keeps its text. Each other name gets the first free suffix of -2, -3, and so on.
    """
    names = [None] * len(bases)
    used = set()
    for index, base in enumerate(bases):
        if base.casefold() not in used:
            used.add(base.casefold())
            names[index] = base
    for index, base in enumerate(bases):
        number = 2
        while names[index] is None:
            suffix = f"-{number}"
            candidate = f"{base[: MAX_NODE_NAME_LENGTH - len(suffix)]}{suffix}"
            if candidate.casefold() not in used:
                used.add(candidate.casefold())
                names[index] = candidate
            number += 1
    return names


def _mapping_node(mapping):
    """Return the node keys of a mapping, in containerlab order, with no key for an empty value."""
    values = (
        ("kind", mapping.kind),
        ("image", mapping.image),
        ("type", mapping.node_type),
        ("cpu", None if mapping.cpu is None else float(mapping.cpu)),
        ("memory", mapping.memory),
        ("env", dict(mapping.env or {})),
    )
    return {key: value for key, value in values if value not in (None, "", {})}


def _assign_addresses(lab, names):
    """Return the management address of each node name, from offset 2 of the lab subnet.

    Raises:
        ValidationError: On `mgmt_subnet`, if the subnet is not valid or has no address for each node.
    """
    try:
        validate_ipv4_network(lab.mgmt_subnet)
    except ValidationError as error:
        raise ValidationError({"mgmt_subnet": error.messages}) from error
    room = subnet_node_room(lab.mgmt_subnet)
    if len(names) > room:
        raise ValidationError({"mgmt_subnet": [SUBNET_ROOM_MESSAGE.format(room=room, count=len(names))]})
    network = ipaddress.IPv4Network(lab.mgmt_subnet)
    return {
        name: str(network.network_address + FIRST_NODE_OFFSET + index) for index, name in enumerate(names)
    }


def build_topology(lab, devices):
    """Return the containerlab topology of the lab, the build warnings, and the device of each node name.

    The caller gives only the devices that the user can view.

    Raises:
        ValidationError: If the lab name or the subnet is not valid, or the device list is empty.
        ImproperlyConfigured: If a device has no mapping and the default_node_mapping setting is not valid.
    """
    if not CLAB_NAME_PATTERN.fullmatch(lab.name or ""):
        raise ValidationError({"name": [LAB_NAME_MESSAGE]})
    unique_devices = {device.pk: device for device in devices}
    if not unique_devices:
        raise ValidationError(EMPTY_DEVICES_MESSAGE.format(lab=lab))
    ordered = sorted(unique_devices.values(), key=_node_order)
    device_nodes = [_mapping_node(resolve_mapping(device)) for device in ordered]
    names = _allocate_names([_base_name(device.name, device.pk) for device in ordered])
    addresses = _assign_addresses(lab, names)
    nodes = {name: {**node, "mgmt-ipv4": addresses[name]} for name, node in zip(names, device_nodes)}
    node_devices = dict(zip(names, ordered))
    topology = {
        "name": lab.name,
        "mgmt": {"network": f"{MGMT_NETWORK_PREFIX}{lab.name}", "ipv4-subnet": lab.mgmt_subnet},
        "topology": {"nodes": nodes},
    }
    return topology, [], node_devices


def to_yaml(topology):
    """Return the topology as YAML text, with the keys in the order of the topology."""
    return yaml.safe_dump(topology, sort_keys=False, default_flow_style=False, allow_unicode=True)
