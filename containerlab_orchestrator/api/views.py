"""REST API views for the containerlab_orchestrator app."""

from django.http import HttpResponse
from django.utils.http import content_disposition_header
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from nautobot.apps.api import ModelViewSet, NautobotModelViewSet, ReadOnlyModelViewSet
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.settings import api_settings

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

NO_TOPOLOGY_MESSAGE = "Lab {lab} has no topology. Run the Build Lab Topology job first."
TOPOLOGY_RENDERERS = [renderer for renderer in api_settings.DEFAULT_RENDERER_CLASSES if renderer.format != "csv"]
"""The renderers of the topology download. With no CSV renderer, a CSV request cannot rename the file."""


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

    @extend_schema(
        methods=["get"],
        description="Download the stored topology YAML of the lab as `<lab>.clab.yml`.",
        responses={(200, "application/yaml"): OpenApiTypes.STR},
    )
    @action(detail=True, methods=["get"], url_path="topology", renderer_classes=TOPOLOGY_RENDERERS)
    def topology(self, request, pk=None):  # pylint: disable=unused-argument
        """Download the stored topology YAML of the lab as `<lab>.clab.yml`.

        Raises:
            NotFound: If the lab has no topology YAML.
        """
        lab = self.get_object()
        if not lab.topology_yaml:
            raise NotFound(NO_TOPOLOGY_MESSAGE.format(lab=lab))
        return HttpResponse(
            lab.topology_yaml,
            content_type="application/yaml",
            headers={"Content-Disposition": content_disposition_header(True, f"{lab.name}.clab.yml")},
        )


class LabNodeViewSet(ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Read-only REST API viewset for LabNode. The Build and Inspect jobs write the rows."""

    queryset = LabNode.objects.select_related("lab__host", "device")
    serializer_class = LabNodeSerializer
    filterset_class = LabNodeFilterSet
