"""Filter sets for the containerlab_orchestrator app."""

from django.contrib.auth import get_user_model
from nautobot.apps.filters import (
    BaseFilterSet,
    ModelMultipleChoiceFilter,
    NaturalKeyOrPKMultipleChoiceFilter,
    NautobotFilterSet,
    SearchFilter,
    StatusModelFilterSetMixin,
)
from nautobot.dcim.models import Device, DeviceType, Platform
from nautobot.extras.models import DynamicGroup, SecretsGroup

from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping


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


class LabFilterSet(StatusModelFilterSetMixin, NautobotFilterSet):  # pylint: disable=too-many-ancestors
    """Filter set for Lab."""

    q = SearchFilter(filter_predicates={"name": "icontains", "host__name": "icontains", "mgmt_subnet": "icontains"})
    host = NaturalKeyOrPKMultipleChoiceFilter(queryset=ContainerlabHost.objects.all(), label="Host (ID or name)")
    owner = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=get_user_model().objects.all(),
        to_field_name="username",
        label="Owner (ID or username)",
    )
    dynamic_group = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=DynamicGroup.objects.all(),
        label="Dynamic Group (ID or name)",
    )
    devices = NaturalKeyOrPKMultipleChoiceFilter(queryset=Device.objects.all(), label="Devices (ID or name)")

    class Meta:
        """Meta attributes for LabFilterSet."""

        model = Lab
        fields = "__all__"


class LabNodeFilterSet(BaseFilterSet):
    """Filter set for LabNode."""

    q = SearchFilter(filter_predicates={"node_name": "icontains", "kind": "icontains", "image": "icontains"})
    lab = ModelMultipleChoiceFilter(queryset=Lab.objects.all(), label="Lab (ID)")
    device = NaturalKeyOrPKMultipleChoiceFilter(queryset=Device.objects.all(), label="Device (ID or name)")

    class Meta:
        """Meta attributes for LabNodeFilterSet."""

        model = LabNode
        fields = "__all__"
