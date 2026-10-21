# 09.05.26

import re
from urllib.parse import urlparse

GENERIC_LICENSE = "generic"
_PLACEHOLDER = re.compile(r"^://([a-z0-9-]+)$")


def clean_license_url(license_url: str) -> str:
    """The license a key is stored under: scheme://host, nothing else."""
    if not license_url:
        return ""

    parsed = urlparse(license_url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"

    placeholder = _PLACEHOLDER.match(license_url.split("?", 1)[0].split("#", 1)[0].rstrip("/"))
    return placeholder.group(0) if placeholder and placeholder.group(1) != GENERIC_LICENSE else GENERIC_LICENSE
