"""REST API views for the containerlab_orchestrator app."""

from nautobot.apps.api import ModelViewSet, NautobotModelViewSet, ReadOnlyModelViewSet

from containerlab_orchestrator.api.serializers import (
    ContainerlabHostSerializer,
    InterfaceMappingSerializer,
    LabNodeSerializer,
    LabSerializer,
    NodeMappingSerializer,
)
from containerlab_orchestrator.filters import (
    ContainerlabHostFilterSet,
    InterfaceMappingFilterSet,
    LabFilterSet,
    LabNodeFilterSet,
    NodeMappingFilterSet,
)
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping


class ContainerlabHostViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API viewset for ContainerlabHost."""

    queryset = ContainerlabHost.objects.all()
    serializer_class = ContainerlabHostSerializer
    filterset_class = ContainerlabHostFilterSet


class NodeMappingViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API viewset for NodeMapping."""

    queryset = NodeMapping.objects.select_related("device_type__manufacturer", "platform")
    serializer_class = NodeMappingSerializer
    filterset_class = NodeMappingFilterSet


class InterfaceMappingViewSet(ModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API viewset for InterfaceMapping."""

    queryset = InterfaceMapping.objects.select_related(
        "node_mapping__device_type__manufacturer", "node_mapping__platform"
    )
    serializer_class = InterfaceMappingSerializer
    filterset_class = InterfaceMappingFilterSet


class LabViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API viewset for Lab."""

    queryset = Lab.objects.select_related("host", "owner", "status", "dynamic_group")
    serializer_class = LabSerializer
    filterset_class = LabFilterSet


class LabNodeViewSet(ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Read-only REST API viewset for LabNode. The Build and Inspect jobs write the rows."""

    queryset = LabNode.objects.select_related("lab__host", "device")
    serializer_class = LabNodeSerializer
    filterset_class = LabNodeFilterSet
