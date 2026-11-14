"""REST API views for the containerlab_orchestrator app."""

from nautobot.apps.api import ModelViewSet, NautobotModelViewSet

from containerlab_orchestrator.api.serializers import (
    ContainerlabHostSerializer,
    InterfaceMappingSerializer,
    NodeMappingSerializer,
)
from containerlab_orchestrator.filters import ContainerlabHostFilterSet, InterfaceMappingFilterSet, NodeMappingFilterSet
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping


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
