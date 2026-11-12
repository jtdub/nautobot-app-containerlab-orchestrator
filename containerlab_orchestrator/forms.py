"""Forms for the containerlab_orchestrator app."""

from django import forms
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.forms import (
    BulkEditNullBooleanSelect,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    NautobotBulkEditForm,
    NautobotFilterForm,
    NautobotModelForm,
    TagFilterField,
    TagsBulkEditFormMixin,
)
from nautobot.extras.models import SecretsGroup

from containerlab_orchestrator.models import ContainerlabHost


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
    remote_url = forms.CharField(required=False, label="URL")
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
    name = forms.CharField(required=False)
    secrets_group = DynamicModelMultipleChoiceField(
        queryset=SecretsGroup.objects.all(),
        to_field_name="name",
        required=False,
    )
    verify_ssl = forms.NullBooleanField(required=False, label="Verify SSL", widget=BulkEditNullBooleanSelect)
    tag = TagFilterField(model)
