"""Models for the containerlab_orchestrator app."""

from django.core.exceptions import ValidationError
from django.db import models
from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.models import LaxURLField, PrimaryModel, extras_features

REQUIRED_HTTP_SECRET_TYPES = (
    SecretsGroupSecretTypeChoices.TYPE_USERNAME,
    SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
)
"""Secret types that a host Secrets Group must hold with the HTTP(S) access type."""


@extras_features("custom_links", "custom_validators", "export_templates", "graphql", "webhooks")
class ContainerlabHost(PrimaryModel):
    """A containerlab host that runs clab-api-server."""

    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True)
    remote_url = LaxURLField(
        max_length=500,
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
        """Reject a Secrets Group without an HTTP(S) username secret and an HTTP(S) password secret."""
        super().clean()
        if not self.secrets_group_id:
            return
        present_types = set(
            self.secrets_group.secrets_group_associations.filter(
                access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
                secret_type__in=REQUIRED_HTTP_SECRET_TYPES,
            ).values_list("secret_type", flat=True)
        )
        missing_types = [secret_type for secret_type in REQUIRED_HTTP_SECRET_TYPES if secret_type not in present_types]
        if missing_types:
            missing_secrets = " and an ".join(f"HTTP(S) {secret_type} secret" for secret_type in missing_types)
            raise ValidationError({"secrets_group": f"The Secrets Group must have an {missing_secrets}."})
