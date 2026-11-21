"""Views for the containerlab_orchestrator app."""

import logging
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from nautobot.apps.ui import (
    Button,
    ButtonColorChoices,
    ObjectDetailContent,
    ObjectFieldsPanel,
    ObjectsTablePanel,
    ObjectTextPanel,
    Panel,
    SectionChoices,
    Tab,
    render_component_template,
)
from nautobot.apps.views import NautobotUIViewSet
from nautobot.extras.models import Job

from containerlab_orchestrator import filters, forms, models, tables
from containerlab_orchestrator.api import serializers
from containerlab_orchestrator.deploy import DEPLOY_STATUSES, STATUS_MESSAGE
from containerlab_orchestrator.destroy import DESTROY_STATUS_MESSAGE, DESTROY_STATUSES
from containerlab_orchestrator.graph import SKIPPED_LINKS_TEXT, STATE_GROUPS, lab_graph
from containerlab_orchestrator.inspection import INSPECT_STATUS_MESSAGE, INSPECT_STATUSES
from containerlab_orchestrator.jobs import DestroyLab, RedeployLab, lab_access_message
from containerlab_orchestrator.redeploy import REDEPLOY_STATUS_MESSAGE, REDEPLOY_STATUSES
from containerlab_orchestrator.sync import LAB_JOBS_MODULE, is_busy

logger = logging.getLogger(__name__)

INTERFACE_ROWS_PREFIX = "interface_mappings"
NO_BUILD_TEXT = "This lab has no topology. Run the Build Lab Topology job."
LARGE_YAML_TEXT = "The topology YAML has {size}. Use Download YAML to get it."
TAB_YAML_LIMIT = 1_000_000
"""The most characters of topology YAML that the Topology YAML tab shows."""
GRAPH_CHART_ID = "lab-topology-graph"
GRAPH_TEMPLATE = "containerlab_orchestrator/lab_topology_graph.html"
GRAPH_YAML_LIMIT = 10_000_000
"""The most characters of topology YAML that the Topology tab reads for the graph."""
GRAPH_CAPTION_TEXT = "The colors show the last stored state of each node. Run Inspect Lab to read the state again."
GRAPH_NO_LIBRARY_TEXT = "The graph cannot load. The Nodes table on the main tab shows the same nodes."
LARGE_GRAPH_TEXT = "The topology YAML has {size}. The graph shows a topology YAML of 10,000,000 characters or less."


class TopologyYamlPanel(ObjectTextPanel):
    """Show the stored topology YAML of the lab as escaped text, or a message when there is none or it is too large."""

    def render_body_content(self, context):
        """Return the panel body for the topology YAML of the lab in the context."""
        value = self.get_value(context)
        if not value:
            return format_html("<p>{}</p>", NO_BUILD_TEXT)
        if len(value) > TAB_YAML_LIMIT:
            return format_html("<p>{}</p>", LARGE_YAML_TEXT.format(size=f"{len(value):,} characters"))
        return super().render_body_content(context)


class TopologyGraphPanel(Panel):
    """Show the graph data, the legend, and the caption of the lab graph, or a message when the graph cannot show."""

    def render_body_content(self, context):
        """Return the panel body for the graph of the lab in the context."""
        lab = context["object"]
        if not lab.topology_yaml:
            return format_html("<p>{}</p>", NO_BUILD_TEXT)
        if len(lab.topology_yaml) > GRAPH_YAML_LIMIT:
            return format_html("<p>{}</p>", LARGE_GRAPH_TEXT.format(size=f"{len(lab.topology_yaml):,} characters"))
        graph = lab_graph(lab, context["request"].user)
        if graph.message:
            return format_html("<p>{}</p>", graph.message)
        skipped = graph.data["skipped_links"]
        return render_component_template(
            GRAPH_TEMPLATE,
            context,
            chart_id=GRAPH_CHART_ID,
            graph_data=graph.data,
            state_groups=STATE_GROUPS,
            caption_text=GRAPH_CAPTION_TEXT,
            no_library_text=GRAPH_NO_LIBRARY_TEXT,
            skipped_text=SKIPPED_LINKS_TEXT.format(count=skipped) if skipped else "",
        )


class TopologyDownloadButton(Button):
    """Link to the topology YAML download, shown only when the lab has a topology YAML."""

    def should_render(self, context):
        """Return True if the user has the permissions and the lab in the context has a topology YAML."""
        return super().should_render(context) and bool(context["object"].topology_yaml)


class LabJobAccess(NamedTuple):
    """The lab job buttons that the user of a request can see on one lab."""

    runnable: frozenset
    changing_allowed: bool
    inspect_allowed: bool
    busy: bool


NO_ACCESS = LabJobAccess(frozenset(), False, False, False)
LAB_JOB_CLASS_NAMES = ("BuildLabTopology", "DeployLab", "RedeployLab", "DestroyLab", "InspectLab")
BUSY_TOOLTIP = "A lab job of this lab runs."
LAB_JOB_ACCESS_ATTRIBUTE = "_containerlab_lab_job_access"
"""The request attribute that holds the lab job access of each lab, keyed by the lab pk."""


def _read_lab_job_access(user, lab):
    """Return the lab jobs that the user can run on the lab, with the access checks of the jobs."""
    runnable = frozenset(
        Job.objects.restrict(user, "run")
        .filter(module_name=LAB_JOBS_MODULE, job_class_name__in=LAB_JOB_CLASS_NAMES, installed=True, enabled=True)
        .values_list("job_class_name", flat=True)
    )
    if not runnable:
        return NO_ACCESS
    inspect_allowed = lab_access_message(lab, user, "inspect", owner_check=False) == ""
    changing_allowed = inspect_allowed and (user.is_superuser or lab.owner_id == user.pk)
    shows_button = changing_allowed or (inspect_allowed and "InspectLab" in runnable)
    return LabJobAccess(runnable, changing_allowed, inspect_allowed, busy=shows_button and is_busy(lab))


def lab_job_access(context):
    """Return the lab job access of the request user on the lab in the context, read one time for each request.

    A database error shows no lab job button and goes to the log, so the rest of the page still shows.
    """
    request = context["request"]
    lab = context["object"]
    cache = vars(request).setdefault(LAB_JOB_ACCESS_ATTRIBUTE, {})
    if lab.pk not in cache:
        try:
            cache[lab.pk] = _read_lab_job_access(request.user, lab)
        except DatabaseError:
            logger.exception(
                "The lab page could not read the lab job access of lab %s, so it shows no lab job button.", lab
            )
            cache[lab.pk] = NO_ACCESS
    return cache[lab.pk]


class LabJobButton(Button):
    """Link to the run form of one lab job with the lab selected, shown only when the job accepts the user.

    A status that the job does not accept gives a disabled button with the status message of the job as its title.
    Else a busy lab gives a disabled button with BUSY_TOOLTIP as its title.
    """

    job_class_name = None
    start_statuses = None
    """The lab statuses that the job accepts, or None when the job accepts each status."""
    status_message = ""
    changing = True
    confirm_text = ""

    def should_render(self, context):
        """Return True if the user can run the job and passes the lab access check of the job."""
        if not super().should_render(context):
            return False
        access = lab_job_access(context)
        if self.job_class_name not in access.runnable:
            return False
        return access.changing_allowed if self.changing else access.inspect_allowed

    def disabled_reason(self, context):
        """Return the reason that the button is disabled, or an empty string if it is enabled."""
        lab = context["object"]
        if self.start_statuses is None or lab.status.name in self.start_statuses:  # pylint: disable=unsupported-membership-test
            return BUSY_TOOLTIP if lab_job_access(context).busy else ""
        return self.status_message.format(lab=lab, status=lab.status)

    def get_link(self, context):
        """Return the run form link of the job with the lab selected, or None if the button is disabled."""
        if self.disabled_reason(context):
            return None
        run_url = reverse(
            "extras:job_run_by_class_path", kwargs={"class_path": f"{LAB_JOBS_MODULE}.{self.job_class_name}"}
        )
        return f"{run_url}?{urlencode({'lab': context['object'].pk})}"

    def get_extra_context(self, context):
        """Add the confirmation text, and the disabled attributes when the button is disabled."""
        extra_context = {**super().get_extra_context(context), "confirm_text": self.confirm_text}
        reason = self.disabled_reason(context)
        if reason:
            extra_context["attributes"] = {"disabled": "disabled", "aria-disabled": "true", "title": reason}
        return extra_context


LAB_JOB_CONFIRM_SCRIPT = "containerlab_orchestrator/lab_job_confirm.js"
LAB_JOB_BUTTONS = (
    LabJobButton(weight=200, label="Build", icon="mdi-hammer-wrench", job_class_name="BuildLabTopology"),
    LabJobButton(
        weight=300,
        label="Deploy",
        icon="mdi-rocket-launch",
        job_class_name="DeployLab",
        start_statuses=DEPLOY_STATUSES,
        status_message=STATUS_MESSAGE,
    ),
    LabJobButton(
        weight=400,
        label="Redeploy",
        icon="mdi-restart",
        color=ButtonColorChoices.RED,
        job_class_name="RedeployLab",
        start_statuses=REDEPLOY_STATUSES,
        status_message=REDEPLOY_STATUS_MESSAGE,
        javascript_template_path=LAB_JOB_CONFIRM_SCRIPT,
        confirm_text=RedeployLab.Meta.description,
    ),
    LabJobButton(
        weight=500,
        label="Destroy",
        icon="mdi-delete",
        color=ButtonColorChoices.RED,
        job_class_name="DestroyLab",
        start_statuses=DESTROY_STATUSES,
        status_message=DESTROY_STATUS_MESSAGE,
        javascript_template_path=LAB_JOB_CONFIRM_SCRIPT,
        confirm_text=DestroyLab.Meta.description,
    ),
    LabJobButton(
        weight=600,
        label="Inspect",
        icon="mdi-magnify",
        job_class_name="InspectLab",
        start_statuses=INSPECT_STATUSES,
        status_message=INSPECT_STATUS_MESSAGE,
        changing=False,
    ),
)


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
                table_class=tables.LabDeviceTable,
                table_attribute="devices",
                related_field_name="labs",
                table_title="Devices",
                add_button_route=None,
                enable_related_link=False,
                max_display_count=1000,
                show_table_config_button=False,
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
        extra_tabs=(
            Tab(
                tab_id="topology",
                label="Topology",
                weight=Tab.WEIGHT_MAIN_TAB + 40,
                panels=(
                    TopologyGraphPanel(
                        weight=100,
                        section=SectionChoices.FULL_WIDTH,
                        label="Topology graph",
                        body_id="topology-graph",
                    ),
                ),
            ),
            Tab(
                tab_id="topology_yaml",
                label="Topology YAML",
                weight=Tab.WEIGHT_MAIN_TAB + 50,
                panels=(
                    TopologyYamlPanel(
                        weight=100,
                        section=SectionChoices.FULL_WIDTH,
                        label="Topology YAML",
                        object_field="topology_yaml",
                        render_as=ObjectTextPanel.RenderOptions.CODE,
                    ),
                ),
            ),
        ),
        extra_buttons=(
            TopologyDownloadButton(
                weight=100,
                label="Download YAML",
                icon="mdi-download",
                link_name="plugins-api:containerlab_orchestrator-api:lab-topology",
                render_on_tab_id="topology_yaml",
                required_permissions=["containerlab_orchestrator.view_lab"],
            ),
            *LAB_JOB_BUTTONS,
        ),
    )

    def get_queryset(self):
        """Return the labs. The list page does not load the topology YAML, because it can be large."""
        queryset = super().get_queryset()
        if self.action == "list":
            return queryset.defer("topology_yaml")
        return queryset

    def form_save(self, form, **kwargs):
        """Save the lab. On create, make the user the owner and set the Planned status.

        Raises:
            ValidationError: If the Planned status is not available for labs.
        """
        if not form.instance.present_in_database:
            form.instance.set_create_defaults(self.request.user)
        return super().form_save(form, **kwargs)
