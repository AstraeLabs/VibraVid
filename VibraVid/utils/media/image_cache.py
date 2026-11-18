# 23.07.26

import logging
import os
import threading
from urllib.parse import urlparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import fetch_image_bytes

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


def _tmdb_cache_path(url: str) -> str | None:
    """Resolve `.cache/images/tmdb/<image_path_basename>` for a TMDB image URL"""
    try:
        name = os.path.basename(urlparse(url).path)
    except Exception:
        return None
    if not name:
        return None
    return os.path.join(config_manager.base_path, ".cache", "images", "tmdb", name)


def get_cached_tmdb_image(url: str | None) -> bytes | None:
    """Return TMDB image bytes for *url*, from disk cache if present, else fetch + persist."""
    if not url:
        return None

    path = _tmdb_cache_path(url)
    if not path:
        return fetch_image_bytes(url)

    with _lock_for(path):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"[image_cache] could not read {path}: {e}")

        data = fetch_image_bytes(url)
        if data:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(data)
            except Exception as e:
                logger.warning(f"[image_cache] could not persist {path}: {e}")
        return data
