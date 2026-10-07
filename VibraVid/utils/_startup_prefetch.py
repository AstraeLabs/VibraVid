# 03.07.26

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from curl_cffi import requests

logger = logging.getLogger(__name__)


DOMAINS_URL = "https://domains-tracker.server66.workers.dev/get"

_HEADERS = {"User-Agent": "Mozilla/5.0"}

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="startup-prefetch")
_futures: dict[str, Future] = {}


def _fetch_domains():
    response = requests.get(DOMAINS_URL, headers=_HEADERS, timeout=4)
    response.raise_for_status()
    return response.json()


_JOBS = {
    "domains": _fetch_domains,
}


def start() -> None:
    """Kick off the startup network fetch in the background. Idempotent."""
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
