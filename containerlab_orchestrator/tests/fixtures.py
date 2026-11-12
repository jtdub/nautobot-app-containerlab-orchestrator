"""Test data helpers for the containerlab_orchestrator tests."""

from nautobot.apps.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import Secret, SecretsGroup, SecretsGroupAssociation

from containerlab_orchestrator.models import ContainerlabHost


def create_http_secrets_group(name, username=True, password=True):
    """Return a new Secrets Group with the requested HTTP(S) username and password secrets.

    Each secret reads an environment variable, so the tests never store a secret value.
    """
    secrets_group = SecretsGroup.objects.create(name=name)
    wanted_types = []
    if username:
        wanted_types.append(SecretsGroupSecretTypeChoices.TYPE_USERNAME)
    if password:
        wanted_types.append(SecretsGroupSecretTypeChoices.TYPE_PASSWORD)
    for secret_type in wanted_types:
        secret = Secret.objects.create(
            name=f"{name} {secret_type}",
            provider="environment-variable",
            parameters={"variable": f"CLAB_{secret_type.upper()}"},
        )
        SecretsGroupAssociation.objects.create(
            secrets_group=secrets_group,
            secret=secret,
            access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
            secret_type=secret_type,
        )
    return secrets_group


def create_containerlab_hosts():
    """Return three saved hosts, each with its own complete Secrets Group."""
    hosts = []
    for number in range(1, 4):
        hosts.append(
            ContainerlabHost.objects.create(
                name=f"clab{number:02d}",
                url=f"https://clab{number:02d}.example.com:8080",
                verify_ssl=number != 3,
                secrets_group=create_http_secrets_group(f"Containerlab credentials {number}"),
                description=f"Lab server {number}",
            )
        )
    return hosts
