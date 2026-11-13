"""Views for the containerlab_orchestrator app."""

from nautobot.apps.ui import ObjectDetailContent, ObjectFieldsPanel, SectionChoices
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
