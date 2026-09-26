# 21.09.26

import base64
import json
import logging
import os
import uuid
import time
from typing import Any

from pyplayready.device import Device as PRDevice
from pywidevine.device import Device as WVDevice
from rich.console import Console

from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import site_constants
from VibraVid.services._base.login_status import ACCOUNT, print_login
from VibraVid.setup import get_prd_path, get_wvd_path
from VibraVid.utils import config_manager

from .sdk import APP_VERSION, CLIENT_ID, ENTITLEMENTS, SDK_VERSION, Sdk

logger = logging.getLogger(__name__)
console = Console()

_client = None
_cookie_st = config_manager.login.get("dsnsp", "token", default=None)
_refresh_token_cfg = config_manager.login.get("dsnsp", "refresh_token", default=None)

SEARCH_URL = "https://disney.api.edge.bamgrid.com/explore/v1.20/search"

SET_IMAX = """mutation updateProfileImaxEnhancedVersion($input: UpdateProfileImaxEnhancedVersionInput!) {
  updateProfileImaxEnhancedVersion(updateProfileImaxEnhancedVersion: $input) { accepted }
}"""

SET_REMASTERED_AR = """mutation updateProfileRemasteredAspectRatio($input: UpdateProfileRemasteredAspectRatioInput!) {
  updateProfileRemasteredAspectRatio(updateProfileRemasteredAspectRatio: $input) { accepted }
}"""

SESSION_STATUS = """query {
  me {
    activeSession { isSubscriber }
    account {
      activeProfile { id }
      profiles { id attributes { playbackSettings { preferImaxEnhancedVersion prefer133 } } }
    }
  }
}"""
SESSION_STATUS_LANGUAGE = """query {
  me {
    account {
      activeProfile { id }
      profiles { id attributes { languagePreferences { appLanguage } } }
    }
  }
}"""


class Client:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.region = "US"
        self.account_tokens = {}
        self.active_session = {}
        self.playback_data = {}

        self.sdk = Sdk(cookies=cookies, on_token_refreshed=_save_session_token)

        opts = getattr(context_tracker, "site_options", None) or {}
        self.prefer_imax = bool(opts.get("imax"))
        self.prefer_remastered_ar = bool(opts.get("remastered_ar"))

        self._authenticate()

    # -- Passthrough properties so existing method bodies below can keep reading
    # self.session/self.access_token/self.refresh_token/self.prod_config unchanged. --
    @property
    def session(self):
        return self.sdk.session

    @property
    def access_token(self):
        return self.sdk.access_token

    @property
    def refresh_token(self):
        return self.sdk.refresh_token

    @property
    def prod_config(self):
        return self.sdk.prod_config

    @property
    def prod_data(self) -> dict:
        """The prod config with its top level `data` wrapper removed."""
        return self.sdk.prod_config.get("data", self.sdk.prod_config) or {}

    def _authenticate(self):
        if not self._restore_cached_session():
            message = (
                "Disney+ token missing or expired. Copy a fresh token from the browser "
            )
            logger.error(message)
            console.print(f"[red]{message}[/red]")
            raise SystemExit(1)
        print_login(ACCOUNT, resolver=self._account_name)
        self.sdk.fetch_prod_config()
        self._apply_playback_preferences()

    @staticmethod
    def _token_expiry(token: str) -> int | None:
        """Read the `exp` claim out of a JWT, or None if it cannot be read."""
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
        except Exception:
            return None

    def _restore_cached_session(self) -> bool:
        """Reuse the browser token stored in login.json.

        The token is used while it is still unexpired. A refresh token is spent
        on a new access token when one was saved; otherwise the caller asks the
        user for a fresh browser token.
        """
        if not _cookie_st:
            return False

        self.sdk.use_session_token(_cookie_st, _refresh_token_cfg)
        expiry = self._token_expiry(_cookie_st)
        if expiry is not None and expiry > time.time() + 60:
            logger.info(f"Account: Using cached session (valid for {(expiry - time.time()) / 3600:.1f}h)")
            return True

        if _refresh_token_cfg and self.sdk.refresh():
            logger.info("Account: Cached session expired, refreshed")
            return True

        logger.info("Account: Cached session expired and could not be refreshed")
        return False

    def _get_session_status(self) -> dict:
        """Fetch the current account/profile session status."""
        status = {
            "is_subscriber": None, "imax": None, "remastered_ar": None, "app_language": None,
        }
        try:
            data = self.sdk.graphql("SessionStatus", SESSION_STATUS)
            me = data.get("data", {}).get("me", {}) or {}
            status["is_subscriber"] = (me.get("activeSession", {}) or {}).get("isSubscriber")
            account = me.get("account", {}) or {}
            active_id = (account.get("activeProfile", {}) or {}).get("id")
            active = next((p for p in account.get("profiles", []) or [] if p.get("id") == active_id), None)
            if active:
                settings = (active.get("attributes", {}) or {}).get("playbackSettings", {}) or {}
                status["imax"] = settings.get("preferImaxEnhancedVersion")
                status["remastered_ar"] = settings.get("prefer133")
        except Exception as e:
            logger.debug(f"Could not fetch session status: {e}")

        try:
            data = self.sdk.graphql("SessionLanguage", SESSION_STATUS_LANGUAGE)
            me = data.get("data", {}).get("me", {}) or {}
            account = me.get("account", {}) or {}
            active_id = (account.get("activeProfile", {}) or {}).get("id")
            active = next((p for p in account.get("profiles", []) or [] if p.get("id") == active_id), None)
            if active:
                status["app_language"] = (active.get("attributes", {}) or {}).get("languagePreferences", {}).get("appLanguage")
        except Exception as e:
            logger.debug(f"Could not fetch app language: {e}")

        return status

    def _apply_playback_preferences(self):
        """Check the current session's playback preferences and update them if they don't match the user's requested options."""
        status = self._get_session_status()

        logger.info("Session:")
        if status["is_subscriber"] is not None:
            logger.info(f" + Subscribed: {status['is_subscriber']}")
        if status["imax"] is not None:
            logger.info(f" + IMAX Enhanced: {status['imax']}")
        if status["remastered_ar"] is not None:
            logger.info(f" + Remastered Aspect Ratio: {not status['remastered_ar']}")
        if status["app_language"] is not None:
            logger.info(f" + App Language: {status['app_language']}")

        if self.prefer_imax and status["imax"] is not True:
            try:
                self._set_playback_preference("updateProfileImaxEnhancedVersion", SET_IMAX, "imaxEnhancedVersion", True)
            except Exception as e:
                logger.warning(f"Could not set IMAX Enhanced preference: {e}")

        if self.prefer_remastered_ar and status["remastered_ar"] is not False:
            try:
                self._set_playback_preference("updateProfileRemasteredAspectRatio", SET_REMASTERED_AR, "remasteredAspectRatio", True)
            except Exception as e:
                logger.warning(f"Could not set Remastered Aspect Ratio preference: {e}")

    def _set_playback_preference(self, operation_name: str, mutation: str, field_name: str, enabled: bool) -> bool:
        data = self.sdk.graphql(
            operation_name=operation_name,
            query=mutation,
            variables={"input": {field_name: enabled}},
        )
        result = data.get("data", {}).get(operation_name, {}) or {}
        accepted = bool(result.get("accepted"))
        if accepted:
            logger.info(f"Updated profile playback preference {field_name}={enabled}")
            new_token = data.get("extensions", {}).get("sdk", {}).get("token", {}).get("accessToken")
            if new_token:
                self.sdk.access_token = new_token
                self.sdk.session.headers.update({"Authorization": f"Bearer {self.sdk.access_token}"})
                _save_session_token(self.sdk.access_token, self.sdk.refresh_token)
        else:
            logger.warning(f"Profile playback preference update not accepted: {field_name}={enabled}")
        return accepted

    def _account_name(self) -> str:
        try:
            info = self._get_account_info()
            profiles = info.get("account", {}).get("profiles", [])
            active = info.get("account", {}).get("activeProfile", {})
            return active.get("name") or profiles[0].get("name", site_constants.SITE_NAME) if profiles else site_constants.SITE_NAME
        except Exception:
            return site_constants.SITE_NAME

    def _get_account_info(self) -> dict:
        data = self.sdk.graphql(
            operation_name="EntitledGraphMeQuery",
            query=ENTITLEMENTS,
            variables={},
        )
        return data.get("data", {}).get("me", {})

    @staticmethod
    def _resolution_for(drm_type: str) -> str:
        """Determine the maximum resolution allowed for the given DRM type based on the CDM's security level."""
        try:
            if drm_type == "wv":
                wvd_path = get_wvd_path()
                if wvd_path and os.path.isfile(wvd_path):
                    level = WVDevice.load(wvd_path).security_level
                    return "3840x2160" if level == 1 else "1280x720"
                return "1280x720"
            else:
                prd_path = get_prd_path()
                if prd_path and os.path.isfile(prd_path):
                    level = PRDevice.load(prd_path).security_level
                    return "3840x2160" if level >= 3000 else "1920x1080"
                return "1920x1080"
        except Exception as e:
            logger.warning(f"Could not determine CDM security level for {drm_type}: {e}")
            return "1280x720" if drm_type == "wv" else "1920x1080"

    def _get_drm_license_url_from_config(self) -> dict[str, dict]:
        try:
            endpoints = self.prod_data.get("services", {}).get("drm", {}).get("client", {}).get("endpoints", {})
            result = {}
            if "widevineLicense" in endpoints:
                url = self._endpoint("drm", "widevineLicense")
                result["widevine"] = {"url": url, "type": DRMType.WIDEVINE}
                logger.info(f"Widevine DRM endpoint: {url}")
            if "playReadyLicense" in endpoints:
                url = self._endpoint("drm", "playReadyLicense")
                result["playready"] = {"url": url, "type": DRMType.PLAYREADY}
                logger.info(f"PlayReady DRM endpoint: {url}")
            return result
        except Exception as e:
            logger.error(f"Could not get DRM license URL from config: {e}")
        return {}

    def _get_license_headers_from_config(self, drm_type: str) -> dict:
        """Get the license request headers for the specified DRM type from the prod_config, replacing any {accessToken} placeholders with the current access token if available."""
        try:
            endpoints = self.prod_data.get("services", {}).get("drm", {}).get("client", {}).get("endpoints", {})
            endpoint_name = "widevineLicense" if drm_type.upper() == "WIDEVINE" else "playReadyLicense"
            endpoint = endpoints.get(endpoint_name, {})
            headers = endpoint.get("headers", {})
            if self.access_token:
                for k, v in headers.items():
                    if isinstance(v, str) and "{accessToken}" in v:
                        headers[k] = v.replace("{accessToken}", self.access_token)
            return headers
        except Exception as e:
            logger.error(f"Could not get DRM license headers from config: {e}")
        return {}

    def _avail_id_from_playback_id(self, media_id: str) -> str:
        """The playbackId is base64 JSON carrying the avail the title belongs to."""
        try:
            padded = media_id + "=" * (-len(media_id) % 4)
            decoded = json.loads(base64.b64decode(padded))
            return decoded.get("availId", "") or ""
        except Exception as e:
            logger.debug(f"Could not decode playbackId: {e}")
            return ""

    def _prime_player_experience(self, media_id: str) -> None:
        """Fetch the player experience for the title's avail before asking for playback.

        Without this the playback service answers with a generic experience context
        and returns the reduced SDR/1080p ladder instead of the full one.
        """
        avail_id = self._avail_id_from_playback_id(media_id)
        if not avail_id:
            logger.debug("No availId in playbackId; skipping playerExperience")
            return
        version = (self.prod_data.get("bamsdk", {}) or {}).get("explore_version") or "v1.18"
        endpoint = f"https://disney.api.edge.bamgrid.com/explore/{version}/playerExperience/{avail_id}"
        try:
            self._request("GET", endpoint, headers={"Accept": "application/json"})
            logger.debug(f"Player experience primed for {avail_id}")
        except Exception as e:
            logger.warning(f"Could not fetch player experience: {e}")

    @staticmethod
    def _uncap_manifest(manifest_url: str) -> str:
        """Drop the query string the playback service appends to the manifest URL.

        That query carries the resolution cap of the playback session
        (`a=3&r=1080&v=1&hash=...`) and the CDN honours it: the same master
        manifest answers with only the renditions up to 1080p. The token in the
        path authorises the resource on its own, so the bare URL still resolves
        and returns the full ladder, VP9 2160p included.
        """
        return manifest_url.split("?", 1)[0] if manifest_url else ""

    def get_playback_info(self, media_id: str, drm_type: str = "pr") -> dict[str, Any]:
        """Get playback information for a given media ID, including manifest URL, license URL, and DRM type."""
        max_resolution = self._resolution_for(drm_type)
        # Order matches the real client: re-declare the device, prime the player
        # experience, then ask for playback.
        self.sdk.update_device_operating_system()
        self._prime_player_experience(media_id)
        endpoint = self._endpoint("media", "mediaPayload", scenario="ctr-high")
        headers = {
            "Accept": "application/vnd.media-service+json",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Application-Version": APP_VERSION,
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Platform": "android/google/tv",
            "X-BAMSDK-Version": SDK_VERSION,
            "X-DSS-Edge-Accept": "vnd.dss.edge+json; version=2",
            "X-DSS-Feature-Filtering": "true",
            "X-Request-Yp-Id": "624b805dafc5c73635b1a216",
        }
        payload = {
            "playbackId": media_id,
            "playback": {
                "attributes": {
                    "codecs": {
                        "supportsMultiCodecMaster": True,
                        "video": ["h.264"]
                        if max_resolution == "1280x720"
                        else ["h.264", "h.265", "vp9"],
                    },
                    "protocol": "HTTPS",
                    "frameRates": [60],
                    "assetInsertionStrategies": {"point": "SGAI", "range": "SGAI"},
                    "playbackInitiationContext": "ONLINE",
                    "slugDuration": "SLUG_500_MS",
                    "maxSlideDuration": "4_HOUR",
                    "resolution": {"max": [max_resolution]},
                    "videoRanges": ["HDR10", "HDR10_PLUS", "DOLBY_VISION"],
                    "audioTypes": ["ATMOS", "DTS_X"],
                },
                "tracking": {"playbackSessionId": str(uuid.uuid4())},
                "preferences": {"preferDtsx": False, "prefer3d": False},
            },
            "allowedCreatives": [
                "VIDEO", "DXC_GATEWAY_GO", "DXC_SHOP",
                "DXC_PAUSE_BASE", "DXC_PAUSE_EXPANDABLE", "DXC_CHOICE",
            ],
            "allowedInsertionVisuals": [
                "PROMO_FULL_TEXT", "PROMO_TEXT", "TITLE_TREATMENT",
                "ON_SCREEN_RATING", "ON_SCREEN_ADVISORY",
            ],
        }
        data = self._request(
            "POST", endpoint, headers=headers, payload=payload,
            exclude_headers=["X-BAMSDK-Platform-Id"],
        )
        stream = data.get("stream", {})
        sources = stream.get("sources", [{}])
        source = sources[0] if sources else {}
        manifest_url = self._uncap_manifest(source.get("complete", {}).get("url", ""))
        logger.info(f"Manifest URL from API: {manifest_url}")

        widevine_drm = source.get("drm", {}).get("widevine", {})
        playready_drm = source.get("drm", {}).get("playready", {})
        logger.debug(f"Source DRM widevine: {widevine_drm}")
        logger.debug(f"Source DRM playready: {playready_drm}")

        widevine_license = widevine_drm.get("licenseUrl", "") if isinstance(widevine_drm, dict) else ""
        playready_license = playready_drm.get("licenseUrl", "") if isinstance(playready_drm, dict) else ""

        widevine_headers = {}
        playready_headers = {}
        if self.access_token:
            if widevine_license:
                widevine_headers["Authorization"] = f"Bearer {self.access_token}"
            if playready_license:
                playready_headers["Authorization"] = f"Bearer {self.access_token}"

        config_drm = self._get_drm_license_url_from_config()

        if not widevine_license and "widevine" in config_drm:
            widevine_license = config_drm["widevine"]["url"]
            widevine_headers = self._get_license_headers_from_config("widevine")
            if self.access_token and "Authorization" not in widevine_headers:
                widevine_headers["Authorization"] = f"Bearer {self.access_token}"
            logger.info(f"Using Widevine license URL from prod_config: {widevine_license}")

        if not playready_license and "playready" in config_drm:
            playready_license = config_drm["playready"]["url"]
            playready_headers = self._get_license_headers_from_config("playready")
            if self.access_token and "Authorization" not in playready_headers:
                playready_headers["Authorization"] = f"Bearer {self.access_token}"
            logger.info(f"Using PlayReady license URL from prod_config: {playready_license}")

        logger.info(
            f"DRM from API response: widevine={'yes' if widevine_license else 'none'}, "
            f"playready={'yes' if playready_license else 'none'}"
        )

        if drm_type == "wv":
            resolved_type, license_url, license_headers = DRMType.WIDEVINE, widevine_license, widevine_headers
        else:
            resolved_type, license_url, license_headers = DRMType.PLAYREADY, playready_license, playready_headers

        if not license_url:
            raise RuntimeError(f"Did not offer a {drm_type.upper()} license for this title")

        logger.info(
            f"Final license_url: {license_url}, drm_type: {resolved_type}, headers: {list(license_headers.keys())}"
        )
        return {
            "manifest": manifest_url,
            "license": license_url,
            "type": "dash",
            "license_headers": license_headers,
            "drm_type": resolved_type,
        }

    def search(self, query: str) -> list[dict]:
        """Search for titles matching the query string. Returns a list of normalized result dicts."""
        headers = {
            "Accept": "application/json",
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Version": SDK_VERSION,
            "X-BAMSDK-Platform": "android/google/tv",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        params = {"query": query}
        try:
            resp = self.session.get(SEARCH_URL, params=params, headers=headers)
            if resp.status_code == 401:
                try:
                    error = resp.json().get("errors", [{}])[0]
                    code = error.get("code", "unauthorized")
                    description = error.get("description", "authentication failed")
                except ValueError:
                    code = "unauthorized"
                    description = resp.reason
                raise ConnectionError(
                    f"Token non valido o scaduto ({code}: {description})."
                )
            resp.raise_for_status()
            data = resp.json()
            results = self._parse_search_results(data)
            logger.info(f"Search: {len(results)} results for '{query}'")
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            if isinstance(e, ConnectionError):
                raise
            return []

    def _parse_search_results(self, data: dict) -> list[dict]:
        """Parse the JSON response from the search API and extract a list of result items."""
        results = []
        page = data.get("data", {}).get("page", {})
        for container in page.get("containers", []):
            for item in container.get("items", []):
                results.append(item)

        if results:
            return results
        items = data.get("data", {}).get("items", [])

        if items:
            return list(items)
        
        items = data.get("data", {}).get("search", {}).get("items", [])
        if items:
            return list(items)
        
        included = data.get("data", {}).get("included", [])
        if included:
            return [e for e in included if e.get("type") in ("movie", "tv", "show", "season", "episode")]
        
        return []

    def refresh_session(self) -> bool:
        """Refresh the session using the stored refresh token, if available."""
        return self.sdk.refresh()

    def _endpoint(self, service: str, endpoint_name: str, **kwargs) -> str:
        return self.sdk.endpoint(service, endpoint_name, **kwargs)

    def _request(self, method: str, endpoint: str, params: dict = None, headers: dict = None, payload: dict = None, exclude_headers: list | None = None) -> Any:
        return self.sdk.request(method, endpoint, params=params, headers=headers, payload=payload, exclude_headers=exclude_headers)


def get_client():
    global _client
    if _client is None:
        # The session is carried by the paired bearer token, not by browser cookies.
        _client = Client()
    return _client


def _save_session_token(token: str, refresh_token: str = None) -> None:
    """Persist the session token (and refresh token, if we have one) for next startup."""
    login_data = config_manager._login_data.setdefault("dsnsp", {})
    changed = login_data.get("token") != token
    if refresh_token and login_data.get("refresh_token") != refresh_token:
        login_data["refresh_token"] = refresh_token
        changed = True
    if not changed:
        return
    login_data["token"] = token
    config_manager.save_login()


def refresh_login_token() -> bool:
    """Use the stored bearer token and persist it after authentication."""
    if not _cookie_st:
        return False
    client = get_client()
    if not client.access_token:
        return False
    _save_session_token(client.access_token)
    return True
