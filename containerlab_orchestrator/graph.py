"""Make the data of the lab graph from the node rows and the stored topology YAML of a lab."""

from typing import NamedTuple

import yaml

from containerlab_orchestrator.inspection import RUNNING
from containerlab_orchestrator.models import LabNode


class StateGroup(NamedTuple):
    """A group of node states with one legend label and one node color."""

    key: str
    label: str
    color: str


STATE_GROUPS = (
    StateGroup("running", "Running", "#2ecc40"),
    StateGroup("not_running", "Not running", "#e01f1f"),
    StateGroup("not_inspected", "Not inspected", "#8a8f98"),
)
"""The state groups in legend order, with colors that show on the light and on the dark background."""

NO_NODE_PERMISSION_TEXT = "You do not have permission to view the nodes of this lab."
"""The message for a user with no view permission of the node rows."""

NO_VIEWABLE_NODES_TEXT = "You cannot view any node of this lab."
"""The message for a user whose permission constraint matches no node row of the lab."""

NO_NODES_TEXT = "This lab has no nodes."
"""The message for a lab with no node rows."""

UNREADABLE_YAML_TEXT = "The app cannot read the topology YAML of this lab. Run the Build Lab Topology job again."
"""The message for a stored topology YAML that the graph cannot read."""

SKIPPED_LINKS_TEXT = "Links that the graph does not show: {count}."
"""The note for the links of the topology YAML that the graph cannot draw."""

YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
"""The safe YAML loader of LibYAML when it is present, because it reads a large YAML fast."""


class LabGraph(NamedTuple):
    """The graph data of a lab, or None and the message that the page shows in place of the graph."""

    data: dict | None
    message: str


def state_group(state):
    """Return the key of the state group of the container state."""
    if state == RUNNING:
        return "running"
    if not state:
        return "not_inspected"
    return "not_running"


def _node(row):
    """Return the graph node of the node row."""
    return {
        "name": row.node_name,
        "group": state_group(row.state),
        "stub": row.is_stub,
        "info": {"kind": row.kind, "image": row.image, "mgmt_ipv4": row.mgmt_ipv4 or "", "state": row.state},
    }


def _yaml_links(topology_yaml):
    """Return the link items of the topology YAML, or None for a YAML that the graph cannot read.

    An absent or null topology section, or links section, gives no link items.
    """
    try:
        document = yaml.load(topology_yaml, Loader=YAML_LOADER)  # noqa: S506
    except (yaml.YAMLError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    section = document.get("topology")
    if section is None:
        return []
    if not isinstance(section, dict):
        return None
    links = section.get("links")
    if links is None:
        return []
    return links if isinstance(links, list) else None


def _endpoint(value):
    """Return the node name and the interface of a link endpoint, or None for an endpoint that the graph cannot draw."""
    if not isinstance(value, str):
        return None
    node, _, interface = value.partition(":")
    return (node, interface) if node and interface else None


def _link(item, names):
    """Return the graph line of the YAML link item, or None for a link that the graph cannot draw.

    Args:
        item: One item of the topology links.
        names: The node names that the graph shows.
    """
    endpoints = item.get("endpoints") if isinstance(item, dict) else None
    if not isinstance(endpoints, list) or len(endpoints) != 2:
        return None
    ends = [_endpoint(value) for value in endpoints]
    if None in ends or any(node not in names for node, _ in ends):
        return None
    (source, _), (target, _) = ends
    return {"source": source, "target": target, "endpoints_text": " to ".join(endpoints)}


def lab_graph(lab, user):
    """Return the graph of the node rows of the lab that the user can view.

    The caller makes sure that the lab has a stored topology YAML.
    """
    if not user.has_perm("containerlab_orchestrator.view_labnode"):
        return LabGraph(None, NO_NODE_PERMISSION_TEXT)
    rows = list(LabNode.objects.restrict(user, "view").filter(lab=lab).order_by("node_name"))
    if not rows:
        if LabNode.objects.filter(lab=lab).exists():
            return LabGraph(None, NO_VIEWABLE_NODES_TEXT)
        return LabGraph(None, NO_NODES_TEXT)
    items = _yaml_links(lab.topology_yaml)
    if items is None:
        return LabGraph(None, UNREADABLE_YAML_TEXT)
    names = {row.node_name for row in rows}
    links = [line for item in items if (line := _link(item, names))]
    data = {
        "nodes": [_node(row) for row in rows],
        "links": links,
        "skipped_links": len(items) - len(links),
        "groups": {group.key: {"label": group.label, "color": group.color} for group in STATE_GROUPS},
    }
    return LabGraph(data, "")
