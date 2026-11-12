"""Validators for the containerlab_orchestrator app."""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError


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
