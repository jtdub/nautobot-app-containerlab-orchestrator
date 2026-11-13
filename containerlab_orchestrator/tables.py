"""Tables for the containerlab_orchestrator app."""

import django_tables2 as tables
from nautobot.apps.tables import (
    BaseTable,
    BooleanColumn,
    ButtonsColumn,
    TagColumn,
    ToggleColumn,
)

from containerlab_orchestrator.models import ContainerlabHost, NodeMapping


class ContainerlabHostTable(BaseTable):
    """Table of ContainerlabHost records."""

    pk = ToggleColumn()
    name = tables.Column(linkify=True)
    remote_url = tables.Column()
    verify_ssl = BooleanColumn()
    secrets_group = tables.Column(linkify=True)
    tags = TagColumn(url_name="plugins:containerlab_orchestrator:containerlabhost_list")
    actions = ButtonsColumn(ContainerlabHost)

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta attributes for ContainerlabHostTable."""

        model = ContainerlabHost
        fields = ("pk", "name", "remote_url", "verify_ssl", "secrets_group", "description", "tags", "actions")
        default_columns = ("pk", "name", "remote_url", "verify_ssl", "secrets_group", "description", "actions")


class NodeMappingTable(BaseTable):
    """Table of NodeMapping records."""

    pk = ToggleColumn()
    device_type = tables.Column(linkify=True)
    platform = tables.Column(linkify=True)
    kind = tables.Column(linkify=True)
    image = tables.Column()
    config_source = tables.Column()
    tags = TagColumn(url_name="plugins:containerlab_orchestrator:nodemapping_list")
    actions = ButtonsColumn(NodeMapping)

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta attributes for NodeMappingTable."""

        model = NodeMapping
        fields = (
            "pk",
            "device_type",
            "platform",
            "kind",
            "image",
            "node_type",
            "cpu",
            "memory",
            "config_source",
            "tags",
            "actions",
        )
        default_columns = ("pk", "device_type", "platform", "kind", "image", "node_type", "config_source", "actions")
