"""REST API views for the containerlab_orchestrator app."""

from nautobot.apps.api import NautobotModelViewSet

from containerlab_orchestrator.api.serializers import ContainerlabHostSerializer
from containerlab_orchestrator.filters import ContainerlabHostFilterSet
from containerlab_orchestrator.models import ContainerlabHost


class ContainerlabHostViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API viewset for ContainerlabHost."""

    queryset = ContainerlabHost.objects.all()
    serializer_class = ContainerlabHostSerializer
    filterset_class = ContainerlabHostFilterSet
