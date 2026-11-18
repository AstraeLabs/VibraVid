# 15.10.26

import logging
import re
import threading
import time
from urllib.parse import urlparse

from VibraVid.utils import disk_cache
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils.vault.base import BaseVault

logger = logging.getLogger(__name__)

GENERIC_SERVICE = "generic"
CUSTOM_PROTOCOL = "custom"
THIRD_PARTY_PROTOCOLS = ("api", "json", "query")
PROTOCOLS = (CUSTOM_PROTOCOL, *THIRD_PARTY_PROTOCOLS)
_CACHE_SERVICE = "vault"
_CACHE_VERSION = 2
_PROBE_KID = "0" * 32
_DEFAULT_TIMEOUT = 10.0
_MAX_MATCHED_SERVICES = 3
_DEFAULT_RETRY_AFTER = 60.0
_DEFAULT_SAVE_KEYS = False
_instances_lock = threading.Lock()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _norm_kid(kid: str) -> str:
    return str(kid).replace("-", "").strip().lower()


def _is_usable_key(key: str) -> bool:
    return bool(key) and not re.fullmatch(r"0+", key)


class _RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


class UniversalVault(BaseVault):
    _profiles: dict[str, dict] = {}
    _profiles_lock = threading.Lock()
    _cooldown_until: dict[str, float] = {}

    def __init__(self, name: str, cfg: dict) -> None:
        super().__init__(name=name)
        self.url = str(cfg.get("url") or "").strip()
        self.token = str(cfg.get("token") or "")
        self.username = str(cfg.get("username") or "")
        self.password = str(cfg.get("password") or "")
        self.forced_protocol = str(cfg.get("protocol") or "").lower() or None
        self.forced_service = str(cfg.get("service") or "") or None
        self.timeout = float(cfg.get("timeout") or _DEFAULT_TIMEOUT)
        self.save_keys = bool(cfg.get("save_keys", _DEFAULT_SAVE_KEYS))
        self.is_third_party = bool(cfg.get("universal"))

        headers = {**get_headers(), "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session = create_client(headers=headers)
        self._lock = threading.Lock()
        self._session_id = None

        if self.forced_protocol and self.forced_protocol not in PROTOCOLS:
            logger.warning(f"[{name}] unknown vault protocol {self.forced_protocol!r} (expected one of {PROTOCOLS}) -- auto-detecting instead")
            self.forced_protocol = None

    @property
    def is_connected(self) -> bool:
        return bool(self.url and (self.token or self.password))

    @property
    def is_writable(self) -> bool:
        return self.save_keys and self.protocol in ("api", "json")

    @property
    def protocol(self) -> str:
        return self._profile().get("protocol") or "api"

    def is_rate_limited(self) -> bool:
        return time.monotonic() < self._cooldown_until.get(self.name, 0.0)

    def _raise_if_rate_limited(self, r) -> None:
        """HTTP 429, or a 200 whose body says rate limited (JSON-RPC status_code 429, API code 2)."""
        data = None
        if r.status_code != 429:
            try:
                data = r.json()
            except ValueError:
                return
            if not (isinstance(data, dict) and (data.get("status_code") == 429 or str(data.get("code")) == "2")):
                return
        else:
            try:
                data = r.json()
            except ValueError:
                data = None

        retry_after = (data or {}).get("retry_after") if isinstance(data, dict) else None
        try:
            retry_after = float(retry_after or r.headers.get("Retry-After") or _DEFAULT_RETRY_AFTER)
        except (TypeError, ValueError):
            retry_after = _DEFAULT_RETRY_AFTER
        raise _RateLimited(retry_after)

    def _cache_name(self) -> str:
        return f"universal_{_norm(self.name)}"

    def _is_profile_current(self, profile) -> bool:
        return (
            isinstance(profile, dict)
            and profile.get("v") == _CACHE_VERSION
            and profile.get("url") == self.url
            and profile.get("forced_protocol") == self.forced_protocol
        )

    def _profile(self) -> dict:
        with self._profiles_lock:
            profile = self._profiles.get(self.name)
            if self._is_profile_current(profile):
                return profile

            cached = disk_cache.load(_CACHE_SERVICE, self._cache_name())
            if self._is_profile_current(cached):
                self._profiles[self.name] = cached
                return cached

            # A failed probe is kept for this process only, so the next run probes again.
            profile = self._probe()
            if profile.get("probed"):
                disk_cache.save(_CACHE_SERVICE, self._cache_name(), profile)
                logger.info(f"[{self.name}] vault profile saved: protocol={profile['protocol']}, services={len(profile['services'] or [])}")
            self._profiles[self.name] = profile
            return profile

    def _is_custom_vault(self) -> bool:
        """True if the vault speaks the default vault's REST contract: POST {url}/get-keys answers with a "keys" list."""
        headers = {"Content-Type": "application/json"}
        with self._lock:
            r = self.session.post(f"{self.url.rstrip('/')}/get-keys", json={"kids": [_PROBE_KID]}, headers=headers, timeout=self.timeout)
        self._raise_if_rate_limited(r)
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except ValueError:
            return False
        return isinstance(data, dict) and isinstance(data.get("keys"), list)

    def _probe(self) -> dict:
        """Detect the protocol (unless forced) and fetch the service list where the vault exposes one."""
        profile = {"v": _CACHE_VERSION, "url": self.url, "forced_protocol": self.forced_protocol, "protocol": self.forced_protocol, "services": None, "probed": True}
        if self.forced_protocol in (CUSTOM_PROTOCOL, "api", "query"):
            return profile
        if not self.forced_protocol and (self.username or self.password) and not self.token:
            profile["protocol"] = "query"
            return profile

        try:
            if not self.forced_protocol and not self.is_third_party and self._is_custom_vault():
                profile["protocol"] = CUSTOM_PROTOCOL
                return profile

            with self._lock:
                r = self.session.post(self.url, json={"method": "GetServices", "params": {}, "token": self.token}, timeout=self.timeout)
            self._raise_if_rate_limited(r)
            data = r.json() if r.content else None
        except ValueError:
            data = None
        except _RateLimited as e:
            self._cooldown_until[self.name] = time.monotonic() + e.retry_after
            logger.warning(f"[{self.name}] vault probe {e} -- using {self.forced_protocol or 'json'} without caching")
            return {**profile, "protocol": self.forced_protocol or "json", "probed": False}
        except Exception as e:
            logger.warning(f"[{self.name}] vault probe failed ({e}) -- using {self.forced_protocol or 'api'} without caching")
            return {**profile, "protocol": self.forced_protocol or "api", "probed": False}

        if not self.forced_protocol:
            profile["protocol"] = "json" if isinstance(data, dict) and r.status_code not in (404, 405) else "api"

        services = data.get("services") if isinstance(data, dict) else None
        if isinstance(data, dict) and isinstance(data.get("message"), dict):
            services = services or data["message"].get("services")
        if isinstance(services, list) and services:
            profile["services"] = [str(s) for s in services if s]
        return profile

    def _context_tokens(self, license_url: str | None) -> set[str]:
        """Words that identify the current platform: the service being downloaded and the license host's labels."""
        from VibraVid.core.ui.tracker import context_tracker

        tokens = {_norm(getattr(context_tracker, "site_name", None) or "")}
        host = urlparse(license_url or "").hostname or ""
        tokens.update(_norm(part) for part in re.split(r"[.\-]", host))
        return {t for t in tokens if len(t) >= 4}

    def _services_for(self, license_url: str | None) -> list[str]:
        """Services to query, best match first. Without a service list only the generic namespace is tried."""
        if self.forced_service:
            return [self.forced_service]

        services = self._profile().get("services")
        if not services:
            return [GENERIC_SERVICE]

        generic = next((s for s in services if _norm(s) == GENERIC_SERVICE), GENERIC_SERVICE)
        tokens = self._context_tokens(license_url)
        matched = [
            s
            for s in services
            if _norm(s) != GENERIC_SERVICE
            and any(t == _norm(s) or (len(t) >= 5 and t in _norm(s)) or (len(_norm(s)) >= 5 and _norm(s) in t) for t in tokens)
        ]
        return matched[:_MAX_MATCHED_SERVICES] + [generic]

    def _lookup_api(self, kid: str, service: str) -> str | None:
        with self._lock:
            r = self.session.get(f"{self.url.rstrip('/')}/{service.lower()}/{kid}", timeout=self.timeout)

        self._raise_if_rate_limited(r)
        if r.status_code == 404:
            return None

        r.raise_for_status()
        data = r.json()
        if int(data.get("code", 0)) != 0:  # 1=auth/listing, 2=rate limit, 3/4=unknown service/kid
            return None

        key = data.get("content_key")
        return key if isinstance(key, str) else None

    def _lookup_json(self, kid: str, service: str) -> str | None:
        params = {"kid": kid, "service": service}
        if self._session_id:
            params["session_id"] = self._session_id

        with self._lock:
            r = self.session.post(self.url, json={"method": "GetKey", "params": params, "token": self.token}, timeout=self.timeout)

        self._raise_if_rate_limited(r)
        if r.status_code == 404:
            return None

        r.raise_for_status()
        data = r.json()
        if data.get("status_code") != 200:
            return None

        message = data.get("message") or {}
        if not isinstance(message, dict):
            return None

        if message.get("session_id"):
            self._session_id = message["session_id"]

        for entry in message.get("keys") or []:
            if isinstance(entry, dict) and _norm_kid(entry.get("kid") or "") == kid:
                return str(entry.get("key") or "")
            if isinstance(entry, str) and ":" in entry and _norm_kid(entry.split(":", 1)[0]) == kid:
                return entry.split(":", 1)[1]
        return None

    def _lookup_query(self, kid: str, service: str) -> str | None:
        params = {"service": service.lower(), "kid": kid, "password": self.password}
        if self.username:
            params["username"] = self.username

        with self._lock:
            r = self.session.get(self.url, params=params, timeout=self.timeout)

        self._raise_if_rate_limited(r)
        r.raise_for_status()
        for entry in r.json().get("keys") or []:
            if isinstance(entry, dict) and _norm_kid(entry.get("kid") or "") == kid:
                return str(entry.get("key") or "")
        return None

    def get_keys_by_kids(self, license_url: str | None, kids: list[str], pssh: str | None = None) -> list[str]:
        if self.is_rate_limited():
            logger.debug(f"[{self.name}] still rate limited -- skipped")
            return []

        lookups = {"api": self._lookup_api, "json": self._lookup_json, "query": self._lookup_query}
        lookup = lookups.get(self.protocol)
        if lookup is None:
            return []

        out: list[str] = []
        for raw in kids or []:
            kid = _norm_kid(raw)
            for service in self._services_for(license_url):
                try:
                    key = lookup(kid, service)
                except _RateLimited as e:
                    self._cooldown_until[self.name] = time.monotonic() + e.retry_after
                    logger.warning(f"[{self.name}] {e} -- vault skipped until then")
                    return out
                except Exception as e:
                    logger.debug(f"[{self.name}] lookup failed for {kid} ({service}): {e}")
                    continue
                if key and _is_usable_key(key):
                    out.append(f"{kid}:{key.lower()}")
                    break
        return out

    def _insert_api(self, kid: str, key: str, service: str) -> bool:
        with self._lock:
            r = self.session.post(f"{self.url.rstrip('/')}/{service.lower()}/{kid}", json={"content_key": key}, timeout=self.timeout)
        self._raise_if_rate_limited(r)
        r.raise_for_status()
        return int(r.json().get("code", 0)) == 0

    def _insert_json(self, kid: str, key: str, service: str) -> bool:
        with self._lock:
            r = self.session.post(self.url, json={"method": "InsertKey", "params": {"kid": kid, "key": key, "service": service}, "token": self.token}, timeout=self.timeout)
        self._raise_if_rate_limited(r)
        r.raise_for_status()
        return r.json().get("status_code") == 200

    def set_keys(self, keys_list: list[str], license_url: str, pssh: str | None = None, kid_to_label: dict | None = None) -> int:
        if not self.is_writable or self.is_rate_limited():
            return 0

        insert = {"api": self._insert_api, "json": self._insert_json}[self.protocol]
        service = self._services_for(license_url)[0]
        added = 0
        for pair in keys_list or []:
            kid, _, key = pair.partition(":")
            if not key:
                continue
            try:
                added += insert(_norm_kid(kid), key.strip().lower(), service)
            except _RateLimited as e:
                self._cooldown_until[self.name] = time.monotonic() + e.retry_after
                logger.warning(f"[{self.name}] {e} -- key upload stopped")
                break
            except Exception as e:
                logger.warning(f"[{self.name}] could not store {kid}: {e}")
        return added


_instances: dict[tuple, UniversalVault] = {}


def build_universal_vault(name: str, cfg: dict, known_urls: set[str] | None = None) -> UniversalVault | None:
    """Build a UniversalVault instance for the given name and config, or return an existing one if already built."""
    cfg = cfg or {}
    url = str(cfg.get("url") or "").rstrip("/").lower()
    if url and url in {u.rstrip("/").lower() for u in (known_urls or set()) if u}:
        logger.info(f"universal vault '{name}' points at an already configured vault -- skipped (no duplicate requests)")
        return None

    ident = (name, *(str(cfg.get(k) or "") for k in ("url", "token", "username", "password", "protocol", "service", "timeout", "save_keys", "universal")))
    with _instances_lock:
        vault = _instances.get(ident)
        if vault is None:
            vault = _instances[ident] = UniversalVault(name, cfg)
        return vault


def resolve_vault_protocol(name: str, cfg: dict) -> str:
    """The DRM.vault entry's protocol: its "protocol" field when valid, else auto-detected once and cached on disk."""
    explicit = str((cfg or {}).get("protocol") or "").lower()
    if explicit in PROTOCOLS:
        return explicit
    return build_universal_vault(name, cfg).protocol
