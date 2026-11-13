"""Validators for the containerlab_orchestrator app."""

import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

CLAB_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")
IMAGE_REFERENCE_PATTERN = re.compile(r"[!-~]+")
CLAB_MEMORY_PATTERN = re.compile(r"[0-9]+(\.[0-9]+)? ?[kKmMgGtTpPeE][iI]?[bB]?")
"""A number and a go-humanize unit, which containerlab reads with ParseBytes."""


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
    """Reject a containerlab kind that is not lowercase letters, digits, "_", and "-", or that does not start with a letter."""
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
