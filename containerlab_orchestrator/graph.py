"""Make the data of the lab graph from the node rows of a lab."""

from typing import NamedTuple

from containerlab_orchestrator.inspection import RUNNING


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


def state_group(state):
    """Return the key of the state group of the container state."""
    if state == RUNNING:
        return "running"
    if not state:
        return "not_inspected"
    return "not_running"
