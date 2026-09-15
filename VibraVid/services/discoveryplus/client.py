# 22.12.25

import logging
import uuid
from typing import Any

from VibraVid.services._base.login_status import ACCOUNT, ANONYMOUS, print_login
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

_discovery_client = None
cookie_st = config_manager.login.get("discoveryplus", "st")


class DiscoveryPlus:
    def __init__(self, cookies: dict[str, str] | None = None):
        """
        Initialize Discovery Plus client with automatic authentication.
        """
        self.device_id = str(uuid.uuid1())
        self.client_id = "b6746ddc-7bc7-471f-a16c-f6aaf0c34d26"
        accept_language = "en-US,en;q=0.9"

        # Base headers for Android TV client (will be updated after auth)
        self.base_headers = {
            "accept": "*/*",
            "accept-language": accept_language,
            "user-agent": "androidtv dplus/20.8.1.2 (android/9; en-US; SHIELD Android TV-NVIDIA; Build/1)",
            "x-disco-client": "ANDROIDTV:9:dplus:20.8.1.2",
            "x-disco-params": "realm=bolt,bid=dplus,features=ar",
            "x-device-info": f"dplus/20.8.1.2 (NVIDIA/SHIELD Android TV; android/9-mdarcy; {self.device_id}/{self.client_id})",
        }
        self.cookies = cookies or {}
        self.access_token = None
        self.base_url = None
        self.headers = None

        self._authenticate()

    def _authenticate(self):
        """
        Obtain an access token and market‑specific base URL.
        """
        if self.cookies.get("st"):
            self.access_token = self.cookies["st"]
        else:
            token_url = "https://default.any-any.prd.api.discoveryplus.com/token"
            params = {"realm": "bolt", "deviceId": self.device_id}
            with create_client(headers=self.base_headers, cookies=self.cookies) as client:
                response = client.get(token_url, params=params)
            response.raise_for_status()
            data = response.json()
            self.access_token = data["data"]["attributes"]["token"]

        # Step 2: Bootstrap to get market‑specific base_url
        bootstrap_url = "https://default.any-any.prd.api.discoveryplus.com/session-context/headwaiter/v1/bootstrap"
        headers = self.base_headers.copy()
        headers["Authorization"] = f"Bearer {self.access_token}"
        with create_client(headers=headers, cookies=self.cookies) as client:
            response = client.post(bootstrap_url)
        response.raise_for_status()
        config = response.json()

        # Build the market-specific content base_url from the bootstrap's own apiGroups template
        routing = config["routing"]
        try:
            template = config["apiGroups"]["bolt-tenant-homemarket"]["baseUrl"]
            self.base_url = template.format(**routing)
        except (KeyError, TypeError):
            self.base_url = f"https://default.{routing['tenant']}-{routing['homeMarket']}.{routing.get('env', 'prd')}.{routing.get('domain', 'api.discomax.com')}"

        # Final headers for all subsequent requests
        self.headers = self.base_headers.copy()
        self.headers["Authorization"] = f"Bearer {self.access_token}"

        # Announced only now: the resolver needs base_url and headers, which the bootstrap above builds.
        print_login(ACCOUNT if self.cookies.get("st") else ANONYMOUS, resolver=self._account_name)

    def _account_name(self) -> str:
        """Account name behind the ST token, via the tenant's own /users/me."""
        with create_client(headers=self.headers, cookies=self.cookies) as client:
            response = client.get(f"{self.base_url}/users/me")
        response.raise_for_status()
        attributes = (response.json().get("data") or {}).get("attributes") or {}
        # `username` is the login email; firstName/lastName are the display name when it is set.
        return attributes.get("username") or " ".join(
            part for part in (attributes.get("firstName"), attributes.get("lastName")) if part
        )

    def _playback_info_request(self, edit_id: str, cdms: list[dict]) -> dict:
        """One playbackInfo call, requesting only the given DRM system(s)."""
        payload = {
            "appBundle": "com.wbd.stream",
            "applicationSessionId": self.device_id,
            "capabilities": {
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
                                    "height": {"max": 2160, "min": 144},
                                    "width": {"max": 3840, "min": 144},
                                },
                                "maxLevel": "5.1",
                                "profiles": ["main10", "main"],
                            },
                        ],
                        "hdrFormats": ["hdr10", "hdr10plus", "dolbyvision", "dolbyvision5", "dolbyvision8", "hlg"],
                    },
                },
                "contentProtection": {"contentDecryptionModules": cdms},
                "manifests": {"formats": {"dash": {}}},
            },
            "consumptionType": "streaming",
            "deviceInfo": {
                "player": {
                    "mediaEngine": {"name": "", "version": ""},
                    "playerView": {"height": 2160, "width": 3840},
                    "sdk": {"name": "", "version": ""},
                }
            },
            "editId": edit_id,
            "firstPlay": False,
            "gdpr": False,
            "playbackSessionId": str(uuid.uuid4()),
            "userPreferences": {},
        }

        url = "https://default.any-any.prd.api.discoveryplus.com/any/playback/v1/playbackInfo"
        with create_client(headers=self.headers, cookies=self.cookies) as client:
            response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()

    def get_playback_info(self, edit_id: str) -> dict[str, Any]:
        """Get manifest and license URLs for a given edit_id."""
        data = self._playback_info_request(edit_id, [{"drmKeySystem": "playready", "maxSecurityLevel": "SL3000"}])
        schemes = data.get("drm", {}).get("schemes", {}) or data.get("fallback", {}).get("drm", {}).get("schemes", {}) or {}

        if "playready" not in schemes:
            data = self._playback_info_request(
                edit_id, [{"drmKeySystem": "widevine", "maxSecurityLevel": "l3"}, {"drmKeySystem": "clearkey"}]
            )
            schemes = data.get("drm", {}).get("schemes", {}) or data.get("fallback", {}).get("drm", {}).get("schemes", {}) or {}

        # Extract manifest and license
        main_manifest = data.get("manifest", {}).get("url")
        fallback_manifest = data.get("fallback", {}).get("manifest", {}).get("url", "").replace("_fallback", "")
        manifest = main_manifest or fallback_manifest

        drm_type = "playready" if "playready" in schemes else ("widevine" if "widevine" in schemes else None)
        license_url = schemes.get(drm_type, {}).get("licenseUrl") if drm_type else None

        return {"manifest": manifest, "license": license_url, "type": "dash", "license_headers": {}, "drm_type": drm_type}


def get_client():
    """Get or create DiscoveryPlus client instance."""
    global _discovery_client
    if _discovery_client is None:
        cookies = {"st": cookie_st} if cookie_st else None
        _discovery_client = DiscoveryPlus(cookies)
    return _discovery_client
