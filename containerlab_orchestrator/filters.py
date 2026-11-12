"""Filter sets for the containerlab_orchestrator app."""

from nautobot.apps.filters import NaturalKeyOrPKMultipleChoiceFilter, NautobotFilterSet, SearchFilter
from nautobot.extras.models import SecretsGroup

from containerlab_orchestrator.models import ContainerlabHost


class ContainerlabHostFilterSet(NautobotFilterSet):
    """Filter set for ContainerlabHost."""

    q = SearchFilter(
        filter_predicates={
            "name": "icontains",
            "url": "icontains",
            "description": "icontains",
        },
    )
    secrets_group = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=SecretsGroup.objects.all(),
        label="Secrets group (ID or name)",
    )

    class Meta:
        """Meta attributes for ContainerlabHostFilterSet."""

        model = ContainerlabHost
        fields = "__all__"
