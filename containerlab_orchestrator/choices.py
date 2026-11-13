"""Choice sets for the containerlab_orchestrator app."""

from nautobot.apps.choices import ChoiceSet


class NodeMappingConfigSourceChoices(ChoiceSet):
    """Sources of the startup config of a lab node."""

    NONE = "none"
    JINJA2 = "jinja2"
    GOLDEN_CONFIG_INTENDED = "golden_config_intended"
    GOLDEN_CONFIG_BACKUP = "golden_config_backup"

    CHOICES = (
        (NONE, "None (kind default)"),
        (JINJA2, "Jinja2 template"),
        (GOLDEN_CONFIG_INTENDED, "Golden Config intended"),
        (GOLDEN_CONFIG_BACKUP, "Golden Config backup"),
    )
