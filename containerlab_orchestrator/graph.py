"""Make the data of the lab graph from the node rows of a lab."""

from typing import NamedTuple

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


def lab_graph(lab, user):
    """Return the graph of the node rows of the lab that the user can view.

    The caller makes sure that the lab has a stored topology YAML.
    """
    rows = LabNode.objects.restrict(user, "view").filter(lab=lab).order_by("node_name")
    data = {
        "nodes": [_node(row) for row in rows],
        "links": [],
        "skipped_links": 0,
        "groups": {group.key: {"label": group.label, "color": group.color} for group in STATE_GROUPS},
    }
    return LabGraph(data, "")
