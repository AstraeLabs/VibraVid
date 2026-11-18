# 17.07.26

import contextlib
import json
import logging
import os
import threading
import time

from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _locks[path] = lock
        return lock


def _remote(service: str) -> bool:
    """True while serving a remote request: that service's cache is the calling client's, carried in memory (see utils.scope)."""
    from VibraVid.utils.scope import cache_active
    return cache_active(service)


def _service_dir(service: str) -> str:
    return os.path.join(config_manager.base_path, ".cache", "services", service)


def cache_path(service: str, name: str) -> str:
    """Path to a service's disk-persisted cache value (any JSON type)."""
    if _remote(service):
        from VibraVid.utils.scope import CACHE_PATH_PREFIX

        return f"{CACHE_PATH_PREFIX}{service}/{name}.json"
    return os.path.join(_service_dir(service), f"{name}.json")


def load(service: str, name: str):
    """Load a service's disk-persisted cache value (any JSON type). None if missing/corrupt."""
    if _remote(service):
        from VibraVid.utils.scope import cache_get

        return cache_get(service, name)
    path = cache_path(service, name)
    with _lock_for(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None


def save(service: str, name: str, data, ttl: float | None = None) -> None:
    """Persist a service's cache value (any JSON type) to disk, optionally with a TTL in seconds."""
    if ttl is not None and isinstance(data, dict):
        data = {**data, "expiry": time.time() + ttl}

    if _remote(service):
        from VibraVid.utils.scope import cache_put

        try:
            cache_put(service, name, data)
        except (TypeError, ValueError) as e:
            logger.warning(f"[disk_cache] could not keep {service}/{name} for the client: {e}")
        return

    path = cache_path(service, name)
    with _lock_for(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except Exception as e:
            logger.warning(f"[disk_cache] could not persist {service}/{name}: {e}")


def load_fresh(service: str, name: str, expiry_key: str = "expiry", buffer_seconds: float = 0):
    """`load`, but None unless the value is a dict whose `expiry_key` timestamp is still valid (with a safety buffer)."""
    data = load(service, name)
    return data if isinstance(data, dict) and is_fresh(data, expiry_key, buffer_seconds) else None


@contextlib.contextmanager
def lock(service: str, name: str, timeout: float = 45.0, stale_after: float = 90.0, poll: float = 0.1):
    """Exclusive lock across threads and processes for a cache entry, for read-then-write sequences such as a rotating refresh token."""
    if _remote(service):  # one request, one thread, an in-memory cache: nothing to lock
        yield
        return
    
    path = os.path.splitext(cache_path(service, name))[0] + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale_after:
                    os.remove(path)
                    continue
            except OSError:
                continue  # released between the check and the stat/remove: try again
            if time.monotonic() >= deadline:
                raise TimeoutError(f"cache lock {service}/{name} busy for {timeout:.0f}s") from None
            time.sleep(poll)
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def invalidate(service: str, name: str) -> None:
    """Delete a service's cache file (e.g. after it's proven invalid server-side)."""
    if _remote(service):
        from VibraVid.utils.scope import cache_remove

        cache_remove(service, name)
        return
    path = cache_path(service, name)
    with _lock_for(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"[disk_cache] could not remove {service}/{name}: {e}")


def clear_service(service: str) -> int:
    """Delete every cached file for a service (e.g. stale MSL/ESN data after a cookie/account change).

    Returns the number of files removed.
    """
    if _remote(service):
        from VibraVid.utils.scope import cache_names, cache_remove

        names = cache_names(service)
        for name in names:
            cache_remove(service, name)
        return len(names)
    
    service_dir = _service_dir(service)
    removed = 0
    try:
        entries = os.listdir(service_dir)
    except FileNotFoundError:
        return 0
    for entry in entries:
        if not entry.endswith(".json"):
            continue
        path = os.path.join(service_dir, entry)
        with _lock_for(path):
            try:
                os.remove(path)
                removed += 1
            except Exception as e:
                logger.warning(f"[disk_cache] could not remove {service}/{entry}: {e}")
    return removed


def is_fresh(data: dict | None, expiry_key: str = "expiry", buffer_seconds: float = 0) -> bool:
    """True if `data` has a numeric `expiry_key` timestamp still valid (with a safety buffer)."""
    if not data:
        return False
    try:
        return time.time() < (float(data[expiry_key]) - buffer_seconds)
    except (KeyError, TypeError, ValueError):
        return False
