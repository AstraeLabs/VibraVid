# 03.07.26

import html
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor
from urllib.parse import urlsplit

from curl_cffi import requests

logger = logging.getLogger(__name__)


_AUTHOR = "AstraeLabs"
_TITLE = "VibraVid"

DOMAINS_URL = "https://domains-tracker.server66.workers.dev/get"
CB01_DOMAIN_SOURCE_URL = "https://www.giardiniblog.it/cb01-nuovo-link/"
VELORA_URL = f"https://raw.githubusercontent.com/{_AUTHOR}/Velora/main/Cargo.toml"
RELEASES_URL = f"https://api.github.com/repos/{_AUTHOR}/{_TITLE}/releases"

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_CB01_HEADING_MARKER = 'id="cb01-nuovo-indirizzo-aggiornato"'
_CB01_URL_RE = re.compile(
    r"https://(?:www\\.)?(?:cineblog[0-9a-z-]*|cb01[0-9a-z-]*)\\.[a-z0-9.-]+(?:/[^\\s<\\\"\\\']*)?",
    re.IGNORECASE,
)

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="startup-prefetch")
_futures: dict[str, Future] = {}


def _fetch_domains():
    response = requests.get(DOMAINS_URL, headers=_HEADERS, timeout=4)
    response.raise_for_status()
    return response.json()


def _extract_cb01_domain(page_html: str) -> str | None:
    """Extract the current CB01 origin published in the GiardiniBlog article."""
    decoded = html.unescape(page_html)
    lowered = decoded.lower()
    marker_pos = lowered.find(_CB01_HEADING_MARKER)

    if marker_pos >= 0:
        candidate_area = decoded[marker_pos : marker_pos + 6000]
    else:
        candidate_area = decoded

    for match in _CB01_URL_RE.finditer(candidate_area):
        candidate = match.group(0).rstrip(".,);")
        parsed = urlsplit(candidate)

        if parsed.scheme.lower() != "https" or not parsed.hostname:
            continue

        hostname = parsed.hostname.lower()
        if not (hostname.startswith("cineblog") or hostname.startswith("cb01")):
            continue

        return f"https://{hostname}/"

    return None


def _fetch_cb01_domain() -> str:
    response = requests.get(CB01_DOMAIN_SOURCE_URL, headers=_HEADERS, timeout=4)
    response.raise_for_status()

    domain = _extract_cb01_domain(response.text)
    if not domain:
        raise ValueError("CB01 domain not found in GiardiniBlog article")

    return domain


def _fetch_velora_version():
    response = requests.get(VELORA_URL, headers=_HEADERS, timeout=10)
    response.raise_for_status()
    for line in response.text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _fetch_releases():
    response = requests.get(RELEASES_URL, headers=_HEADERS, timeout=10)
    response.raise_for_status()
    return response.json()


_JOBS = {
    "domains": _fetch_domains,
    "cb01_domain": _fetch_cb01_domain,
    "velora_version": _fetch_velora_version,
    "releases": _fetch_releases,
}


def start() -> None:
    """Kick off startup network checks concurrently. Idempotent."""
    for key, func in _JOBS.items():
        if key not in _futures:
            _futures[key] = _executor.submit(func)


def collect(key: str, timeout: float | None = None):
    """Block for a prefetched result. Returns None if never started or it raised."""
    future = _futures.get(key)
    if future is None:
        return None
    try:
        return future.result(timeout=timeout)
    except Exception as e:
        logger.debug(f"Startup prefetch '{key}' failed: {e}")
        return None
