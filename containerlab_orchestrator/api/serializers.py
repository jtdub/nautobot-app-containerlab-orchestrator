"""REST API serializers for the containerlab_orchestrator app."""

from nautobot.apps.api import NautobotModelSerializer, TaggedModelSerializerMixin

from containerlab_orchestrator.models import ContainerlabHost, NodeMapping


class ContainerlabHostSerializer(TaggedModelSerializerMixin, NautobotModelSerializer):  # pylint: disable=too-many-ancestors
    """Serializer for ContainerlabHost.

    The Secrets Group shows as a reference, never as a secret value.
    """

    class Meta:
        """Meta attributes for ContainerlabHostSerializer."""

        model = ContainerlabHost
        fields = "__all__"


class NodeMappingSerializer(TaggedModelSerializerMixin, NautobotModelSerializer):  # pylint: disable=too-many-ancestors
    """Serializer for NodeMapping."""

    class Meta:
        """Meta attributes for NodeMappingSerializer."""

        model = NodeMapping
        fields = "__all__"
