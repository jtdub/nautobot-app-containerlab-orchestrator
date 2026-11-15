"""Build a containerlab topology from a lab and its devices."""

import copy
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

OVERRIDE_KEYS = frozenset(
    {"image", "type", "env", "cpu", "memory", "cpu-set", "shm-size", "labels", "image-pull-policy", "startup-delay"}
)
"""The overrides keys of a node mapping that the builder merges into the node. It drops each other key.

A key is on the list only when it changes the node container alone. It reads no host file, runs no command, and adds
no host namespace, device, capability, or privilege. The keys kind, mgmt-ipv4, and startup-config are not on the list,
because the builder and the startup config own them.

Trust boundary: a user with the add or change permission on NodeMapping writes overrides. A different user can build
and deploy the lab. Keys such as binds and exec act on the containerlab host, so the builder drops them.

Variables: containerlab replaces a $var or ${var} anywhere in the topology with a value from the host environment. The
env entry __IMPORT_ENVS imports all host variables. So the builder also drops each value with a $ and each
__IMPORT_ENVS entry, from the overrides and from the mapping fields.
"""

IMPORT_ENVS_KEY = "__IMPORT_ENVS"
"""The env entry that makes containerlab import all environment variables of the host into the node."""

MGMT_NETWORK_PREFIX = "clab-"
"""The prefix of the management network name. The lab name follows it."""

FIRST_NODE_OFFSET = 2
"""The offset of the first node address in the subnet. Docker gives the address at offset 1 to the gateway."""

EMPTY_DEVICES_MESSAGE = "Lab {lab} has no device to build."
"""The error for a build with no device."""

LAB_NAME_MESSAGE = "Start the lab name with a letter or a digit, so that containerlab accepts it."
"""The error for a lab name that the containerlab schema does not accept."""

SUBNET_ROOM_MESSAGE = "Node addresses in the subnet: {room}. Nodes in the topology: {count}. Use a larger subnet."
"""The error for a subnet that has no address for each node."""

W_OVERRIDE = "Mapping {mapping}: the overrides keys {keys} are not allowed. The builder dropped them."
"""The warning for the overrides keys of a node mapping that are not in OVERRIDE_KEYS."""

W_VARIABLE = (
    "Mapping {mapping}: the keys {keys} have a $, and containerlab replaces a $ variable with a value from the host. "
    "The builder dropped them."
)
"""The warning for the node keys and env entries of a mapping that containerlab fills from the host environment."""

KIND_VARIABLE_MESSAGE = (
    "Mapping {mapping}: the kind has a $. Remove the $, because containerlab replaces a $ variable with a value from "
    "the host."
)
"""The error for a mapping kind with a $. A node with no kind is not valid, so the builder does not drop it."""


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


def _merge(node, overrides):
    """Merge the overrides into the node in sorted key order: a dict into a dict deeply, else a copy of the value."""
    for key in sorted(overrides):
        value = overrides[key]
        if isinstance(value, dict) and isinstance(node.get(key), dict):
            _merge(node[key], value)
        else:
            node[key] = copy.deepcopy(value)


def _has_variable(value):
    """Return True if a string, a dict key, or a list item at any depth of the value has a $."""
    if isinstance(value, str):
        return "$" in value
    if isinstance(value, dict):
        return any(_has_variable(key) or _has_variable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_variable(item) for item in value)
    return False


def _drop_variables(node):
    """Remove each node key and env entry that containerlab fills from the host environment.

    Returns:
        The set of dropped names. An env entry has the name env.<entry>.
    """
    dropped = set()
    env = node.get("env")
    if isinstance(env, dict):
        entries = [name for name, value in env.items() if name == IMPORT_ENVS_KEY or _has_variable({name: value})]
        for name in entries:
            del env[name]
            dropped.add(f"env.{name}")
        if entries and not env:
            del node["env"]
    for key in [key for key, value in node.items() if _has_variable(value)]:
        del node[key]
        dropped.add(key)
    return dropped


def _apply_overrides(node, mapping, warnings, warned_pks):
    """Merge the allowed overrides of the mapping into the node, and drop each value that has a host variable.

    Add one W_OVERRIDE and one W_VARIABLE warning for each mapping pk, when it drops a key. The pks are in warned_pks.

    Raises:
        ValidationError: If the kind of the mapping has a $.
    """
    if _has_variable(mapping.kind):
        raise ValidationError(KIND_VARIABLE_MESSAGE.format(mapping=mapping))
    overrides = mapping.overrides or {}
    allowed = {key: copy.deepcopy(value) for key, value in overrides.items() if key in OVERRIDE_KEYS}
    variables = _drop_variables(allowed)
    _merge(node, allowed)
    variables |= _drop_variables(node)
    not_allowed = sorted(key for key in overrides if key not in OVERRIDE_KEYS)
    warning_key = getattr(mapping, "pk", None)
    if warning_key in warned_pks:
        return
    warned_pks.add(warning_key)
    if not_allowed:
        warnings.append(W_OVERRIDE.format(mapping=mapping, keys=", ".join(not_allowed)))
    if variables:
        warnings.append(W_VARIABLE.format(mapping=mapping, keys=", ".join(sorted(variables))))


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
    return {name: str(network.network_address + FIRST_NODE_OFFSET + index) for index, name in enumerate(names)}


def build_topology(lab, devices):
    """Return the containerlab topology of the lab, the build warnings, and the device of each node name.

    The caller gives only the devices that the user can view.

    Raises:
        ValidationError: If the lab name or the subnet is not valid, the device list is empty, or a kind has a $.
        ImproperlyConfigured: If a device has no mapping and the default_node_mapping setting is not valid.
    """
    if not CLAB_NAME_PATTERN.fullmatch(lab.name or ""):
        raise ValidationError({"name": [LAB_NAME_MESSAGE]})
    unique_devices = {device.pk: device for device in devices}
    if not unique_devices:
        raise ValidationError(EMPTY_DEVICES_MESSAGE.format(lab=lab))
    ordered = sorted(unique_devices.values(), key=_node_order)
    warnings = []
    warned_pks = set()
    device_nodes = []
    for device in ordered:
        mapping = resolve_mapping(device)
        node = _mapping_node(mapping)
        _apply_overrides(node, mapping, warnings, warned_pks)
        device_nodes.append(node)
    names = _allocate_names([_base_name(device.name, device.pk) for device in ordered])
    addresses = _assign_addresses(lab, names)
    nodes = {name: {**node, "mgmt-ipv4": addresses[name]} for name, node in zip(names, device_nodes)}
    node_devices = dict(zip(names, ordered))
    topology = {
        "name": lab.name,
        "mgmt": {"network": f"{MGMT_NETWORK_PREFIX}{lab.name}", "ipv4-subnet": lab.mgmt_subnet},
        "topology": {"nodes": nodes},
    }
    return topology, warnings, node_devices


def to_yaml(topology):
    """Return the topology as YAML text, with the keys in the order of the topology."""
    return yaml.safe_dump(topology, sort_keys=False, default_flow_style=False, allow_unicode=True)
