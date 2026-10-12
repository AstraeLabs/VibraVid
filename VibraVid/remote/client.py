# 12.10.26

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .config import RemoteServer
from .credentials import collect_credentials
from .models import RemoteError, ResolvedMedia, unpack_text

logger = logging.getLogger(__name__)

KEY_HEADER = "X-VibraVid-Key"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class RemoteClient:
    def __init__(self, server: RemoteServer, timeout: float = 120.0, login_data: dict | None = None, on_login_update=None, local_cache=None):
        self.server = server
        self.timeout = timeout
        self.login_data = login_data
        self.on_login_update = on_login_update
        self.local_cache = local_cache
        self._extra_sections: dict[str, set] = {}
        self._extra_cache: dict[str, set] = {}
        self._last_login_lines = None

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.server.url}/api/remote/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "User-Agent": "VibraVid-remote"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.server.api_key:
            headers[KEY_HEADER] = self.server.api_key

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            info = self._error_body(exc)
            self._store_updates(info)
            error = RemoteError(str(info.get("error") or f"HTTP {exc.code} {exc.reason}"), exc.code)
            error.wanted = list(info.get("credentials_wanted") or [])
            error.wanted_cache = list(info.get("cache_wanted") or [])
            raise error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteError(f"cannot reach remote server '{self.server.name}' at {self.server.url}: {exc}") from exc

        try:
            answer = json.loads(payload.decode("utf-8")) if payload else {}
        except ValueError as exc:
            raise RemoteError(f"remote server '{self.server.name}' sent a non-JSON answer") from exc
        self._store_updates(answer)
        return answer

    @staticmethod
    def _error_body(exc: urllib.error.HTTPError) -> dict:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def _store_updates(self, answer: dict) -> None:
        events = answer.get("login_status") if isinstance(answer, dict) else None
        if events and events != self._last_login_lines:
            from VibraVid.services._base.login_status import Login

            self._last_login_lines = events
            Login.replay(events)  # same lines, same look as a local run
        cache_update = answer.get("cache_update") if isinstance(answer, dict) else None
        if cache_update and self.local_cache is not None:
            try:
                self.local_cache.apply(cache_update)
            except Exception:
                logger.exception("could not store the cache files returned by the server")
        updates = answer.get("credentials_update") if isinstance(answer, dict) else None
        if updates and self.on_login_update:
            try:
                self.on_login_update(updates)
            except Exception:
                logger.exception("could not store the credentials returned by the server")

    def add_login_sections(self, site: str, names) -> None:
        """Also send these login sections for *site* (hints from the server, or sections a service asked for)."""
        self._extra_sections.setdefault(site, set()).update(names or ())

    def add_cache_services(self, site: str, names) -> None:
        """Also send these cache services for *site* (disk_cache names such as ``cs``, learned the same way)."""
        self._extra_cache.setdefault(site, set()).update(names or ())

    def _cache(self, site: str) -> dict:
        if not self.server.send_credentials or self.local_cache is None:
            return {}
        return self.local_cache.collect({site, *self._extra_cache.get(site, ())})

    def _credentials(self, site: str) -> dict:
        if not self.server.send_credentials or self.login_data is None:
            return {}
        credentials = collect_credentials(site, self.login_data, self._extra_sections.get(site, ()))
        if (credentials or any(self._cache(site).values())) and not self._transport_is_safe():
            host = urllib.parse.urlparse(self.server.url).hostname
            raise RemoteError(
                f"refusing to send your credentials over plain http to '{host}': use https or a VPN, "
                f"or set \"allow_insecure\": true for this server (or \"send_credentials\": false)"
            )
        return credentials

    def _transport_is_safe(self) -> bool:
        parsed = urllib.parse.urlparse(self.server.url)
        return parsed.scheme == "https" or parsed.hostname in LOOPBACK_HOSTS or self.server.allow_insecure

    def _site_call(self, site: str, path: str, body: dict) -> dict:
        """POST *body* with the client's credentials for *site*; retries once with any extra section the service asked for."""
        for attempt in (0, 1):
            credentials = self._credentials(site)
            cache = self._cache(site)
            sent = set(credentials)
            error = None
            try:
                answer = self._call("POST", path, {**body, "site": site, "credentials": credentials, "cache": cache})
                wanted, wanted_cache = answer.get("credentials_wanted") or [], answer.get("cache_wanted") or []
            except RemoteError as exc:
                error, answer, wanted, wanted_cache = exc, {}, getattr(exc, "wanted", []), getattr(exc, "wanted_cache", [])

            available = {name for name in wanted if name not in sent and isinstance(self.login_data, dict) and name in self.login_data}
            available_cache = {name for name in wanted_cache if name not in cache and self.local_cache is not None}
            if (available or available_cache) and attempt == 0 and self.server.send_credentials:
                logger.info(f"{site}: the service also reads {sorted(available | available_cache)}: retrying with them")
                self.add_login_sections(site, available)
                self.add_cache_services(site, available_cache)
                continue
            if error:
                raise error
            return answer
        raise RemoteError("unreachable")

    def health(self) -> bool:
        return self._call("GET", "health/").get("status") == "ok"

    def sites(self) -> dict:
        """``{"sites": [...], "server_cdm": bool}`` for this API key."""
        return self._call("GET", "sites/")

    def site_info(self, site: str) -> dict:
        """The service's own CLI options and the login sections the server has seen it read."""
        info = self._call("GET", f"sites/{urllib.parse.quote(site)}/info/")
        self.add_login_sections(site, info.get("login_sections"))
        self.add_cache_services(site, info.get("cache_services"))
        return info

    def search(self, site: str, query: str, site_options: dict | None = None) -> list[dict]:
        """*query* is a title or, for services that resolve them (``--url``), a title URL."""
        return self._site_call(site, "search/", {"query": query, "site_options": site_options or {}}).get("results", [])

    def seasons(self, site: str, payload: dict, site_options: dict | None = None) -> list[dict]:
        return self._site_call(site, "seasons/", {"payload": payload, "site_options": site_options or {}}).get("seasons", [])

    def resolve(
        self,
        site: str,
        payload: dict,
        season: str | None = None,
        episodes: str | None = None,
        site_options: dict | None = None,
    ) -> tuple[str, list[ResolvedMedia]]:
        data = self._site_call(
            site,
            "resolve/",
            {"payload": payload, "season": season, "episodes": episodes, "site_options": site_options or {}},
        )
        return data["session_id"], [ResolvedMedia.from_wire(item) for item in data.get("items", [])]

    def login_start(self, site: str, site_options: dict | None = None) -> str:
        """Start the service's interactive step (``--login``...) on the server; returns the job id. The account is sent as for any call."""
        credentials = self._credentials(site)
        data = self._call(
            "POST", "login/",
            {"site": site, "site_options": site_options or {}, "credentials": credentials, "cache": self._cache(site)},
        )
        return data["job_id"]

    def login_poll(self, job_id: str, wait: int = 15) -> dict:
        return self._call("GET", f"login/{job_id}/?wait={int(wait)}")

    def login_answer(self, job_id: str, answer: str) -> None:
        self._call("POST", f"login/{job_id}/answer/", {"answer": answer})

    def login_abort(self, job_id: str) -> None:
        try:
            self._call("DELETE", f"login/{job_id}/")
        except RemoteError as exc:
            logger.debug(f"cancelling login {job_id[:6]}... failed: {exc}")

    def license(self, session_id: str, index: int, drm: str, challenge: bytes, pssh_item: dict | None = None) -> bytes | str:
        """Relay *challenge* through the server; returns the raw license (Widevine) or its XML text (PlayReady)."""
        data = self._call(
            "POST",
            f"session/{session_id}/license/",
            {
                "item": index,
                "drm": drm,
                "challenge": base64.b64encode(challenge).decode("ascii"),
                "pssh_item": pssh_item or {},
            },
        )
        if "license_text" in data:
            return data["license_text"]
        return base64.b64decode(data["license"])

    def refresh_manifest(self, session_id: str, index: int) -> str | None:
        """Ask the server to run the service's manifest refresh; returns the new manifest text, if any."""
        return unpack_text(self._call("POST", f"session/{session_id}/refresh/", {"item": index}).get("manifest"))

    def keys(self, session_id: str, index: int) -> list[str]:
        return self._call("POST", f"session/{session_id}/keys/", {"item": index}).get("keys", [])

    def close(self, session_id: str) -> None:
        try:
            self._call("DELETE", f"session/{session_id}/")
        except RemoteError as exc:
            logger.debug(f"closing session {session_id[:6]}... failed: {exc}")


def make_license_request_fn(client: RemoteClient, session_id: str, index: int, default_drm: str | None = None):
    """The ``license_request_fn`` the downloaders call instead of POSTing to the license server themselves."""
    def request(challenge: bytes, item: dict):
        kind = f"{(item or {}).get('type', '')} {default_drm or ''}".lower()
        drm = "PR" if ("playready" in kind or " pr" in f" {kind}") else "WV"
        return client.license(session_id, index, drm, challenge, item)

    return request


def make_manifest_refresh_fn(client: RemoteClient, session_id: str, index: int):
    """The ``manifest_refresh_fn`` for a service whose refresh runs on the server."""
    return lambda: client.refresh_manifest(session_id, index)
