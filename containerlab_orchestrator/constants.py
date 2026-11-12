"""Constants for the containerlab_orchestrator app."""

from nautobot.apps.choices import SecretsGroupSecretTypeChoices

REQUIRED_HTTP_SECRET_TYPES = (
    SecretsGroupSecretTypeChoices.TYPE_USERNAME,
    SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
)
"""Secret types that a host Secrets Group must hold with the HTTP(S) access type."""
