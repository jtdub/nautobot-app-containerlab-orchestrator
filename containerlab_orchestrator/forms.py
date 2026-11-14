"""Forms for the containerlab_orchestrator app."""

from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.forms import (
    BootstrapMixin,
    BulkEditNullBooleanSelect,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    JSONField,
    NautobotBulkEditForm,
    NautobotFilterForm,
    NautobotModelForm,
    StaticSelect2,
    TagFilterField,
    TagsBulkEditFormMixin,
    add_blank_choice,
)
from nautobot.dcim.models import DeviceType, Platform
from nautobot.extras.models import SecretsGroup

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, NodeMapping


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
