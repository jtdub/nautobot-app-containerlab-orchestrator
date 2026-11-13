"""Panels that the containerlab_orchestrator app adds to core Nautobot pages."""

from nautobot.apps.ui import ObjectsTablePanel, SectionChoices, TemplateExtension

from containerlab_orchestrator.tables import NodeMappingTable

PANEL_HIDDEN_COLUMNS = ["pk", "device_type", "platform", "tags"]
"""Columns that the panel hides, because the page already shows the Device Type or the Platform."""


def node_mapping_panel(field_name):
    """Return a Node Mappings panel that shows the mapping of the page object through this field."""
    return ObjectsTablePanel(
        weight=500,
        section=SectionChoices.RIGHT_HALF,
        table_class=NodeMappingTable,
        table_filter=field_name,
        related_field_name=field_name,
        exclude_columns=PANEL_HIDDEN_COLUMNS,
        table_title="Node Mappings",
    )


class DeviceTypeNodeMappingContent(TemplateExtension):
    """Add the Node Mappings panel to the Device Type detail page."""

    model = "dcim.devicetype"
    object_detail_panels = (node_mapping_panel("device_type"),)


class PlatformNodeMappingContent(TemplateExtension):
    """Add the Node Mappings panel to the Platform detail page."""

    model = "dcim.platform"
    object_detail_panels = (node_mapping_panel("platform"),)


template_extensions = [DeviceTypeNodeMappingContent, PlatformNodeMappingContent]
