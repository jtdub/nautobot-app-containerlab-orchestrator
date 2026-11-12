"""Tables for the containerlab_orchestrator app."""

import django_tables2 as tables
from nautobot.apps.tables import BaseTable, BooleanColumn, ButtonsColumn, TagColumn, ToggleColumn

from containerlab_orchestrator.models import ContainerlabHost


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
