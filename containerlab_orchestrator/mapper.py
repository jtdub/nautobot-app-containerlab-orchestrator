"""Select the node mapping of a device and give container interface names."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured, ValidationError
from nautobot.apps.config import get_app_settings_or_config

from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.models import InterfaceMapping, NodeMapping
from containerlab_orchestrator.validators import validate_clab_kind, validate_image_reference

DEFAULT_MAPPING_KEYS = ("kind", "image")
"""The keys that the default_node_mapping setting holds."""


@dataclass(frozen=True, eq=False)
class DefaultMapping:  # pylint: disable=too-many-instance-attributes
    """The node settings for a device that no node mapping matches.

    It has the node attributes of `NodeMapping` and `interface_mappings`, but no `pk`, `_meta`, or
    `get_absolute_url`. A caller that needs those checks `isinstance(mapping, DefaultMapping)`.
    """

    kind: str
    image: str
    node_type: str = ""
    env: dict = field(default_factory=dict)
    cpu: Decimal | None = None
    memory: str = ""
    overrides: dict = field(default_factory=dict)
    config_source: str = ""
    config_template: str = ""
    device_type: None = None
    platform: None = None

    @property
    def interface_mappings(self):
        """Return an empty queryset of interface rows."""
        return InterfaceMapping.objects.none()

    def __str__(self):
        """Return the text that names the default mapping."""
        return "Default mapping"


def _default_mapping():
    """Return a new DefaultMapping from the default_node_mapping setting.

    Raises:
        ImproperlyConfigured: If the setting is not a dict with a valid kind and image and no other key.
    """
    setting = get_app_settings_or_config(APP_NAME, "default_node_mapping")
    if not isinstance(setting, dict) or any(key not in setting for key in DEFAULT_MAPPING_KEYS):
        raise ImproperlyConfigured("The default_node_mapping setting must be a dict with a kind and an image.")
    for key, validator in (("kind", validate_clab_kind), ("image", validate_image_reference)):
        try:
            validator(setting[key])
        except ValidationError as error:
            message = " ".join(error.messages)
            raise ImproperlyConfigured(f"The default_node_mapping setting is not valid: {key}: {message}") from error
    unknown_keys = sorted(str(key) for key in setting if key not in DEFAULT_MAPPING_KEYS)
    if unknown_keys:
        raise ImproperlyConfigured(f"The default_node_mapping setting has an unknown key: {', '.join(unknown_keys)}.")
    return DefaultMapping(kind=setting["kind"], image=setting["image"])


def resolve_mapping(device):
    """Return the Device Type mapping of the device, else its Platform mapping, else a DefaultMapping.

    Raises:
        ImproperlyConfigured: If no mapping matches and the default_node_mapping setting is not valid.
    """
    mappings = NodeMapping.objects.select_related("device_type__manufacturer", "platform").prefetch_related(
        "interface_mappings"
    )
    mapping = None
    if device.device_type_id is not None:
        mapping = mappings.filter(device_type_id=device.device_type_id).first()
    if mapping is None and device.platform_id is not None:
        mapping = mappings.filter(platform_id=device.platform_id).first()
    return mapping or _default_mapping()
