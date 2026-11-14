"""Filter sets for the containerlab_orchestrator app."""

from nautobot.apps.filters import (
    BaseFilterSet,
    ModelMultipleChoiceFilter,
    NaturalKeyOrPKMultipleChoiceFilter,
    NautobotFilterSet,
    SearchFilter,
)
from nautobot.dcim.models import DeviceType, Platform
from nautobot.extras.models import SecretsGroup

from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping


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


class InterfaceMappingFilterSet(BaseFilterSet):
    """Filter set for InterfaceMapping."""

    q = SearchFilter(filter_predicates={"physical_name": "icontains", "container_name": "icontains"})
    node_mapping = ModelMultipleChoiceFilter(queryset=NodeMapping.objects.all(), label="Node mapping (ID)")

    class Meta:
        """Meta attributes for InterfaceMappingFilterSet."""

        model = InterfaceMapping
        fields = "__all__"
