# 05.09.26

import json
import logging
import uuid
from typing import Any

from VibraVid.services._base.login_status import ACCOUNT, print_login
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

_max_client = None
_API_ROOT = "https://default.any-any.prd.api.hbomax.com"
_BOOTSTRAP_URL = f"{_API_ROOT}/session-context/headwaiter/v1/bootstrap"
_PLAYBACK_URL = "https://default.any-any.prd.api.hbomax.com/any/playback/v1/playbackInfo"
_PLAYREADY_SOAP_ACTION = "http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense"


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

    def _authenticate(self) -> None:
        """Authenticate with the configured ``st`` cookie and bootstrap routing."""
        if not self.access_token:
            raise OSError(
                "HBO Max requires an 'st' cookie in Conf/login.json (section 'hbomax')."
            )

        with create_client(headers=self.base_headers, cookies=self.cookies) as client:
            response = client.post(_BOOTSTRAP_URL)
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
                "browser": {"name": "chrome", "version": "113.0.0.0"},
                "make": "Microsoft",
                "model": "XBOX-Unknown",
                "os": {"name": "Windows", "version": "113.0.0.0"},
                "platform": "XBOX",
                "deviceType": "xbox",
                "player": {
                    "mediaEngine": {"name": "GLUON_BROWSER", "version": "1.20.1"},
                    "playerView": {"height": 2160, "width": 3840},
                    "sdk": {"name": "Beam Player Console", "version": "1.0.2.4"},
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
            logger.error("Max playbackInfo failed: status=%s error=%s", response.status_code, details)
        response.raise_for_status()
        return response.json()

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

    @staticmethod
    def _manifest_url(data: dict) -> str | None:
        fallback = (data.get("fallback") or {}).get("manifest") or {}
        manifest = (data.get("manifest") or {}).get("url") or fallback.get("url")
        if not manifest:
            return None
        return manifest.replace("_fallback", "").replace("fly", "akm").replace("gcp", "akm")

    def get_playback_info(self, edit_id: str) -> dict[str, Any]:
        """Return the DASH manifest and DRM license for ``edit_id``."""
        data = self._playback_info_request(
            edit_id, [{"drmKeySystem": "playready", "maxSecurityLevel": "SL3000"}]
        )
        schemes = self._drm_schemes(data)

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
        return {
            "manifest": self._manifest_url(data),
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
