"""Views for the containerlab_orchestrator app."""

from nautobot.apps.ui import ObjectDetailContent, ObjectFieldsPanel, ObjectTextPanel, SectionChoices
from nautobot.apps.views import NautobotUIViewSet

from containerlab_orchestrator import filters, forms, models, tables
from containerlab_orchestrator.api import serializers


class ContainerlabHostUIViewSet(NautobotUIViewSet):  # pylint: disable=too-many-ancestors
    """List, detail, create, edit, and delete pages for ContainerlabHost."""

    bulk_update_form_class = forms.ContainerlabHostBulkEditForm
    filterset_class = filters.ContainerlabHostFilterSet
    filterset_form_class = forms.ContainerlabHostFilterForm
    form_class = forms.ContainerlabHostForm
    queryset = models.ContainerlabHost.objects.select_related("secrets_group")
    serializer_class = serializers.ContainerlabHostSerializer
    table_class = tables.ContainerlabHostTable

    object_detail_content = ObjectDetailContent(
        panels=(
            ObjectFieldsPanel(
                weight=100,
                section=SectionChoices.LEFT_HALF,
                fields=["name", "remote_url", "verify_ssl", "secrets_group", "description"],
            ),
        ),
    )


class NodeMappingUIViewSet(NautobotUIViewSet):  # pylint: disable=too-many-ancestors
    """List, detail, create, edit, and delete pages for NodeMapping."""

    bulk_update_form_class = forms.NodeMappingBulkEditForm
    filterset_class = filters.NodeMappingFilterSet
    filterset_form_class = forms.NodeMappingFilterForm
    form_class = forms.NodeMappingForm
    queryset = models.NodeMapping.objects.select_related("device_type__manufacturer", "platform")
    serializer_class = serializers.NodeMappingSerializer
    table_class = tables.NodeMappingTable

    object_detail_content = ObjectDetailContent(
        panels=(
            ObjectFieldsPanel(
                weight=100,
                section=SectionChoices.LEFT_HALF,
                fields=["device_type", "platform", "kind", "image", "node_type", "cpu", "memory", "config_source"],
            ),
            ObjectTextPanel(
                weight=100,
                section=SectionChoices.RIGHT_HALF,
                label="Environment variables (not secret)",
                object_field="env",
                render_as=ObjectTextPanel.RenderOptions.JSON,
            ),
            ObjectTextPanel(
                weight=200,
                section=SectionChoices.RIGHT_HALF,
                label="Overrides",
                object_field="overrides",
                render_as=ObjectTextPanel.RenderOptions.JSON,
            ),
            ObjectTextPanel(
                weight=300,
                section=SectionChoices.FULL_WIDTH,
                label="Config template",
                object_field="config_template",
                render_as=ObjectTextPanel.RenderOptions.CODE,
            ),
        ),
    )
