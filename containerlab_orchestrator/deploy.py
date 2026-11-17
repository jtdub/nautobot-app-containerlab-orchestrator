"""Deploy the stored topology of a lab to its host, and record the status and the node state."""

from django.core.exceptions import ValidationError

IMAGE_LIST_MESSAGE = "Host {host} returned an image list that the job cannot read."
"""The error for an image list of the host that is not in the expected shape."""

SERVER_TEXT_LIMIT = 500
"""Characters of the server error text that a message keeps."""

DOCKER_REGISTRY = "docker.io/"
"""The registry of an image name that names no registry."""

DOCKER_INDEX = "index.docker.io/"
"""The older name of the Docker Hub registry."""

DOCKER_LIBRARY = "library/"
"""The namespace of an official Docker Hub image."""


def normalize_image(reference):
    """Return the image reference in the form `<registry>/<path>:<tag>` or `<registry>/<path>@<digest>`.

    A reference with a digest drops its tag. A reference with no tag and no digest gets the tag `latest`.
    """
    if reference.startswith(DOCKER_INDEX):
        reference = DOCKER_REGISTRY + reference.removeprefix(DOCKER_INDEX)
    name, _, digest = reference.partition("@")
    tag = "latest"
    colon = name.rfind(":")
    if colon > name.rfind("/"):
        name, tag = name[:colon], name[colon + 1 :]
    first, _, rest = name.partition("/")
    if not rest or ("." not in first and ":" not in first and first != "localhost"):
        name = DOCKER_REGISTRY + name
    path = name.removeprefix(DOCKER_REGISTRY)
    if name.startswith(DOCKER_REGISTRY) and "/" not in path:
        name = DOCKER_REGISTRY + DOCKER_LIBRARY + path
    return f"{name}@{digest}" if digest else f"{name}:{tag}"


def host_images(response, host):
    """Return the normalized tags and digests of the image list of the host.

    Raises:
        ValidationError: If the image list is not in the expected shape.
    """
    images = response.get("images") if isinstance(response, dict) else None
    if not isinstance(images, list) or not all(isinstance(image, dict) for image in images):
        raise ValidationError(IMAGE_LIST_MESSAGE.format(host=host))
    references = []
    for image in images:
        for key in ("repoTags", "repoDigests"):
            values = image.get(key, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValidationError(IMAGE_LIST_MESSAGE.format(host=host))
            references.extend(values)
    return {normalize_image(reference) for reference in references}


def missing_images(nodes, available):
    """Return the images of the topology nodes that are not available, each with its nodes, or an empty string.

    An entry shows the image text of its first node in name order. The entries are sorted by that text.
    """
    groups = {}
    for name in sorted(nodes):
        image = nodes[name]["image"]
        key = normalize_image(image)
        if key not in available:
            groups.setdefault(key, (image, []))[1].append(name)
    return "; ".join(f"{image} ({', '.join(names)})" for image, names in sorted(groups.values()))


def error_text(error):
    """Return the message of the ClabApiError, with the server text at its end cut to SERVER_TEXT_LIMIT."""
    server_text = error.server_message
    if server_text and len(server_text) > SERVER_TEXT_LIMIT and error.message.endswith(server_text):
        return error.message[: -len(server_text)] + server_text[:SERVER_TEXT_LIMIT]
    return error.message
