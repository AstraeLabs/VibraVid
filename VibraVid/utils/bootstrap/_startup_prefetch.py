# 03.07.26

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from curl_cffi import requests

logger = logging.getLogger(__name__)
DOMAINS_URL = "https://raw.githubusercontent.com/AstraeLabs/Domains/refs/heads/main/domains_s.json"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="startup-prefetch")
_futures: dict[str, Future] = {}


def _fetch_domains():
    logger.info("Fetching domains from GitHub...")
    started = time.perf_counter()
    response = requests.get(DOMAINS_URL, headers=_HEADERS, timeout=4)
    response.raise_for_status()
    data = response.json()
    logger.info("Domains fetched in %d ms.", (time.perf_counter() - started) * 1000)
    return data


def _check_update():
    logger.info("Checking for updates...")
    from VibraVid.utils.upload.update import check_for_updates

    return check_for_updates()


def _warm_vault():
    """Construct the default vault early: its constructor calls warm()."""
    from VibraVid.utils.vault.default import default_vault

    default_vault.warm()
    return True


_JOBS = {
    "domains": _fetch_domains,
}


def start() -> None:
    """Kick off the startup network fetch in the background. Idempotent."""
    for key, func in _JOBS.items():
        if key not in _futures:
            _futures[key] = _executor.submit(func)


def start_update_check() -> None:
    """Kick off the release check in the background (cached 24h). Idempotent, interactive CLI only."""
    if "update" not in _futures:
        _futures["update"] = _executor.submit(_check_update)


def start_vault_warm() -> None:
    """Wake the default vault (Worker + Postgres) while the user picks a title. Idempotent."""
    if "vault" not in _futures:
        _futures["vault"] = _executor.submit(_warm_vault)


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
