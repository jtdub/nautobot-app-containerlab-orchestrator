"""Filter sets for the containerlab_orchestrator app."""

from nautobot.apps.filters import NaturalKeyOrPKMultipleChoiceFilter, NautobotFilterSet, SearchFilter
from nautobot.dcim.models import DeviceType, Platform
from nautobot.extras.models import SecretsGroup

from containerlab_orchestrator.models import ContainerlabHost, NodeMapping


class ContainerlabHostFilterSet(NautobotFilterSet):
    """Filter set for ContainerlabHost."""

    q = SearchFilter(
        filter_predicates={
            "name": "icontains",
            "remote_url": "icontains",
            "description": "icontains",
        },
    )
    secrets_group = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=SecretsGroup.objects.all(),
        label="Secrets group (ID or name)",
    )

    class Meta:
        """Meta attributes for ContainerlabHostFilterSet."""

        model = ContainerlabHost
        fields = "__all__"


class NodeMappingFilterSet(NautobotFilterSet):
    """Filter set for NodeMapping."""

    q = SearchFilter(
        filter_predicates={
            "kind": "icontains",
            "image": "icontains",
            "node_type": "icontains",
            "device_type__model": "icontains",
            "platform__name": "icontains",
        },
    )
    device_type = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=DeviceType.objects.all(),
        to_field_name="model",
        label="Device type (ID or model)",
    )
    platform = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=Platform.objects.all(),
        label="Platform (ID or name)",
    )

    class Meta:
        """Meta attributes for NodeMappingFilterSet."""

        model = NodeMapping
        fields = "__all__"
