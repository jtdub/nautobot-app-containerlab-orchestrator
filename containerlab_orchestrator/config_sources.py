"""Give the startup config text of a lab node from its config source."""

from celery.exceptions import SoftTimeLimitExceeded
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

W_SOURCE_NOT_AVAILABLE = (
    "Device {device}: the config source {source} is not available. The node gets no startup config."
)
"""The warning for a Golden Config source, which the app does not read yet."""


def source_label(source):
    """Return the label of the config source, or the value itself if it is not a choice."""
    return NodeMappingConfigSourceChoices.as_dict().get(source, source)


def source_of(mapping, lab):
    """Return the config source of the mapping, else the default of the lab, else none."""
    return mapping.config_source or lab.config_source or NodeMappingConfigSourceChoices.NONE


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


def get_startup_config(device, mapping, interface_map, lab):
    """Return the raw startup config text of the device, or None, and the warnings.

    Args:
        device: The device of the node.
        mapping: The node mapping or the DefaultMapping of the device.
        interface_map: The container name of each physical interface name of the device.
        lab: The lab that gives the default config source.
    """
    source = source_of(mapping, lab)
    if source == NodeMappingConfigSourceChoices.NONE:
        return None, []
    if source in (
        NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED,
        NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP,
    ):
        return None, [W_SOURCE_NOT_AVAILABLE.format(device=device, source=source_label(source))]
    if source != NodeMappingConfigSourceChoices.JINJA2:
        return None, [W_UNKNOWN_SOURCE.format(device=device, source=source)]
    text, warning = _jinja2_text(device, mapping, interface_map, lab)
    if warning:
        return None, [warning]
    if not text.strip():
        return None, [W_EMPTY_CONFIG.format(device=device, source=source_label(source))]
    return text, []
