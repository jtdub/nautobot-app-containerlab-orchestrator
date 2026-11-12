"""App declaration for containerlab_orchestrator."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
from importlib import metadata

from nautobot.apps import NautobotAppConfig

__version__ = metadata.version(__name__)


class ContainerlabOrchestratorConfig(NautobotAppConfig):
    """App configuration for the containerlab_orchestrator app."""

    name = "containerlab_orchestrator"
    verbose_name = "Containerlab Orchestrator"
    version = __version__
    author = "Network to Code, LLC"
    description = "Containerlab Orchestrator."
    base_url = "containerlab-orchestrator"
    required_settings = []
    default_settings = {
        "default_node_mapping": {"kind": "linux", "image": "alpine:latest"},
        "request_timeout": 300,
    }
    docs_view_name = "plugins:containerlab_orchestrator:docs"
    searchable_models = []


config = ContainerlabOrchestratorConfig  # pylint:disable=invalid-name
