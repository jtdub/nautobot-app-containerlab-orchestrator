"""REST API views for the containerlab_orchestrator app."""

from nautobot.apps.api import NautobotModelViewSet

from containerlab_orchestrator.api.serializers import ContainerlabHostSerializer, NodeMappingSerializer
from containerlab_orchestrator.filters import ContainerlabHostFilterSet, NodeMappingFilterSet
from containerlab_orchestrator.models import ContainerlabHost, NodeMapping


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
