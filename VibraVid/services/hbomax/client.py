# 22.08.26
# By @sync-luca98

import json
import logging
import re
import uuid
from typing import Any

from rich.console import Console

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base.login_status import ACCOUNT, print_login
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)
console = Console()

_max_client = None
_API_ROOT = "https://default.any-any.prd.api.hbomax.com"
_BOOTSTRAP_URL = f"{_API_ROOT}/session-context/headwaiter/v1/bootstrap"
_PLAYBACK_URL = "https://default.any-any.prd.api.hbomax.com/any/playback/v1/playbackInfo"
_PLAYREADY_SOAP_ACTION = "http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense"
_UHD_HEIGHT = 2160
_HEIGHT_ATTR = re.compile(rb'height="(\d+)"')
_PROBE_CHUNK = 3 * 1024 * 1024
_MINIMAL_HEADERS = {
    "x-device-info": "hboMax/hboMax (hboMax/hboMax; hboMax/hboMax; hboMax/hboMax)",
    "x-disco-client": "hboMax:hboMax:hboMax:hboMax",
    "x-disco-params": "hboMax=hboMax",
}

def _probe_enabled() -> bool:
    return bool((context_tracker.site_options or {}).get("probe"))


def _manifest_name(url: str) -> str:
    return url.split("?", 1)[0].rsplit("/", 1)[-1]


def _login_cookies() -> dict[str, str]:
    """Read the Max cookie jar from the Max section of Conf/login.json."""
    configured = config_manager.login.get_section("hbomax", {})
    return {
        str(name): str(value)
        for name, value in configured.items()
        if str(name).lower() in {"st", "session"} and value not in (None, "")
    }


def _session_id(session_cookie: str | None, default: str) -> str:
    """Extract the browser session id used by Max's tracing headers."""
    if not session_cookie:
        return default
    try:
        value = json.loads(session_cookie)
    except (TypeError, json.JSONDecodeError):
        return session_cookie
    if isinstance(value, dict):
        for key in ("deviceId", "device_id", "id", "sessionId"):
            if value.get(key):
                return str(value[key])
    return str(value) if value else default


class Max:
    def __init__(self, cookies: dict[str, str] | None = None):
        """Create a Max client using the cookies supplied in login.json."""
        self.device_id = str(uuid.uuid4())
        self.client_id = "b6746ddc-7bc7-471f-a16c-f6aaf0c34d26"
        self.cookies = dict(cookies or {})
        self.access_token = self.cookies.get("st")
        self.base_url: str | None = None
        self.headers: dict[str, str] = {}
        self._manifest_probe_cache: dict[str, bool] = {}
        self.session_id = _session_id(self.cookies.get("session"), self.device_id)

        self.base_headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "user-agent": "BEAM-Android/1.0.0.104 (SONY/XR-75X95EL)",
            "origin": "https://play.hbomax.com",
            "referer": "https://play.hbomax.com/",
            "x-disco-client": "SAMSUNGTV:124.0.0.0:beam:4.0.0.118",
            "x-disco-params": "realm=bolt,bid=beam,features=ar",
            "x-device-info": (
                f"beam/4.0.0.118 (Samsung/Samsung-Unknown; "
                f"Tizen/124.0.0.0; {self.device_id}/{self.client_id})"
            ),
            "tracestate": f"wbd=session:{self.session_id}",
        }

        self._authenticate()

    @staticmethod
    def _fail_auth() -> None:
        """Report an unusable session and stop without a traceback."""
        message = "HBO Max token missing or expired. Copy a fresh token from the browser"
        logger.error(message)
        console.print(f"[red]{message}[/red]")

    def _authenticate(self) -> None:
        """Authenticate with the configured ``st`` cookie and bootstrap routing."""
        if not self.access_token:
            self._fail_auth()

        with create_client(headers=self.base_headers, cookies=self.cookies) as client:
            response = client.post(_BOOTSTRAP_URL)
        if response.status_code in (400, 401, 403):
            self._fail_auth()
        response.raise_for_status()

        bootstrap = response.json()
        routing = bootstrap.get("routing") or {}
        api_groups = bootstrap.get("apiGroups") or {}
        template = (api_groups.get("bolt-tenant-homemarket") or {}).get("baseUrl")

        if template:
            try:
                self.base_url = template.format(**routing)
            except (KeyError, TypeError, ValueError):
                logger.debug("Could not format Max market URL from bootstrap routing", exc_info=True)

        if not self.base_url:
            tenant = routing.get("tenant", "any")
            market = routing.get("homeMarket", "any")
            environment = routing.get("env", "prd")
            domain = routing.get("domain", "api.hbomax.com")
            self.base_url = f"https://default.{tenant}-{market}.{environment}.{domain}"

        self.headers = dict(self.base_headers)
        session_state = response.headers.get("x-wbd-session-state")
        if session_state:
            self.headers["x-wbd-session-state"] = session_state

        print_login(ACCOUNT, resolver=self._account_name)

    def _account_name(self) -> str:
        """Resolve the account name without putting credentials in source code."""
        with create_client(headers=self.headers, cookies=self.cookies) as client:
            response = client.get(f"{self.base_url}/users/me")
        response.raise_for_status()
        attributes = (response.json().get("data") or {}).get("attributes") or {}
        return attributes.get("username") or " ".join(
            part for part in (attributes.get("firstName"), attributes.get("lastName")) if part
        )

    @staticmethod
    def _capabilities(cdms: list[dict]) -> dict:
        """Capabilities used by the Beam playbackInfo endpoint."""
        return {
            "codecs": {
                "audio": {
                    "decoders": [
                        {"codec": "aac", "profiles": ["lc", "he", "hev2", "xhe"]},
                        {"codec": "eac3", "profiles": ["atmos"]},
                    ]
                },
                "video": {
                    "decoders": [
                        {
                            "codec": "h264",
                            "levelConstraints": {
                                "framerate": {"max": 60, "min": 0},
                                "height": {"max": 2160, "min": 48},
                                "width": {"max": 3840, "min": 48},
                            },
                            "maxLevel": "5.2",
                            "profiles": ["baseline", "main", "high"],
                        },
                        {
                            "codec": "h265",
                            "levelConstraints": {
                                "framerate": {"max": 60, "min": 0},
                                "height": {"max": 2160, "min": 1080},
                                "width": {"max": 3840, "min": 1920},
                            },
                            "maxLevel": "6.2",
                            "profiles": ["main10", "main"],
                        },
                    ],
                    "hdrFormats": [
                        "hdr10",
                        "hdr10plus",
                        "dolbyvision",
                        "dolbyvision5",
                        "dolbyvision8",
                        "hlg",
                    ],
                },
            },
            "contentProtection": {"contentDecryptionModules": cdms},
            "devicePlatform": {
                "network": {
                    "lastKnownStatus": {"networkTransportType": "unknown"},
                    "capabilities": {"protocols": {"http": {"byteRangeRequests": True}}},
                },
                "videoSink": {
                    "lastKnownStatus": {"width": 3840, "height": 2160},
                    "capabilities": {
                        "colorGamuts": ["standard", "wide"],
                        "hdrFormats": ["dolbyvision", "hdr10plus", "hdr10", "hlg"],
                    },
                },
            },
            "manifests": {"formats": {"dash": {}}},
        }

    def _playback_info_request(self, edit_id: str, cdms: list[dict]) -> dict:
        """Call playbackInfo with the requested DRM systems."""
        payload = {
            "appBundle": "beam",
            "applicationSessionId": str(uuid.uuid4()),
            "consumptionType": "streaming",
            "deviceInfo": {
                "deviceId": self.device_id,
                "make": "Samsung",
                "model": "Samsung-UHD-TV",
                "os": {"name": "Tizen", "version": "124.0.0.0"},
                "platform": "SAMSUNGTV",
                "deviceType": "tv",
                "player": {
                    "mediaEngine": {"name": "BEAM", "version": "4.0.0.118"},
                    "playerView": {"height": 2160, "width": 3840},
                    "sdk": {"name": "beam", "version": "4.0.0.118"},
                },
            },
            "editId": edit_id,
            "capabilities": self._capabilities(cdms),
            "gdpr": False,
            "firstPlay": False,
            "playbackSessionId": str(uuid.uuid4()),
            "userPreferences": {},
            "features": [],
        }

        with create_client(headers=self.headers, cookies=self.cookies) as client:
            response = client.post(_PLAYBACK_URL, json=payload)
        if not response.ok:
            try:
                details = response.json()
            except ValueError:
                details = response.text[:500]
            logger.error(f"Max playbackInfo failed: status={response.status_code} error={details}")
        response.raise_for_status()
        return response.json()

    def _playback_info_minimal(self, edit_id: str, cdms: list[dict]) -> dict:
        """Second playbackInfo probe using rosso's deliberately thin device declaration."""
        payload = {
            "appBundle": "",
            "applicationSessionId": "",
            "consumptionType": "streaming",
            "playbackSessionId": "",
            "deviceInfo": {
                "player": {
                    "mediaEngine": {"name": "", "version": ""},
                    "playerView": {"height": 0, "width": 0},
                    "sdk": {"name": "", "version": ""},
                },
            },
            "editId": edit_id,
            "capabilities": {
                "contentProtection": {"contentDecryptionModules": cdms},
                "manifests": {"formats": {"dash": {}}},
            },
            "gdpr": False,
            "firstPlay": False,
            "userPreferences": {},
        }
        headers = dict(self.headers)
        headers.update(_MINIMAL_HEADERS)
        with create_client(headers=headers, cookies=self.cookies) as client:
            response = client.post(_PLAYBACK_URL, json=payload)
        response.raise_for_status()
        return response.json()

    def _compare_declarations(self, edit_id: str, cdms: list[dict], full: dict) -> None:
        """Measure the ladder for the full and the thin device declaration side by side."""
        for label, data in (("full", full), ("minimal", None)):
            if data is None:
                try:
                    data = self._playback_info_minimal(edit_id, cdms)
                except Exception as exc:
                    logger.warning(f"Max minimal declaration probe failed: {exc}")
                    return
            for url in self._manifest_candidates(data):
                height = self._probe_manifest(url)
                logger.info(f"Max ladder [{label}] {_manifest_name(url)}: {height or 'unknown'}")

    def _license_headers(self, drm_type: str | None) -> dict[str, str]:
        """Headers needed by downloader DRM requests, including configured cookies."""
        headers = dict(self.headers)
        if drm_type == "playready":
            headers["SOAPAction"] = _PLAYREADY_SOAP_ACTION
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        return headers

    @staticmethod
    def _drm_schemes(data: dict) -> dict:
        drm = data.get("drm") or (data.get("fallback") or {}).get("drm") or {}
        return drm.get("schemes") or {}

    @classmethod
    def _manifest_candidates(cls, data: dict) -> list[str]:
        """Manifest URLs in priority order, already rewritten to the akm CDN."""
        fallback = (data.get("fallback") or {}).get("manifest") or {}
        ordered: list[str] = []
        for url in (fallback.get("url"), (data.get("manifest") or {}).get("url")):
            if not url:
                continue
            cleaned = url.replace("_fallback", "").replace("fly", "akm").replace("gcp", "akm")
            if cleaned not in ordered:
                ordered.append(cleaned)
        return ordered

    @classmethod
    def _manifest_url(cls, data: dict) -> str | None:
        candidates = cls._manifest_candidates(data)
        return candidates[0] if candidates else None

    def _serves_manifest(self, url: str) -> bool:
        """Confirm a candidate URL actually answers with a DASH manifest."""
        if url in self._manifest_probe_cache:
            return self._manifest_probe_cache[url]
        try:
            with create_client(headers=self.headers, cookies=self.cookies) as client:
                response = client.get(url, stream=True)
                ok = response.ok
                head = next(response.iter_content(chunk_size=256), b"") if ok else b""
        except Exception as exc:
            logger.debug(f"Max manifest probe failed for {url}: {exc}")
            ok, head = False, b""
        result = bool(ok and b"<" in head)
        self._manifest_probe_cache[url] = result
        return result

    def _resolve_manifest(self, candidates: list[str]) -> str | None:
        """Pick the first candidate that serves a manifest, preferring the uncapped one."""
        for candidate in candidates:
            if self._serves_manifest(candidate):
                if candidate != candidates[0]:
                    logger.warning(f"Max uncapped manifest unavailable, falling back to the capped URL: {candidate}")
                return candidate
        return candidates[0] if candidates else None

    def _probe_manifest(self, url: str) -> int | None:
        """Best-effort read of the tallest rendition advertised by a DASH manifest."""
        try:
            with create_client(headers=self.headers, cookies=self.cookies) as client:
                response = client.get(url, stream=True)
                if not response.ok:
                    logger.warning(f"Max probe: {url} -> HTTP {response.status_code}")
                    return None
                buffer = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) >= _PROBE_CHUNK:
                        break
        except Exception as exc:
            logger.warning(f"Max probe failed for {url}: {exc}")
            return None

        heights = [int(value) for value in _HEIGHT_ATTR.findall(bytes(buffer))]
        return max(heights) if heights else None

    def _report_uhd(self, edit_id: str, candidates: list[str], chosen: str | None) -> None:
        """Log the resolution ladder behind every candidate manifest URL."""
        best = 0
        for url in candidates:
            height = self._probe_manifest(url)
            mark = " <- in use" if url == chosen else ""
            if height is None:
                logger.info(f"Max probe {_manifest_name(url)}: ladder unknown{mark}")
            else:
                best = max(best, height)
                level = "UHD" if height >= _UHD_HEIGHT else "no UHD"
                logger.info(f"Max probe {_manifest_name(url)}: tallest {height}p {level}{mark}")

        if best >= _UHD_HEIGHT:
            logger.info(f"Max editId={edit_id}: best ladder {best}p UHD, using {_manifest_name(chosen or '')}")
        else:
            logger.warning(f"Max editId={edit_id}: no candidate advertised {_UHD_HEIGHT}p, best was {best}p")

    def get_playback_info(self, edit_id: str) -> dict[str, Any]:
        """Return the DASH manifest and DRM license for ``edit_id``."""
        data = self._playback_info_request(
            edit_id, [{"drmKeySystem": "playready", "maxSecurityLevel": "SL3000"}]
        )
        schemes = self._drm_schemes(data)

        if "playready" not in schemes:
            playready_cdms = [{"drmKeySystem": "playready", "maxSecurityLevel": "SL3000"}]
            try:
                minimal = self._playback_info_minimal(edit_id, playready_cdms)
            except Exception as exc:
                logger.warning(f"Max minimal-declaration PlayReady retry failed: {exc}")
                minimal = None
            
            if minimal is not None and "playready" in self._drm_schemes(minimal):
                logger.info("Max PlayReady scheme obtained via the minimal device declaration")
                data = minimal
                schemes = self._drm_schemes(minimal)

        if "playready" not in schemes:
            data = self._playback_info_request(
                edit_id,
                [
                    {"drmKeySystem": "widevine", "maxSecurityLevel": "l3"},
                    {"drmKeySystem": "clearkey"},
                ],
            )
            schemes = self._drm_schemes(data)

        drm_type = "playready" if "playready" in schemes else "widevine" if "widevine" in schemes else None
        license_url = (schemes.get(drm_type) or {}).get("licenseUrl") if drm_type else None
        candidates = self._manifest_candidates(data)
        manifest = self._resolve_manifest(candidates)
        if _probe_enabled():
            self._report_uhd(edit_id, candidates, manifest)
            cdms = (
                [{"drmKeySystem": "playready", "maxSecurityLevel": "SL3000"}]
                if drm_type == "playready"
                else [{"drmKeySystem": "widevine", "maxSecurityLevel": "l3"}, {"drmKeySystem": "clearkey"}]
            )
            self._compare_declarations(edit_id, cdms, data)
        
        return {
            "manifest": manifest,
            "license": license_url,
            "type": "dash",
            "license_headers": self._license_headers(drm_type),
            "drm_type": drm_type,
        }


def get_client():
    """Return the process-wide HBO Max client configured from Conf/login.json."""
    global _max_client
    if _max_client is None:
        _max_client = Max(_login_cookies())
    return _max_client
