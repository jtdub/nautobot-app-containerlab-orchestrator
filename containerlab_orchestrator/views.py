"""Views for the containerlab_orchestrator app."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from nautobot.apps.ui import (
    Button,
    ObjectDetailContent,
    ObjectFieldsPanel,
    ObjectsTablePanel,
    ObjectTextPanel,
    SectionChoices,
)
from nautobot.apps.views import NautobotUIViewSet
from nautobot.dcim.tables import DeviceTable

from containerlab_orchestrator import filters, forms, models, tables
from containerlab_orchestrator.api import serializers

INTERFACE_ROWS_PREFIX = "interface_mappings"


def _has_interface_rows(request):
    """Return True when the POST of the request holds the interface rows formset."""
    return f"{INTERFACE_ROWS_PREFIX}-TOTAL_FORMS" in request.POST


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
            ObjectsTablePanel(
                weight=200,
                section=SectionChoices.LEFT_HALF,
                table_class=tables.InterfaceMappingTable,
                table_filter="node_mapping",
                table_title="Interface Mappings",
                add_button_route=None,
                enable_related_link=False,
                max_display_count=1000,
                show_table_config_button=False,
                footer_buttons=(
                    Button(
                        weight=100,
                        label="Edit interfaces",
                        icon="mdi-pencil",
                        link_name="plugins:containerlab_orchestrator:nodemapping_edit",
                        required_permissions=["containerlab_orchestrator.change_nodemapping"],
                    ),
                ),
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

    def get_extra_context(self, request, instance=None):
        """Add the interface rows formset to the create and edit pages."""
        context = super().get_extra_context(request, instance)
        if self.action in ("create", "update"):
            data = request.POST if _has_interface_rows(request) else None
            context[INTERFACE_ROWS_PREFIX] = forms.InterfaceMappingFormSet(
                data=data, instance=instance, prefix=INTERFACE_ROWS_PREFIX
            )
        return context

    def form_save(self, form, **kwargs):
        """Save the node mapping and, when the POST holds them, its interface rows.

        Raises:
            ValidationError: If a row is not valid, or if the database rejects a row as a duplicate.
        """
        obj = super().form_save(form, **kwargs)
        if not _has_interface_rows(self.request):
            return obj
        interface_rows = forms.InterfaceMappingFormSet(
            data=self.request.POST, instance=obj, prefix=INTERFACE_ROWS_PREFIX
        )
        if not interface_rows.is_valid():
            raise ValidationError("Fix the errors in the interface rows.")
        try:
            with transaction.atomic():
                interface_rows.save()
        except IntegrityError as error:
            raise ValidationError(
                "Two interface rows have the same name. "
                "Names that differ only in case or accent count as the same name."
            ) from error
        return obj


class LabUIViewSet(NautobotUIViewSet):  # pylint: disable=too-many-ancestors
    """List, detail, create, edit, and delete pages for Lab.

    The list page has no Import button, because the CSV import rejects each lab row.
    """

    action_buttons = ("add", "export")
    bulk_update_form_class = forms.LabBulkEditForm
    filterset_class = filters.LabFilterSet
    filterset_form_class = forms.LabFilterForm
    form_class = forms.LabForm
    queryset = models.Lab.objects.select_related("host", "owner", "status", "dynamic_group")
    serializer_class = serializers.LabSerializer
    table_class = tables.LabTable

    object_detail_content = ObjectDetailContent(
        panels=(
            ObjectFieldsPanel(
                weight=100,
                section=SectionChoices.LEFT_HALF,
                fields=["name", "host", "owner", "status", "config_source", "mgmt_subnet", "built_at"],
            ),
            ObjectFieldsPanel(
                weight=100,
                section=SectionChoices.RIGHT_HALF,
                label="Selection",
                fields=["dynamic_group"],
            ),
            ObjectTextPanel(
                weight=200,
                section=SectionChoices.RIGHT_HALF,
                label="Device filter",
                object_field="device_filter",
                render_as=ObjectTextPanel.RenderOptions.JSON,
            ),
            ObjectsTablePanel(
                weight=300,
                section=SectionChoices.RIGHT_HALF,
                table_class=DeviceTable,
                table_attribute="devices",
                related_field_name="labs",
                table_title="Devices",
                add_button_route=None,
                enable_related_link=False,
                max_display_count=1000,
                show_table_config_button=False,
                exclude_columns=["pk", "tenant", "rack", "primary_ip", "actions"],
                select_related_fields=["status", "role", "device_type__manufacturer", "location"],
            ),
            ObjectsTablePanel(
                weight=200,
                section=SectionChoices.FULL_WIDTH,
                table_class=tables.LabNodeTable,
                table_filter="lab",
                table_title="Nodes",
                add_button_route=None,
                enable_related_link=False,
                max_display_count=1000,
                show_table_config_button=False,
                select_related_fields=["device"],
            ),
        ),
    )

    def form_save(self, form, **kwargs):
        """Save the lab. On create, make the user the owner and set the Planned status.

        Raises:
            ValidationError: If the Planned status is not available for labs.
        """
        if not form.instance.present_in_database:
            form.instance.set_create_defaults(self.request.user)
        return super().form_save(form, **kwargs)
