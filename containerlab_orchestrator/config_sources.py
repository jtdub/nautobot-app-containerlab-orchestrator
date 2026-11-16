"""Give the startup config text of a lab node from its config source."""

import re

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from nautobot.apps.utils import render_jinja2

from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices

AUTOESCAPE_OFF = ("{% autoescape false %}", "{% endautoescape %}")
"""The tags around a config template, so that the render does not HTML-escape a value.

They go on the first and the last line of the template, so the line number in an error does not change.
"""

W_NO_TEMPLATE = (
    "Device {device}: the config source is Jinja2, but {mapping} has no config template. "
    "The node gets no startup config."
)
"""The warning for the Jinja2 source on a mapping with no config template."""

W_TEMPLATE_ERROR = (
    "Device {device}: the config template of {mapping} did not render: {error}. The node gets no startup config."
)
"""The warning for a config template that raises an error."""

W_EMPTY_CONFIG = "Device {device}: the {source} startup config is empty. The node gets no startup config."
"""The warning for a config text with no characters other than spaces."""

W_UNKNOWN_SOURCE = "Device {device}: the config source {source} is not known. The node gets no startup config."
"""The warning for a config source that is not a choice of NodeMappingConfigSourceChoices."""

W_GOLDEN_CONFIG_NOT_INSTALLED = (
    "Device {device}: the config source is {source}, but Golden Config is not installed. "
    "The node gets no startup config."
)
"""The warning for a Golden Config source when the Golden Config app is not installed."""

W_NO_GOLDEN_CONFIG = (
    "Device {device}: the build found no Golden Config record for the device. The node gets no startup config."
)
"""The warning for a device with no Golden Config record that the build user can view."""

W_RENAME_FALLBACK = (
    "Device {device}, interface {interface}: the interface table has no row, so the config keeps the name {interface}."
)
"""The warning for a linked interface that the rename of a Golden Config text does not change."""

GOLDEN_CONFIG_APP = "nautobot_golden_config"
"""The app name of Nautobot Golden Config, which is an optional dependency."""

GOLDEN_CONFIG_FIELDS = {
    NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED: "intended_config",
    NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP: "backup_config",
}
"""The field of the GoldenConfig model that each Golden Config source reads."""

SHORT_FORMS = {
    "GigabitEthernet": "Gi",
    "TenGigabitEthernet": "Te",
    "TenGigE": "Te",
    "FastEthernet": "Fa",
    "Ethernet": "Et",
    "HundredGigabitEthernet": "Hu",
    "HundredGigE": "Hu",
    "Port-channel": "Po",
}
"""The short form of each long interface name prefix that the rename also changes."""

SHORT_FORM_PATTERN = re.compile(rf"({'|'.join(SHORT_FORMS)})(\d.*)")
"""The pattern of a full interface name with a prefix of SHORT_FORMS. Use it with fullmatch."""

GOLDEN_CONFIG_MESSAGE = "Golden Config is not installed, so this config source is not available."
"""The validation error for a new or changed Golden Config source when Golden Config is not installed."""

NAME_BEFORE = r"(?<![A-Za-z0-9_/-])"
"""The lookbehind that stops a match at the end of a longer name."""

NAME_AFTER = r"(?![A-Za-z0-9_/:])"
"""The lookahead that stops a match at the start of a longer name. A subinterface dot can follow."""


def source_label(source):
    """Return the label of the config source, or the value itself if it is not a choice."""
    return NodeMappingConfigSourceChoices.as_dict().get(source, source)


def source_of(mapping, lab):
    """Return the config source of the mapping, else the default of the lab, else none."""
    return mapping.config_source or lab.config_source or NodeMappingConfigSourceChoices.NONE


def golden_config_installed():
    """Return True if the Golden Config app is in the installed apps."""
    return apps.is_installed(GOLDEN_CONFIG_APP)


def config_source_choices():
    """Return the config source choices, without the Golden Config choices when Golden Config is not installed."""
    if golden_config_installed():
        return NodeMappingConfigSourceChoices.CHOICES
    return tuple(choice for choice in NodeMappingConfigSourceChoices.CHOICES if choice[0] not in GOLDEN_CONFIG_FIELDS)


def validate_config_source(value):
    """Raise ValidationError for a Golden Config source when Golden Config is not installed."""
    if value in GOLDEN_CONFIG_FIELDS and not golden_config_installed():
        raise ValidationError(GOLDEN_CONFIG_MESSAGE)


def _golden_config_text(device, source, user):
    """Return the text of the Golden Config field of the source, or None if the build cannot read a record."""
    records = apps.get_model(GOLDEN_CONFIG_APP, "GoldenConfig").objects
    if user is not None:
        records = records.restrict(user, "view")
    return records.filter(device=device).values_list(GOLDEN_CONFIG_FIELDS[source], flat=True).first()


def _short_names(rename_map):
    """Return the new name of each short form of the full names, without a shared short form or a full name."""
    new_names = {}
    for name, new_name in rename_map.items():
        match = SHORT_FORM_PATTERN.fullmatch(name)
        if match:
            new_names.setdefault(f"{SHORT_FORMS[match[1]]}{match[2]}", []).append(new_name)
    return {short: names[0] for short, names in new_names.items() if len(names) == 1 and short not in rename_map}


def rename_interfaces(text, rename_map):
    """Return the text with each full name of the map, and its short form, changed to the new name.

    A name changes only as a whole word, in one pass, with the longest name first. The match is case-sensitive.
    """
    replacements = {**_short_names(rename_map), **rename_map}
    if not replacements:
        return text
    names = sorted(replacements, key=lambda name: (-len(name), name))
    pattern = re.compile(f"{NAME_BEFORE}(?:{'|'.join(map(re.escape, names))}){NAME_AFTER}")
    return pattern.sub(lambda match: replacements[match[0]], text)


def _golden_config_startup_config(device, mapping, interface_map, source, user):
    """Return the renamed Golden Config text of the device, or None, and the warnings."""
    if not golden_config_installed():
        return None, [W_GOLDEN_CONFIG_NOT_INSTALLED.format(device=device, source=source_label(source))]
    text = _golden_config_text(device, source, user)
    if text is None:
        return None, [W_NO_GOLDEN_CONFIG.format(device=device)]
    if not text.strip():
        return None, [W_EMPTY_CONFIG.format(device=device, source=source_label(source))]
    rename_map = {row.physical_name: row.container_name for row in mapping.interface_mappings.all()}
    warnings = [
        W_RENAME_FALLBACK.format(device=device, interface=name)
        for name in sorted(interface_map)
        if name not in rename_map
    ]
    return rename_interfaces(text, rename_map), warnings


def _jinja2_text(device, mapping, interface_map, lab):
    """Return the rendered config template of the mapping and a warning, one of which is None.

    Template code can raise an exception of any type, so each one gives W_TEMPLATE_ERROR. The render runs in a
    savepoint, so a database error in the template does not stop the rest of the build. The Celery soft time limit is
    not caught, so the job stops.
    """
    if not mapping.config_template:
        return None, W_NO_TEMPLATE.format(device=device, mapping=mapping)
    prefix, suffix = AUTOESCAPE_OFF
    context = {"device": device, "lab": lab, "interface_map": dict(interface_map)}
    try:
        with transaction.atomic():
            return render_jinja2(f"{prefix}{mapping.config_template}{suffix}", context), None
    except SoftTimeLimitExceeded:
        raise
    except Exception as error:  # pylint: disable=broad-exception-caught
        return None, W_TEMPLATE_ERROR.format(device=device, mapping=mapping, error=error)


def get_startup_config(device, mapping, interface_map, lab, user=None):
    """Return the raw startup config text of the device, or None, and the warnings.

    Args:
        device: The device of the node.
        mapping: The node mapping or the DefaultMapping of the device.
        interface_map: The container name of each physical interface name of the device.
        lab: The lab that gives the default config source.
        user: The build user, who must be able to view the Golden Config record. With no user, each record is visible.
    """
    source = source_of(mapping, lab)
    if source == NodeMappingConfigSourceChoices.NONE:
        return None, []
    if source in GOLDEN_CONFIG_FIELDS:
        return _golden_config_startup_config(device, mapping, interface_map, source, user)
    if source != NodeMappingConfigSourceChoices.JINJA2:
        return None, [W_UNKNOWN_SOURCE.format(device=device, source=source)]
    text, warning = _jinja2_text(device, mapping, interface_map, lab)
    if warning:
        return None, [warning]
    if not text.strip():
        return None, [W_EMPTY_CONFIG.format(device=device, source=source_label(source))]
    return text, []
