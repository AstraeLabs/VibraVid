# 03.07.26

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from curl_cffi import requests

logger = logging.getLogger(__name__)


_AUTHOR = "AstraeLabs"
_TITLE = "VibraVid"

DOMAINS_URL = "https://domains-tracker.server66.workers.dev/get"
VELORA_URL = f"https://raw.githubusercontent.com/{_AUTHOR}/Velora/main/Cargo.toml"
RELEASES_URL = f"https://api.github.com/repos/{_AUTHOR}/{_TITLE}/releases"

_HEADERS = {"User-Agent": "Mozilla/5.0"}

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="startup-prefetch")
_futures: dict[str, Future] = {}


def _fetch_domains():
    response = requests.get(DOMAINS_URL, headers=_HEADERS, timeout=4)
    response.raise_for_status()
    return response.json()


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
