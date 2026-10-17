# 12.10.26

import json
import threading
from contextlib import contextmanager

_state = threading.local()
PASSTHROUGH_SECTIONS = frozenset({"Provider"})
PASSTHROUGH_CACHE = frozenset({"_site_loader"})
CACHE_PATH_PREFIX = "remote-cache://"


def current_overlay() -> dict | None:
    """Per-thread login sections that replace ``Conf/login`` while a remote request is served, else ``None``."""
    return getattr(_state, "overlay", None)


def current_scope() -> str:
    """Fingerprint of the credentials in use on this thread ("" outside a remote request)."""
    return getattr(_state, "scope", "") or ""


def note_wanted(section: str) -> None:
    """Record that the service asked for a login section the client did not send (the client is told, and can send it)."""
    wanted = getattr(_state, "wanted", None)
    if wanted is not None:
        wanted.add(section)


def wanted_sections() -> set:
    return set(getattr(_state, "wanted", None) or ())


def mark_changed(section: str) -> None:
    """Record that the service rewrote *section* (e.g. a rotated refresh token): the client must store the new value."""
    changed = getattr(_state, "changed", None)
    if changed is not None:
        changed.add(section)


def changed_sections() -> set:
    return set(getattr(_state, "changed", None) or ())


def cache_active(service: str) -> bool:
    """True when *service*'s cache files are the client's (carried in the request) instead of files on this machine."""
    return getattr(_state, "cache", None) is not None and service not in PASSTHROUGH_CACHE


def _roundtrip(value):
    return json.loads(json.dumps(value))  # what a file would give back: an independent, JSON-clean copy


def cache_get(service: str, name: str):
    cache = _state.cache
    if service not in cache:
        _state.cache_wanted.add(service)  # the client did not send this service's cache: it can, on a retry
        return None
    value = cache[service].get(name)
    return _roundtrip(value) if value is not None else None


def cache_put(service: str, name: str, value) -> None:
    value = _roundtrip(value)
    _state.cache.setdefault(service, {})[name] = value
    _state.cache_changed.setdefault(service, {})[name] = value


def cache_remove(service: str, name: str) -> None:
    if name in _state.cache.get(service, {}):
        del _state.cache[service][name]
        _state.cache_changed.setdefault(service, {})[name] = None


def cache_names(service: str) -> list[str]:
    return list(_state.cache.get(service, {}))


def cache_changes() -> dict:
    return {service: dict(names) for service, names in (getattr(_state, "cache_changed", None) or {}).items()}


def cache_wanted() -> set:
    return set(getattr(_state, "cache_wanted", None) or ())


_FIELDS = ("overlay", "scope", "wanted", "changed", "cache", "cache_changed", "cache_wanted")


@contextmanager
def scoped(overlay: dict, scope: str, cache: dict | None = None):
    """Temporarily set the per-thread overlay, scope, and cache for the duration of a ``with`` block."""
    previous = tuple(getattr(_state, name, None) for name in _FIELDS)
    values = (overlay, scope, set(), set(), cache if cache is not None else {}, {}, set())
    for name, value in zip(_FIELDS, values, strict=True):
        setattr(_state, name, value)
    try:
        yield
    finally:
        for name, value in zip(_FIELDS, previous, strict=True):
            setattr(_state, name, value)
