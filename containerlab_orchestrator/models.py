"""Models for the containerlab_orchestrator app."""

from decimal import Decimal

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from jinja2 import TemplateError, TemplateSyntaxError
from nautobot.apps.choices import SecretsGroupAccessTypeChoices
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.models import LaxURLField, PrimaryModel, extras_features
from nautobot.apps.utils import validate_jinja2
from nautobot.extras.models import SecretsGroup, SecretsGroupAssociation

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.constants import REQUIRED_HTTP_SECRET_TYPES
from containerlab_orchestrator.validators import (
    validate_clab_api_url,
    validate_clab_kind,
    validate_clab_memory,
    validate_image_reference,
    validate_json_object,
    validate_string_map,
)


@extras_features("custom_links", "custom_validators", "export_templates", "graphql", "webhooks")
class ContainerlabHost(PrimaryModel):  # pylint: disable=too-many-ancestors
    """A containerlab host that runs clab-api-server."""

    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True)
    remote_url = LaxURLField(
        max_length=500,
        validators=[validate_clab_api_url],
        verbose_name="URL",
        help_text="The clab-api-server URL, for example https://clab01:8080.",
    )
    verify_ssl = models.BooleanField(default=True, verbose_name="Verify SSL")
    secrets_group = models.ForeignKey(
        to="extras.SecretsGroup",
        on_delete=models.PROTECT,
        related_name="containerlab_hosts",
        help_text="The Secrets Group with the HTTP(S) username and password for clab-api-server.",
    )
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True)

    class Meta:
        """Meta attributes for ContainerlabHost."""

        ordering = ["name"]
        verbose_name = "Containerlab host"

    def __str__(self):
        """Return the host name."""
        return self.name

    def clean(self):
        """Reject a Secrets Group that has no HTTP(S) username secret or no HTTP(S) password secret."""
        super().clean()
        if self.secrets_group_id is None:
            return
        present_types = set(
            SecretsGroupAssociation.objects.filter(
                secrets_group_id=self.secrets_group_id,
                access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
                secret_type__in=REQUIRED_HTTP_SECRET_TYPES,
            ).values_list("secret_type", flat=True)
        )
        missing_types = [secret_type for secret_type in REQUIRED_HTTP_SECRET_TYPES if secret_type not in present_types]
        if missing_types and SecretsGroup.objects.filter(pk=self.secrets_group_id).exists():
            missing_secrets = " and an ".join(f"HTTP(S) {secret_type} secret" for secret_type in missing_types)
            raise ValidationError({"secrets_group": f"The Secrets Group must have an {missing_secrets}."})


@extras_features("custom_links", "custom_validators", "export_templates", "graphql", "webhooks")
class NodeMapping(PrimaryModel):  # pylint: disable=too-many-ancestors
    """The containerlab kind, image, and node settings for one Device Type or one Platform."""

    device_type = models.OneToOneField(
        to="dcim.DeviceType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="node_mapping",
    )
    platform = models.OneToOneField(
        to="dcim.Platform",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="node_mapping",
    )
    kind = models.CharField(
        max_length=CHARFIELD_MAX_LENGTH,
        validators=[validate_clab_kind],
        help_text="The containerlab kind, for example ceos or nokia_srlinux.",
    )
    image = models.CharField(
        max_length=CHARFIELD_MAX_LENGTH,
        validators=[validate_image_reference],
        help_text="The container image, for example ceos:4.32.0F.",
    )
    node_type = models.CharField(
        max_length=CHARFIELD_MAX_LENGTH,
        blank=True,
        verbose_name="Type",
        help_text="The containerlab node type, if the kind needs one.",
    )
    env = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Environment variables",
        help_text=(
            "Environment variables for the node. These values are not secret. "
            "The REST API, GraphQL, the change log, and webhooks show them."
        ),
    )
    cpu = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="CPU",
    )
    memory = models.CharField(
        max_length=32,
        blank=True,
        validators=[validate_clab_memory],
        help_text="For example 512mb or 1Gb.",
    )
    overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Containerlab node keys to deep-merge into the node. "
            "WARNING: Some keys, for example binds and exec, run on the containerlab host. "
            "Add only keys that you trust."
        ),
    )
    config_source = models.CharField(
        max_length=50,
        choices=NodeMappingConfigSourceChoices,
        blank=True,
        default="",
        verbose_name="Config source",
        help_text="Leave empty to use the lab default.",
    )
    config_template = models.TextField(
        blank=True,
        verbose_name="Config template",
        help_text="A Jinja2 template for the startup config.",
    )

    natural_key_field_names = ["device_type", "platform"]

    class Meta:
        """Meta attributes for NodeMapping."""

        ordering = ["device_type", "platform"]
        verbose_name = "Node mapping"
        constraints = [
            models.CheckConstraint(
                name="containerlab_orchestrator_nodemapping_not_both",
                condition=models.Q(device_type__isnull=True) | models.Q(platform__isnull=True),
                violation_error_message="Set a Device Type or a Platform, but not both.",
            )
        ]

    def __str__(self):
        """Return the Device Type or the Platform of the mapping."""
        if self.device_type is not None:
            return f"Device Type: {self.device_type.display}"
        if self.platform is not None:
            return f"Platform: {self.platform.name}"
        return "Node mapping"

    def clean(self):
        """Reject a mapping with no target, a bad env or overrides object, and a missing or bad config template.

        The database constraint rejects only both targets, because a MySQL cascade delete first sets a target to NULL.
        The JSON checks are here and not on the fields, because Django skips field validators for an empty value.
        """
        super().clean()
        errors = {}
        if self.device_type_id is None and self.platform_id is None:
            errors[NON_FIELD_ERRORS] = ["Set a Device Type or a Platform."]
        for field_name, validator in (("env", validate_string_map), ("overrides", validate_json_object)):
            try:
                validator(getattr(self, field_name))
            except ValidationError as error:
                errors[field_name] = error.messages
        if self.config_source == NodeMappingConfigSourceChoices.JINJA2 and not self.config_template:
            errors["config_template"] = ["Enter a template when the config source is Jinja2."]
        if self.config_template:
            try:
                validate_jinja2(self.config_template)
            except TemplateSyntaxError as error:
                errors["config_template"] = [f"Template syntax error on line {error.lineno}: {error.message}"]
            except TemplateError as error:
                errors["config_template"] = [f"Template error: {error}"]
        if errors:
            raise ValidationError(errors)
