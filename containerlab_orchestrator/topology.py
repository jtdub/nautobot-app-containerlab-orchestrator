"""Build a containerlab topology from a lab and its devices."""

import copy
import ipaddress
import re
from typing import NamedTuple

import yaml
from django.core.exceptions import ImproperlyConfigured, ValidationError
from nautobot.apps.config import get_app_settings_or_config
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.models import naturalize_interface
from nautobot.dcim.models import Device, Interface

from containerlab_orchestrator.config_sources import get_startup_config
from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.mapper import FALLBACK_INTERFACE_PREFIX, map_interfaces, resolve_mapping
from containerlab_orchestrator.validators import subnet_node_room, validate_image_reference, validate_ipv4_network

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

Variables: containerlab runs the topology file as a Go template, so a {{ action can write any text into it. Then
containerlab replaces a $var or ${var} with a value from the host environment. The env entry __IMPORT_ENVS imports all
host variables. So the builder also drops each value with a $ or a {{, and each __IMPORT_ENVS entry. This applies to
the overrides and to the mapping fields. A startup config keeps its $ and {{, because the builder escapes them.
"""

HOST_VARIABLE_MARKERS = ("$", "{{")
"""The text that makes containerlab change a value: a shell variable or a Go template action.

The builder drops a value with a marker in the mapping fields and the overrides. It escapes them in a startup config.
"""

TEMPLATE_ACTION_ESCAPE = "{{`{{`}}"
"""The Go template action that writes {{."""

KIND_PASSES = {
    "cisco_iol": (0, 1),
    "cisco_vios": (0, 0),
    "arrcus_arcos": (0, 0),
    "6wind_vsr": (0, 0),
    "nokia_srsim": (2, 0),
}
"""The passes of each kind on its startup config in containerlab v0.83.0, after the passes on the topology file.

A pair holds the count of env and template passes, then the count of template passes alone. cisco_iol renders its
partial config one time (iol.go:440-476). cisco_vios, arrcus_arcos, and 6wind_vsr copy the text (cisco-vios.go:148-178,
arcos.go:84-86, 6wind_vsr.go:74-80). nokia_srsim runs the partial config and then the full config through
SubstituteEnvsAndTemplate (sros/config.go:118, 314). Read the kind code again for a new containerlab version.
"""

DEFAULT_KIND_PASSES = (1, 0)
"""The passes of each other kind: GenerateConfig runs SubstituteEnvsAndTemplate one time (default_node.go:660-695)."""

SRL_KINDS = frozenset({"nokia_srlinux", "srl"})
"""The SR Linux kind names. They use DEFAULT_KIND_PASSES for a CLI config, and render a JSON config one more time
(srl.go:733-781)."""

SRL_JSON_PASSES = (2, 0)
"""The passes of an SR Linux startup config whose first character that is not blank is {."""

IMPORT_ENVS_KEY = "__IMPORT_ENVS"
"""The env entry that makes containerlab import all environment variables of the host into the node."""

MGMT_NETWORK_PREFIX = "clab-"
"""The prefix of the management network name. The lab name follows it."""

FIRST_NODE_OFFSET = 2
"""The offset of the first node address in the subnet. Docker gives the address at offset 1 to the gateway."""

STUB_KIND = "linux"
"""The containerlab kind of a stub node."""

STUB_PREFIX = "stub-"
"""The prefix of a stub node name. The name of the far device follows it."""

EMPTY_DEVICES_MESSAGE = "Lab {lab} has no device to build."
"""The error for a build with no device."""

LAB_NAME_MESSAGE = "Start the lab name with a letter or a digit, so that containerlab accepts it."
"""The error for a lab name that the containerlab schema does not accept."""

SUBNET_ROOM_MESSAGE = "Node addresses in the subnet: {room}. Nodes in the topology: {count}. Use a larger subnet."
"""The error for a subnet that has no address for each node."""

STUB_IMAGE_MESSAGE = "The stub_node_image setting is not valid: {message}"
"""The error for a stub_node_image setting that is not a valid image name."""

STUB_IMAGE_VARIABLE_TEXT = (
    "it has a $ variable or a Go template action, which containerlab replaces before it reads the topology."
)
"""The reason in STUB_IMAGE_MESSAGE for an image with a marker of HOST_VARIABLE_MARKERS."""

W_OVERRIDE = "Mapping {mapping}: the overrides keys {keys} are not allowed. The builder dropped them."
"""The warning for the overrides keys of a node mapping that are not in OVERRIDE_KEYS."""

W_VARIABLE = (
    "Mapping {mapping}: the keys {keys} have a $ variable or a Go template action, which containerlab replaces "
    "before it reads the topology. The builder dropped them."
)
"""The warning for the node keys and env entries of a mapping with a marker of HOST_VARIABLE_MARKERS."""

KIND_VARIABLE_MESSAGE = (
    "Mapping {mapping}: the kind has a $ variable or a Go template action. Remove it, because containerlab replaces "
    "it before it reads the topology."
)
"""The error for a mapping kind with a host variable marker. A node with no kind is not valid, so the builder does not
drop it."""

CABLE_WARNING = "Device {device}, interface {interface}: {text}"
"""The form of a cable warning. It names the local end, or the end with no valid container name."""

W_MGMT_ONLY = "the cable is on a management-only interface. The builder skipped it."
"""The warning for a cable with a management-only end."""

W_NO_FAR_END = "the cable has no far end. The builder skipped it."
"""The warning for a cable with no termination on the far side."""

W_BREAKOUT = "the cable has more than one far end. The builder skipped it."
"""The warning for a multi-connector cable, or a cable end with more than one peer."""

W_NOT_INTERFACE = "the cable goes to a {kind}, not to an interface. The builder skipped it."
"""The warning for a cable to a circuit termination, a front port, or another termination that is not an interface."""

W_NO_FAR_DEVICE = "the cable goes to an interface with no device. The builder skipped it."
"""The warning for a cable to an interface on a module that is in no module bay."""

W_HIDDEN = "the cable goes to a device that you cannot view. The builder skipped it."
"""The warning for a cable to a device outside the selection that the user cannot view."""

W_NO_NAME = "the interface has no container name. The builder skipped its cable."
"""The warning for a cable on an interface that map_interfaces gives no container name."""

W_NAME_VARIABLE = (
    "the container name has a $ variable or a Go template action, which containerlab replaces before it reads the "
    "topology. The builder skipped its cable."
)
"""The warning for a cable on an interface whose container name has a marker of HOST_VARIABLE_MARKERS."""

_CABLE_RELATION = "cable_termination" if hasattr(Interface, "get_cable_peers") else "cable"
"""The Interface relation to its cable: a reverse one-to-one on Nautobot 3.2 and later, else a foreign key."""


class BuildContext(NamedTuple):
    """The lab, the devices in node order, and the user of a build. With no user, each device is visible."""

    lab: object
    devices: list
    user: object


class CablePair(NamedTuple):
    """A cable to link: the local interface, the far interface, and True if the far device is in the selection."""

    local: Interface
    far: Interface
    internal: bool


def _node_order(device):
    """Return the key that puts devices in node order."""
    return (device.name or "", str(device.pk))


def _base_name(name, pk, prefix=""):
    """Return a node name that the containerlab schema accepts, or a name from the pk if no letter or digit is left."""
    cleaned = CLAB_NAME_BAD_CHARACTERS.sub("-", name or "")
    if not cleaned.lstrip("_.-"):
        return f"{prefix}device-{pk.hex[:8]}"
    return f"{prefix}{cleaned}".lstrip("_.-")[:MAX_NODE_NAME_LENGTH]


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
    """Return True if a string, a dict key, or a list item at any depth of the value has a host variable marker."""
    if isinstance(value, str):
        return any(marker in value for marker in HOST_VARIABLE_MARKERS)
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
        ValidationError: If the kind of the mapping has a marker of HOST_VARIABLE_MARKERS.
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


def _kind_passes(kind, text):
    """Return the env and template passes, then the template passes alone, of the kind on the startup config text."""
    if kind in SRL_KINDS and text.lstrip(" \t\r\n").startswith("{"):
        return SRL_JSON_PASSES
    return KIND_PASSES.get(kind, DEFAULT_KIND_PASSES)


def _containerlab_text(text, kind):
    """Return the startup config text so that the node of the kind gets it unchanged, with a final newline.

    containerlab reads the topology with one template and one env pass, then the kind passes follow. An env pass
    changes $$ to $, and a template pass changes TEMPLATE_ACTION_ESCAPE to {{. A text with a newline is always inline
    text, never a host path or a URL.
    """
    env_template, template_only = _kind_passes(kind, text)
    text = text.replace("$", "$" * 2 ** (1 + env_template))
    for _ in range(1 + env_template + template_only):
        text = text.replace("{{", TEMPLATE_ACTION_ESCAPE)
    return text if text.endswith("\n") else f"{text}\n"


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


def _interface_key(interface):
    """Return the key that puts interfaces in natural order of their names."""
    return (naturalize_interface(interface.name, CHARFIELD_MAX_LENGTH), interface.name, str(interface.pk))


def _cabled_interfaces(device):
    """Return the interfaces of the device and its modules that have a cable, in natural order."""
    interfaces = device.all_interfaces.filter(**{f"{_CABLE_RELATION}__isnull": False}).select_related("device")
    if _CABLE_RELATION == "cable_termination":
        interfaces = interfaces.select_related("cable_termination__cable__cable_type").prefetch_related(
            "cable_termination__cable__terminations"
        )
    return sorted(interfaces, key=_interface_key)


def _cable_peers(termination):
    """Return the far terminations of the cable of the termination."""
    if hasattr(termination, "get_cable_peers"):
        return termination.get_cable_peers()
    peer = termination.get_cable_peer()
    return [] if peer is None else [peer]


def _is_multi_connector(interface):
    """Return True if the cable of the interface has a cable type with more than one connector on a side."""
    if _CABLE_RELATION == "cable":
        return False
    cable_type = getattr(interface.cable, "cable_type", None)
    return cable_type is not None and cable_type.is_multi_connector


def _add_cable_warning(warnings, interface, text):
    """Add a cable warning that names the interface and its device."""
    warnings.append(CABLE_WARNING.format(device=interface.parent, interface=interface.name, text=text))


def _skip_text(local, peers):
    """Return the warning text of the first rule that skips the cable of the local interface, or None."""
    if local.mgmt_only:
        return W_MGMT_ONLY
    if _is_multi_connector(local) or len(peers) > 1:
        return W_BREAKOUT
    if not peers:
        return W_NO_FAR_END
    if not isinstance(peers[0], Interface):
        return W_NOT_INTERFACE.format(kind=peers[0]._meta.verbose_name)
    if peers[0].parent is None:
        return W_NO_FAR_DEVICE
    return None


def _visible_device_pks(device_pks, user):
    """Return the pks of the devices that the user can view. With no user, each device is visible."""
    if user is None or not device_pks:
        return set(device_pks)
    return set(Device.objects.restrict(user, "view").filter(pk__in=device_pks).values_list("pk", flat=True))


def _collect_cables(devices, user, warnings):
    """Return a CablePair for each cable of the devices that the builder can link, in node order.

    Add one warning for each cable that it skips. A cable to an outside device that the user cannot view is
    skipped before a rule reads the far interface.

    Returns:
        The pairs, and the local interfaces of the cables to the devices that the user cannot view.
    """
    selected_pks = {device.pk for device in devices}
    seen_cable_ids = set()
    candidates = []
    for device in devices:
        for local in _cabled_interfaces(device):
            if local.cable_id in seen_cable_ids:
                continue
            seen_cable_ids.add(local.cable_id)
            peers = _cable_peers(local)
            text = _skip_text(local, peers)
            if text:
                _add_cable_warning(warnings, local, text)
            else:
                candidates.append((local, peers[0]))
    viewable_pks = selected_pks | _visible_device_pks({peer.parent.pk for _, peer in candidates} - selected_pks, user)
    pairs = []
    hidden_locals = []
    for local, peer in candidates:
        if peer.parent.pk not in viewable_pks:
            _add_cable_warning(warnings, local, W_HIDDEN)
            hidden_locals.append(local)
        elif peer.mgmt_only:
            _add_cable_warning(warnings, local, W_MGMT_ONLY)
        elif len(_cable_peers(peer)) > 1:
            _add_cable_warning(warnings, local, W_BREAKOUT)
        else:
            pairs.append(CablePair(local, peer, peer.parent.pk in selected_pks))
    return pairs, hidden_locals


def _link_names(mappings, pairs, hidden_locals, warnings):
    """Return the container name of each linked interface, from one map_interfaces call for each device.

    The interfaces of a device are the local ends of its pairs, the far ends of the internal pairs on it, and its
    hidden_locals. So a hidden cable keeps the container names of the other interfaces of the device.

    Args:
        mappings: The mapping of each device pk, in node order.
        pairs: The CablePair list of _collect_cables.
        hidden_locals: The local interfaces of the cables to devices that the user cannot view.
        warnings: The list that gets the map_interfaces warnings, in node order.
    """
    interfaces = {device_pk: set() for device_pk in mappings}
    for local in hidden_locals:
        interfaces[local.parent.pk].add(local)
    for pair in pairs:
        interfaces[pair.local.parent.pk].add(pair.local)
        if pair.internal:
            interfaces[pair.far.parent.pk].add(pair.far)
    container_names = {}
    for device_pk, mapping in mappings.items():
        names, mapper_warnings = map_interfaces(mapping, interfaces[device_pk])
        container_names.update(names)
        warnings.extend(mapper_warnings)
    return container_names


def _name_problem(interface, container_names):
    """Return the warning text if the interface has no container name, or a name with a host variable marker."""
    name = container_names.get(interface)
    if name is None:
        return W_NO_NAME
    if _has_variable(name):
        return W_NAME_VARIABLE
    return None


def _link(end_a, end_b):
    """Return a containerlab link between two endpoints, in sorted order."""
    return {"endpoints": sorted([end_a, end_b])}


def _internal_links(pairs, node_names, container_names, warnings):
    """Return a link for each pair between selected devices, and skip a pair with an end that has no valid name."""
    links = []
    for pair in pairs:
        if not pair.internal:
            continue
        ends = (pair.local, pair.far)
        problems = [(end, text) for end in ends if (text := _name_problem(end, container_names))]
        if problems:
            _add_cable_warning(warnings, *problems[0])
            continue
        links.append(_link(*(f"{node_names[end.parent.pk]}:{container_names[end]}" for end in ends)))
    return links


def _stub_links(pairs, container_names, warnings):
    """Return the stub interfaces of each far device outside the selection, in node order of the far devices.

    Each stub interface is a pair of its ethN name and the local interface. A pair whose local end has no valid
    container name is skipped with a warning. A far device with no pair left gets no stub.
    """
    groups = {}
    for pair in pairs:
        if pair.internal:
            continue
        text = _name_problem(pair.local, container_names)
        if text:
            _add_cable_warning(warnings, pair.local, text)
        else:
            groups.setdefault(pair.far.parent, []).append(pair)
    return {
        far_device: [
            (f"{FALLBACK_INTERFACE_PREFIX}{number}", pair.local)
            for number, pair in enumerate(sorted(groups[far_device], key=lambda pair: _interface_key(pair.far)), 1)
        ]
        for far_device in sorted(groups, key=_node_order)
    }


def _stub_image():
    """Return the stub_node_image setting.

    Raises:
        ImproperlyConfigured: If the setting is not a valid image name, or has a marker of HOST_VARIABLE_MARKERS.
    """
    image = get_app_settings_or_config(APP_NAME, "stub_node_image")
    try:
        validate_image_reference(image)
    except ValidationError as error:
        raise ImproperlyConfigured(STUB_IMAGE_MESSAGE.format(message=" ".join(error.messages))) from error
    if _has_variable(image):
        raise ImproperlyConfigured(STUB_IMAGE_MESSAGE.format(message=STUB_IMAGE_VARIABLE_TEXT))
    return image


def _stub_nodes(stubs, node_names, container_names):
    """Return the node of each stub and the link of each stub interface.

    The stub interface names are FALLBACK_INTERFACE_PREFIX and a number, so they cannot hold a host variable marker.
    """
    if not stubs:
        return {}, []
    image = _stub_image()
    nodes = {node_names[far_device.pk]: {"kind": STUB_KIND, "image": image} for far_device in stubs}
    links = [
        _link(f"{node_names[local.parent.pk]}:{container_names[local]}", f"{node_names[far_device.pk]}:{stub_end}")
        for far_device, ends in stubs.items()
        for stub_end, local in ends
    ]
    return nodes, links


def _node_names(devices, far_devices):
    """Return the node name of each device pk and far device pk, and the device of each name, with None for a stub.

    The stubs of the far devices get their names after the devices.
    """
    bases = [_base_name(device.name, device.pk) for device in devices]
    bases += [_base_name(device.name, device.pk, STUB_PREFIX) for device in far_devices]
    names = _allocate_names(bases)
    node_names = {owner.pk: name for owner, name in zip([*devices, *far_devices], names)}
    return node_names, {**dict(zip(names, devices)), **dict.fromkeys(names[len(devices) :])}


def _device_nodes(devices, warnings):
    """Return the node of each device and the mapping of each device pk, in the order of the devices."""
    warned_pks = set()
    device_nodes = []
    mappings = {}
    for device in devices:
        mapping = resolve_mapping(device)
        node = _mapping_node(mapping)
        _apply_overrides(node, mapping, warnings, warned_pks)
        device_nodes.append(node)
        mappings[device.pk] = mapping
    return device_nodes, mappings


def _linkable_cables(devices, mappings, user, warnings):
    """Return the CablePair list of the devices and the container name of each linked interface."""
    pairs, hidden_locals = _collect_cables(devices, user, warnings)
    return pairs, _link_names(mappings, pairs, hidden_locals, warnings)


def _interface_maps(mappings, container_names):
    """Return the container name of each physical interface name, for each device pk of the mappings.

    A map holds the interface rows of the mapping, then the container name that the builder gave each linked interface.
    """
    maps = {
        device_pk: {row.physical_name: row.container_name for row in mapping.interface_mappings.all()}
        for device_pk, mapping in mappings.items()
    }
    for interface, name in container_names.items():
        maps[interface.parent.pk][interface.name] = name
    return maps


def _add_startup_configs(context, device_nodes, mappings, container_names, warnings):
    """Add the escaped startup config and enforce-startup-config to each device node that has a config.

    The device nodes are in the order of the devices of the context.
    """
    interface_maps = _interface_maps(mappings, container_names)
    for device, node in zip(context.devices, device_nodes):
        text, config_warnings = get_startup_config(device, mappings[device.pk], interface_maps[device.pk], context.lab)
        warnings.extend(config_warnings)
        if text is not None:
            node["startup-config"] = _containerlab_text(text, node["kind"])
            node["enforce-startup-config"] = True


def _nodes_and_links(context, warnings):
    """Return the nodes with no address, the links, and the device of each node name, with None for a stub."""
    device_nodes, mappings = _device_nodes(context.devices, warnings)
    pairs, container_names = _linkable_cables(context.devices, mappings, context.user, warnings)
    _add_startup_configs(context, device_nodes, mappings, container_names, warnings)
    stub_warnings = []
    stubs = _stub_links(pairs, container_names, stub_warnings)
    node_names, node_devices = _node_names(context.devices, list(stubs))
    links = _internal_links(pairs, node_names, container_names, warnings)
    warnings.extend(stub_warnings)
    stub_nodes, stub_links = _stub_nodes(stubs, node_names, container_names)
    nodes = {node_names[device.pk]: node for device, node in zip(context.devices, device_nodes)}
    return {**nodes, **stub_nodes}, links + stub_links, node_devices


def build_topology(lab, devices, user=None):
    """Return the containerlab topology of the lab, the build warnings, and the device of each node name.

    The caller gives only the devices that the user can view. With a user, a cable to an outside device that the
    user cannot view gets no stub. The device of a stub node name is None. A device node gets a startup config from
    its config source. Each node with no config because of a problem gets a warning.

    Raises:
        ValidationError: If the lab name or the subnet is not valid, the device list is empty, or a kind has a marker.
        ImproperlyConfigured: If the default_node_mapping setting, or the stub_node_image setting for a stub, is not
            valid.
    """
    if not CLAB_NAME_PATTERN.fullmatch(lab.name or ""):
        raise ValidationError({"name": [LAB_NAME_MESSAGE]})
    unique_devices = {device.pk: device for device in devices}
    if not unique_devices:
        raise ValidationError(EMPTY_DEVICES_MESSAGE.format(lab=lab))
    warnings = []
    context = BuildContext(lab, sorted(unique_devices.values(), key=_node_order), user)
    nodes, links, node_devices = _nodes_and_links(context, warnings)
    addresses = _assign_addresses(lab, list(nodes))
    topology = {
        "name": lab.name,
        "mgmt": {"network": f"{MGMT_NETWORK_PREFIX}{lab.name}", "ipv4-subnet": lab.mgmt_subnet},
        "topology": {"nodes": {name: {**node, "mgmt-ipv4": addresses[name]} for name, node in nodes.items()}},
    }
    if links:
        topology["topology"]["links"] = sorted(links, key=lambda link: link["endpoints"])
    return topology, warnings, node_devices


class _TopologyDumper(yaml.SafeDumper):  # pylint: disable=too-many-ancestors
    """A YAML dumper that writes text with a newline in the literal block style."""


def _represent_text(dumper, value):
    """Return the YAML node of the text, in the literal block style if it has a newline.

    PyYAML uses the double-quoted style when the text cannot be a literal block.
    """
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_str(value)


_TopologyDumper.add_representer(str, _represent_text)


def to_yaml(topology):
    """Return the topology as YAML text, with the keys in the order of the topology."""
    return yaml.dump(topology, Dumper=_TopologyDumper, sort_keys=False, default_flow_style=False, allow_unicode=True)
