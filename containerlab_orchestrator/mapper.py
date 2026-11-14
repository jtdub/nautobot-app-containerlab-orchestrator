"""Select the node mapping of a device and give container interface names."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured, ValidationError
from nautobot.apps.config import get_app_settings_or_config
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.models import naturalize_interface

from containerlab_orchestrator.constants import APP_NAME
from containerlab_orchestrator.models import InterfaceMapping, NodeMapping
from containerlab_orchestrator.validators import validate_clab_kind, validate_image_reference

DEFAULT_MAPPING_KEYS = ("kind", "image")
"""The keys that the default_node_mapping setting holds."""

FALLBACK_INTERFACE_PREFIX = "eth"
"""The prefix of a fallback name. eth0 is the management interface, so the numbers start at 1."""


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


def _sort_key(interface):
    """Return the key that puts interfaces in natural order, with a device interface before a module interface."""
    return (
        naturalize_interface(interface.name, CHARFIELD_MAX_LENGTH),
        interface.name,
        interface.module_id is not None,
        str(interface.pk),
    )


def map_interfaces(mapping, interfaces):
    """Return the container name of each interface and a list of warnings.

    Give the interfaces of one device that the caller links. The function does not leave out mgmt_only, virtual,
    or LAG interfaces. An interface that the table does not hold gets the next free ethN name, and a second
    interface with the same name gets no container name.

    Returns:
        A dict of each Interface to its container name, in natural order of the names, and the warnings.

    Raises:
        ValueError: If the interfaces belong to more than one device, or an interface has no device.
    """
    ordered = sorted(interfaces, key=_sort_key)
    if not ordered:
        return {}, []
    devices = {interface.parent for interface in ordered}
    if None in devices or len(devices) > 1:
        raise ValueError("map_interfaces needs the interfaces of one device.")
    (device,) = devices
    table = {row.physical_name: row.container_name for row in mapping.interface_mappings.all()}
    used_names = set(table.values())
    seen_names = set()
    number = 1
    container_names = {}
    warnings = []
    for interface in ordered:
        if interface.name in seen_names:
            warnings.append(
                f"Device {device}, interface {interface.name} (id {interface.pk}): another interface of the device "
                "has the same name. This interface gets no container name."
            )
            continue
        seen_names.add(interface.name)
        if interface.name in table:
            container_names[interface] = table[interface.name]
            continue
        while f"{FALLBACK_INTERFACE_PREFIX}{number}" in used_names:
            number += 1
        fallback = f"{FALLBACK_INTERFACE_PREFIX}{number}"
        used_names.add(fallback)
        container_names[interface] = fallback
        warnings.append(
            f"Device {device}, interface {interface.name}: the interface table has no row. "
            f"The {mapping.kind} node uses {fallback}. Mapping: {mapping}."
        )
    return container_names, warnings
