# 12.10.26

import hashlib
import json
from contextlib import contextmanager

from VibraVid.services._base.login_status import Login
from VibraVid.utils.scope import cache_changes, cache_wanted, changed_sections, scoped, wanted_sections

from .models import RemoteError

MAX_CREDENTIAL_BYTES = 256 * 1024
LOCAL_ONLY_KEYS = ("cdm",)


def _matches(name: str, site: str) -> bool:
    """A login section belongs to *site* when named after it"""
    return name == site or name.startswith(site)


def _unwrap(site_section: dict, name: str) -> dict:
    """Flatten a login section that was nested under its own name (the client pasted it in) into the top level."""
    nested = site_section.get(name)
    if not isinstance(nested, dict):
        return site_section
    flat = {k: v for k, v in site_section.items() if k != name}
    flat.update(nested)
    return flat


def collect_credentials(site: str, login_data: dict, extra=()) -> dict:
    """``{section: {key: value}}`` for each login section of *site* (or in *extra*), without the server's own keys."""
    extra = set(extra or ())
    out: dict = {}
    for name, section in (login_data or {}).items():
        if not (isinstance(name, str) and isinstance(section, dict) and (_matches(name, site) or name in extra)):
            continue
        flat = _unwrap(section, name)
        flat = {k: v for k, v in flat.items() if k not in LOCAL_ONLY_KEYS}
        if flat:
            out[name] = flat
    return out


def apply_updates(login_data: dict, updates: dict) -> bool:
    """Merge sections the server handed back (rotated tokens) into the client's login data. True when anything changed."""
    changed = False
    for name, new in (updates or {}).items():
        if not isinstance(new, dict) or not new:
            continue
        section = login_data.setdefault(name, {})
        target = section[name] if isinstance(section.get(name), dict) else section  # file pasted with an extra nesting level
        for key, value in new.items():
            if target.get(key) != value:
                target[key] = value
                changed = True
    return changed


class LocalCache:
    MAX_FILE_BYTES = 1024 * 1024
    MAX_TOTAL_BYTES = 3 * 1024 * 1024

    @staticmethod
    def _names(service: str) -> list[str]:
        import os

        from VibraVid.utils import disk_cache

        try:
            return sorted(f[:-5] for f in os.listdir(disk_cache._service_dir(service)) if f.endswith(".json"))
        except OSError:
            return []

    def collect(self, services) -> dict:
        """``{service: {name: value}}`` for each of *services* (an empty object when there is nothing, so the server knows it was asked)."""
        from VibraVid.utils import disk_cache

        out: dict = {}
        total = 0
        for service in sorted(set(services)):
            files = {}
            for name in self._names(service):
                value = disk_cache.load(service, name)
                if value is None:
                    continue
                size = len(json.dumps(value))
                if size > self.MAX_FILE_BYTES or total + size > self.MAX_TOTAL_BYTES:
                    continue
                files[name] = value
                total += size
            out[service] = files
        return out

    def apply(self, updates: dict) -> bool:
        """Store what the service wrote on the server (``None`` = delete). True when anything changed."""
        from VibraVid.utils import disk_cache

        changed = False
        for service, files in (updates or {}).items():
            for name, value in (files or {}).items():
                if value is None:
                    disk_cache.invalidate(service, name)
                else:
                    disk_cache.save(service, name, value)
                changed = True
        return changed


class Identity:
    def __init__(self, site: str, scope: str, overlay: dict, server_only: dict, cache: dict | None = None):
        self.site = site
        self.cache = cache if cache is not None else {}
        self.cache_changed: dict = {}
        self.cache_wanted: set = set()
        self.scope = scope
        self.overlay = overlay
        self.server_only = server_only
        self.wanted: set = set()
        self.changed: set = set()
        self.login_events: list = []

    @contextmanager
    def active(self):
        with scoped(self.overlay, self.scope, self.cache), Login.recording(self.login_events):
            try:
                yield
            finally:
                self.wanted |= wanted_sections()
                self.changed |= changed_sections()
                self.cache_wanted |= cache_wanted()
                for service, names in cache_changes().items():
                    self.cache_changed.setdefault(service, {}).update(names)

    def wipe(self) -> None:
        """Drop everything held for the client (credentials, cache files, pending reports)."""
        for container in (self.overlay, self.cache, self.cache_changed, self.server_only):
            container.clear()
        for container in (self.wanted, self.changed, self.cache_wanted):
            container.clear()
        self.login_events.clear()

    def take_login_events(self) -> list:
        events, self.login_events = list(self.login_events), []
        return events

    def take_cache_updates(self) -> dict:
        """Cache files the service wrote (or deleted: ``None``) since the last call, for the client to keep."""
        out, self.cache_changed = self.cache_changed, {}
        return out

    def take_cache_wanted(self) -> list[str]:
        out = sorted(self.cache_wanted - set(self.cache))
        self.cache_wanted.clear()
        return out

    def take_wanted(self) -> list[str]:
        out = sorted(self.wanted - set(self.overlay))
        self.wanted.clear()
        return out

    def take_updates(self) -> dict:
        """The sections rewritten since the last call, without the server's own keys."""
        out = {}
        for name in sorted(self.changed):
            hidden = self.server_only.get(name, ())
            out[name] = {k: v for k, v in (self.overlay.get(name) or {}).items() if k not in hidden}
        self.changed.clear()
        return out


def _validate(site: str, forwarded) -> dict:
    if forwarded in (None, {}):
        return {}
    
    if not isinstance(forwarded, dict):
        raise RemoteError("credentials must be an object of login sections", 400)
    
    try:
        size = len(json.dumps(forwarded))
    except (TypeError, ValueError) as exc:
        raise RemoteError("credentials are not valid JSON data", 400) from exc
    if size > MAX_CREDENTIAL_BYTES:
        raise RemoteError("credentials are too large", 413)
    
    for name, section in forwarded.items():
        if not isinstance(name, str) or not isinstance(section, dict):
            raise RemoteError(f"credentials section '{name}' must be an object", 400)
    return forwarded


MAX_CACHE_BYTES = 4 * 1024 * 1024


def _validate_cache(cache) -> dict:
    if cache in (None, {}):
        return {}
    if not isinstance(cache, dict) or not all(isinstance(v, dict) for v in cache.values()):
        raise RemoteError("cache must be an object of services, each an object of files", 400)
    try:
        size = len(json.dumps(cache))
    except (TypeError, ValueError) as exc:
        raise RemoteError("cache is not valid JSON data", 400) from exc
    if size > MAX_CACHE_BYTES:
        raise RemoteError("cache is too large", 413)
    return cache


def build_identity(site: str, forwarded, server_login: dict, cache=None) -> Identity:
    """The client's own login sections for *site*, plus any extra the server asked for, plus the client's cache files."""
    forwarded = _validate(site, forwarded)

    overlay: dict = {}
    server_only: dict = {}
    for name, section in (server_login or {}).items():
        if isinstance(name, str) and _matches(name, site) and isinstance(section, dict):
            kept = {k: section[k] for k in LOCAL_ONLY_KEYS if k in section}
            overlay[name] = dict(kept)
            server_only[name] = tuple(kept)
    for name, section in forwarded.items():
        overlay[name] = {**section, **overlay.get(name, {})}

    digest = hashlib.sha256(json.dumps(forwarded, sort_keys=True).encode("utf-8")).hexdigest()[:20] if forwarded else "anonymous"
    return Identity(site, digest, overlay, server_only, _validate_cache(cache))
