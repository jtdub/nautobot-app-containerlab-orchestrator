"""REST API serializers for the containerlab_orchestrator app."""

from django.contrib.auth import get_user_model
from nautobot.apps.api import (
    BaseModelSerializer,
    NautobotModelSerializer,
    TaggedModelSerializerMixin,
    ValidatedModelSerializer,
)
from rest_framework import serializers
from rest_framework.utils.field_mapping import get_nested_relation_kwargs

from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, LabNode, NodeMapping


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


class InterfaceMappingSerializer(ValidatedModelSerializer):  # pylint: disable=too-many-ancestors
    """Serializer for InterfaceMapping.

    The model full_clean() checks the unique names, so a duplicate has the same error key `__all__` as the model.
    """

    class Meta:
        """Meta attributes for InterfaceMappingSerializer."""

        model = InterfaceMapping
        fields = "__all__"
        validators = []


class LabOwnerSerializer(BaseModelSerializer):
    """Read-only serializer that shows a lab owner with the username and no other user data."""

    class Meta:
        """Meta attributes for LabOwnerSerializer."""

        model = get_user_model()
        fields = ["id", "object_type", "url", "display", "username"]
        is_nested = True


class LabSerializer(TaggedModelSerializerMixin, NautobotModelSerializer):  # pylint: disable=too-many-ancestors
    """Serializer for Lab.

    The model `clean()` checks the unique name, so a duplicate gets the `name` error key.
    """

    class Meta:
        """Meta attributes for LabSerializer."""

        model = Lab
        fields = "__all__"
        validators = []

    def validate(self, attrs):
        """On create, make the request user the owner and set the Planned status.

        Raises:
            ValidationError: If the request has no signed-in user, as in the CSV import job.
        """
        if self.instance is None:
            user = getattr(self.context.get("request"), "user", None)
            if user is None or not user.is_authenticated:
                raise serializers.ValidationError("Use the add form or the REST API to add a lab.")
            lab = Lab()
            lab.set_create_defaults(user)
            attrs["owner"] = lab.owner
            attrs["status"] = lab.status
        return super().validate(attrs)

    def build_nested_field(self, field_name, relation_info, nested_depth):
        """Show the owner with `LabOwnerSerializer` at each depth from 1, with no other user data."""
        if field_name == "owner":
            return LabOwnerSerializer, get_nested_relation_kwargs(relation_info)
        return super().build_nested_field(field_name, relation_info, nested_depth)


class LabNodeSerializer(BaseModelSerializer):
    """Read-only serializer for LabNode."""

    class Meta:
        """Meta attributes for LabNodeSerializer."""

        model = LabNode
        fields = "__all__"
