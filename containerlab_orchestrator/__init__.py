"""App declaration for containerlab_orchestrator."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
from importlib import metadata

from nautobot.apps import NautobotAppConfig

__version__ = metadata.version("nautobot-containerlab-orchestrator")


class ContainerlabOrchestratorConfig(NautobotAppConfig):
    """App configuration for the containerlab_orchestrator app."""

    name = "containerlab_orchestrator"
    verbose_name = "Containerlab Orchestrator"
    version = __version__
    author = "Network to Code, LLC"
    description = "Containerlab Orchestrator."
    base_url = "containerlab-orchestrator"
    min_version = "3.2.8"
    required_settings = []
    default_settings = {
        "default_node_mapping": {"kind": "linux", "image": "alpine:latest"},
        "max_lab_devices": 100,
        "request_timeout": 300,
        "stub_node_image": "alpine:latest",
    }
    docs_view_name = "plugins:containerlab_orchestrator:docs"
    searchable_models = ["containerlabhost", "lab"]


config = ContainerlabOrchestratorConfig  # pylint:disable=invalid-name
