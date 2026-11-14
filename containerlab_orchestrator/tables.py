"""Tables for the containerlab_orchestrator app."""

import django_tables2 as tables
from nautobot.apps.tables import BaseTable, BooleanColumn, ButtonsColumn, StatusTableMixin, TagColumn, ToggleColumn

from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping


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


class InterfaceMappingTable(BaseTable):
    """Table of the interface rows of a node mapping."""

    physical_name = tables.Column(order_by=("_physical_name",))
    container_name = tables.Column()

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta attributes for InterfaceMappingTable."""

        model = InterfaceMapping
        fields = ("physical_name", "container_name")


class LabTable(StatusTableMixin, BaseTable):
    """Table of Lab records."""

    pk = ToggleColumn()
    name = tables.Column(linkify=True)
    host = tables.Column(linkify=True)
    owner = tables.Column()
    config_source = tables.Column()
    mgmt_subnet = tables.Column()
    built_at = tables.DateTimeColumn()
    tags = TagColumn(url_name="plugins:containerlab_orchestrator:lab_list")
    actions = ButtonsColumn(Lab)

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta attributes for LabTable."""

        model = Lab
        fields = (
            "pk",
            "name",
            "host",
            "owner",
            "status",
            "config_source",
            "mgmt_subnet",
            "built_at",
            "tags",
            "actions",
        )
        default_columns = ("pk", "name", "host", "owner", "status", "mgmt_subnet", "built_at", "actions")


class LabNodeTable(BaseTable):
    """Table of the node rows of a lab."""

    node_name = tables.Column()
    device = tables.Column(linkify=True)
    is_stub = BooleanColumn()
    kind = tables.Column()
    image = tables.Column()
    mgmt_ipv4 = tables.Column()
    state = tables.Column()

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta attributes for LabNodeTable."""

        model = LabNode
        fields = ("node_name", "device", "is_stub", "kind", "image", "mgmt_ipv4", "state", "container_id")
        default_columns = ("node_name", "device", "is_stub", "kind", "image", "mgmt_ipv4", "state")
