"""Validators for the containerlab_orchestrator app."""

import ipaddress
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from containerlab_orchestrator.clab_client import LAB_NAME_PATTERN

CLAB_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")
IMAGE_REFERENCE_PATTERN = re.compile(r"[!-~]+")
CONTAINER_INTERFACE_NAME_PATTERN = re.compile(r"[!-~]+")
CLAB_MEMORY_PATTERN = re.compile(r"[0-9]+(\.[0-9]+)? ?[kKmMgGtTpPeE][iI]?[bB]?")
"""A number and a go-humanize unit, which containerlab reads with ParseBytes."""
IPV4_CIDR_TEXT_PATTERN = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]+")
IPV4_CIDR_PATTERN = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}/([89]|[12][0-9]|30)")
"""An IPv4 network in CIDR form with a prefix length from 8 to 30 and no leading zero."""
NOT_UNICAST_NETWORK = ipaddress.IPv4Network("0.0.0.0/8")
MIN_SUBNET_PREFIX_LENGTH = 8
"""The shortest prefix that the app accepts for a lab network."""
MAX_SUBNET_PREFIX_LENGTH = 30
"""The longest prefix that leaves room for the gateway and one node."""
CIDR_MESSAGE = "Enter an IPv4 network in CIDR form with no host bits, for example 172.20.21.0/24."
SIZE_MESSAGE = (
    f"Use a network from /{MIN_SUBNET_PREFIX_LENGTH} to /{MAX_SUBNET_PREFIX_LENGTH}, "
    "so that the lab has room for at least one node."
)
UNICAST_MESSAGE = (
    "Use a unicast network. Multicast, loopback, link-local, reserved, and 0.0.0.0/8 networks are not allowed."
)


def validate_clab_api_url(value):
    """Reject a URL that is not http or https, that holds a username or a password, or that has a bad port.

    A URL that `urlsplit` cannot parse passes here, because `EnhancedURLValidator` already reports it.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return
    try:
        parts.port
    except ValueError as error:
        raise ValidationError("Enter a valid URL.") from error
    if parts.scheme not in ("http", "https"):
        raise ValidationError("Use an http:// or https:// URL.")
    if parts.username is not None or parts.password is not None:
        raise ValidationError("Do not put a username or a password in the URL. Use the Secrets Group.")


def validate_json_object(value):
    """Reject a value that is not a JSON object."""
    if not isinstance(value, dict):
        raise ValidationError('Enter a JSON object, for example {"key": "value"}.')


def validate_string_map(value):
    """Reject a value that is not a JSON object with string keys and string values."""
    validate_json_object(value)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValidationError("Each key and each value must be a string.")


def _validate_text_pattern(value, pattern, message):
    """Reject a value that is not text or that does not fully match the pattern."""
    if not isinstance(value, str):
        raise ValidationError("Enter a text value.")
    if not pattern.fullmatch(value):
        raise ValidationError(message)


def validate_clab_kind(value):
    """Reject a containerlab kind that does not match [a-z][a-z0-9_-]*."""
    _validate_text_pattern(
        value, CLAB_KIND_PATTERN, 'Use lowercase letters, digits, "_", and "-", and start with a letter.'
    )


def validate_image_reference(value):
    """Reject an image name that holds a space or a character that is not printable ASCII."""
    _validate_text_pattern(
        value, IMAGE_REFERENCE_PATTERN, "The image name must contain only printable characters and no spaces."
    )


def validate_clab_memory(value):
    """Reject a memory size that is not a number and a unit, for example 512mb or 1Gb."""
    _validate_text_pattern(value, CLAB_MEMORY_PATTERN, "Enter a number and a unit, for example 512mb or 1Gb.")


def validate_clab_lab_name(value):
    """Reject a lab name that clab-api-server does not accept."""
    _validate_text_pattern(value, LAB_NAME_PATTERN, 'Use 1 to 128 letters, digits, "_", or "-".')


def validate_ipv4_network(value):
    """Reject a value that is not a unicast IPv4 network in CIDR form from /8 to /30 with no host bits."""
    if not isinstance(value, str):
        raise ValidationError("Enter a text value.")
    if not IPV4_CIDR_PATTERN.fullmatch(value):
        if IPV4_CIDR_TEXT_PATTERN.fullmatch(value):
            raise ValidationError(SIZE_MESSAGE)
        raise ValidationError(CIDR_MESSAGE)
    try:
        network = ipaddress.IPv4Network(value, strict=True)
    except ValueError as error:
        raise ValidationError(CIDR_MESSAGE) from error
    if (
        network.is_multicast
        or network.is_loopback
        or network.is_link_local
        or network.is_reserved
        or network.subnet_of(NOT_UNICAST_NETWORK)
    ):
        raise ValidationError(UNICAST_MESSAGE)


def subnet_node_room(value):
    """Return the number of node addresses in the subnet, after the network, gateway, and broadcast addresses."""
    return ipaddress.IPv4Network(value).num_addresses - 3


def validate_container_interface_name(value):
    """Reject a container interface name that holds a space or a character that is not printable ASCII."""
    _validate_text_pattern(
        value,
        CONTAINER_INTERFACE_NAME_PATTERN,
        "The container interface name must contain only printable characters and no spaces.",
    )


def validate_trimmed_name(value):
    """Reject a value that is not text or that has whitespace at the start or the end."""
    if not isinstance(value, str):
        raise ValidationError("Enter a text value.")
    if value != value.strip():
        raise ValidationError("Remove the spaces at the start and the end of the name.")
