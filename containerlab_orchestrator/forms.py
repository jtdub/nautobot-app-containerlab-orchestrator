"""Forms for the containerlab_orchestrator app."""

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.forms import (
    APISelectMultiple,
    BootstrapMixin,
    BulkEditNullBooleanSelect,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    JSONField,
    NautobotBulkEditForm,
    NautobotFilterForm,
    NautobotModelForm,
    StaticSelect2,
    StatusModelFilterFormMixin,
    TagFilterField,
    TagsBulkEditFormMixin,
    add_blank_choice,
)
from nautobot.dcim.models import Device, DeviceType, Platform
from nautobot.extras.models import DynamicGroup, SecretsGroup

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, NodeMapping


class ContainerlabHostForm(NautobotModelForm):  # pylint: disable=too-many-ancestors
    """Form to create and edit a ContainerlabHost."""

    secrets_group = DynamicModelChoiceField(queryset=SecretsGroup.objects.all())

    class Meta:
        """Meta attributes for ContainerlabHostForm."""

        model = ContainerlabHost
        fields = "__all__"


class ContainerlabHostBulkEditForm(TagsBulkEditFormMixin, NautobotBulkEditForm):  # pylint: disable=too-many-ancestors
    """Form to edit many ContainerlabHost records at once."""

    pk = forms.ModelMultipleChoiceField(queryset=ContainerlabHost.objects.all(), widget=forms.MultipleHiddenInput)
    remote_url = forms.CharField(required=False, max_length=500, label="URL")
    verify_ssl = forms.NullBooleanField(required=False, label="Verify SSL", widget=BulkEditNullBooleanSelect)
    secrets_group = DynamicModelChoiceField(required=False, queryset=SecretsGroup.objects.all())
    description = forms.CharField(required=False, max_length=CHARFIELD_MAX_LENGTH)

    class Meta:
        """Meta attributes for ContainerlabHostBulkEditForm."""

        nullable_fields = ["description"]


class ContainerlabHostFilterForm(NautobotFilterForm):  # pylint: disable=too-many-ancestors
    """Form to filter the ContainerlabHost list."""

    model = ContainerlabHost
    field_order = ["q", "name", "secrets_group", "verify_ssl", "tag"]

    q = forms.CharField(required=False, label="Search")
    name = forms.CharField(required=False, label="Name")
    secrets_group = DynamicModelMultipleChoiceField(
        queryset=SecretsGroup.objects.all(),
        to_field_name="name",
        required=False,
        label="Secrets group",
    )
    verify_ssl = forms.NullBooleanField(
        required=False,
        label="Verify SSL",
        widget=StaticSelect2(choices=add_blank_choice((("True", "Yes"), ("False", "No")))),
    )
    tag = TagFilterField(model)


class NodeMappingForm(NautobotModelForm):  # pylint: disable=too-many-ancestors
    """Form to create and edit a NodeMapping."""

    device_type = DynamicModelChoiceField(queryset=DeviceType.objects.all(), required=False)
    platform = DynamicModelChoiceField(queryset=Platform.objects.all(), required=False)
    env = JSONField(
        required=False,
        label="Environment variables",
        help_text=NodeMapping._meta.get_field("env").help_text,
    )
    overrides = JSONField(required=False, help_text=NodeMapping._meta.get_field("overrides").help_text)

    class Meta:
        """Meta attributes for NodeMappingForm."""

        model = NodeMapping
        fields = "__all__"

    def clean_env(self):
        """Return an empty object when the env field is empty."""
        return self.cleaned_data["env"] if self.cleaned_data["env"] is not None else {}

    def clean_overrides(self):
        """Return an empty object when the overrides field is empty."""
        return self.cleaned_data["overrides"] if self.cleaned_data["overrides"] is not None else {}


class InterfaceMappingRowForm(BootstrapMixin, forms.ModelForm):
    """Form for one interface row on the node mapping edit form."""

    class Meta:
        """Meta attributes for InterfaceMappingRowForm."""

        model = InterfaceMapping
        fields = ("physical_name", "container_name")

    def __init__(self, *args, **kwargs):
        """Remove the browser required check, so that the user can save with an empty row or a deleted row.

        The server still rejects an empty name in a changed row.
        """
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.pop("required", None)


InterfaceMappingFormSet = inlineformset_factory(
    parent_model=NodeMapping,
    model=InterfaceMapping,
    form=InterfaceMappingRowForm,
    extra=1,
)


class NodeMappingBulkEditForm(TagsBulkEditFormMixin, NautobotBulkEditForm):  # pylint: disable=too-many-ancestors
    """Form to edit many NodeMapping records at once."""

    pk = forms.ModelMultipleChoiceField(queryset=NodeMapping.objects.all(), widget=forms.MultipleHiddenInput)
    kind = forms.CharField(required=False, max_length=CHARFIELD_MAX_LENGTH, label="Kind")
    image = forms.CharField(required=False, max_length=CHARFIELD_MAX_LENGTH, label="Image")
    node_type = forms.CharField(required=False, max_length=CHARFIELD_MAX_LENGTH, label="Type")
    cpu = forms.DecimalField(required=False, max_digits=5, decimal_places=2, min_value=Decimal("0.01"), label="CPU")
    memory = forms.CharField(required=False, max_length=32, label="Memory")
    config_source = forms.ChoiceField(
        required=False,
        choices=add_blank_choice(NodeMappingConfigSourceChoices),
        widget=StaticSelect2(),
        label="Config source",
    )

    class Meta:
        """Meta attributes for NodeMappingBulkEditForm."""

        nullable_fields = ["node_type", "cpu", "memory", "config_source"]


class NodeMappingFilterForm(NautobotFilterForm):  # pylint: disable=too-many-ancestors
    """Form to filter the NodeMapping list."""

    model = NodeMapping
    field_order = ["q", "device_type", "platform", "kind", "config_source", "tag"]

    q = forms.CharField(required=False, label="Search")
    device_type = DynamicModelMultipleChoiceField(
        queryset=DeviceType.objects.all(),
        required=False,
        label="Device type",
    )
    platform = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(),
        to_field_name="name",
        required=False,
        label="Platform",
    )
    kind = forms.CharField(required=False, label="Kind")
    config_source = forms.ChoiceField(
        required=False,
        choices=add_blank_choice(NodeMappingConfigSourceChoices),
        widget=StaticSelect2(),
        label="Config source",
    )
    tag = TagFilterField(model)


def _device_dynamic_group_field():
    """Return an optional choice field of the Dynamic Groups of devices."""
    return DynamicModelChoiceField(
        queryset=DynamicGroup.objects.filter(content_type__app_label="dcim", content_type__model="device"),
        query_params={"content_type": "dcim.device"},
        required=False,
        label="Dynamic Group",
    )


def _lab_config_source_field():
    """Return an optional choice field of the default config sources of a lab."""
    return forms.ChoiceField(
        required=False,
        choices=add_blank_choice(NodeMappingConfigSourceChoices),
        widget=StaticSelect2(),
        label="Default config source",
    )


class LabForm(NautobotModelForm):  # pylint: disable=too-many-ancestors
    """Form to create and edit a Lab. The form has no owner and no status field."""

    host = DynamicModelChoiceField(queryset=ContainerlabHost.objects.all(), label="Host")
    dynamic_group = _device_dynamic_group_field()
    devices = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False, label="Devices")
    device_filter = JSONField(
        required=False,
        label="Device filter",
        help_text=Lab._meta.get_field("device_filter").help_text,
    )

    class Meta:
        """Meta attributes for LabForm."""

        model = Lab
        fields = "__all__"

    def clean_device_filter(self):
        """Return an empty object when the device filter field is empty."""
        return self.cleaned_data["device_filter"] if self.cleaned_data["device_filter"] is not None else {}


class LabBulkEditForm(TagsBulkEditFormMixin, NautobotBulkEditForm):  # pylint: disable=too-many-ancestors
    """Form to edit many Lab records at once."""

    pk = forms.ModelMultipleChoiceField(queryset=Lab.objects.all(), widget=forms.MultipleHiddenInput)
    host = DynamicModelChoiceField(queryset=ContainerlabHost.objects.all(), required=False, label="Host")
    config_source = _lab_config_source_field()
    dynamic_group = _device_dynamic_group_field()

    class Meta:
        """Meta attributes for LabBulkEditForm."""

        nullable_fields = ["dynamic_group"]


class LabFilterForm(NautobotFilterForm, StatusModelFilterFormMixin):  # pylint: disable=too-many-ancestors
    """Form to filter the Lab list."""

    model = Lab
    field_order = ["q", "host", "owner", "status", "config_source", "tag"]

    q = forms.CharField(required=False, label="Search")
    host = DynamicModelMultipleChoiceField(
        queryset=ContainerlabHost.objects.all(),
        to_field_name="name",
        required=False,
        label="Host",
    )
    owner = DynamicModelMultipleChoiceField(
        queryset=get_user_model().objects.all(),
        to_field_name="username",
        required=False,
        label="Owner",
        widget=APISelectMultiple(api_url="/api/users/users/"),
    )
    config_source = _lab_config_source_field()
    tag = TagFilterField(model)
