# 13.10.26
# By @Syncs-Luca98

import base64
import json
import logging
import re
import time
from urllib.parse import quote

from rich.console import Console

from VibraVid.services._base.login_status import Login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)
console = Console()

TIMVISION_BASE = "https://timvisiontv.tim.it"
TIMVISION_WWW = "https://www.timvision.it"
TIMVISION_HODOR_API = "https://hodor.prod.front.tim.cptech.pro/api/v2/timvision"
TIMVISION_PLAYER_CONFIG = "https://player.timvision.it/one/configs/v2/14/mycanaltim/prod.json"
TIMVISION_HAPI = "https://hapi-p4.prod.catalog.tim.cptech.pro"

TIMVISION_DOMAINS = [".tim.it", ".timvision.it", ".cptech.pro", ".prod.front.tim.cptech.pro"]

_CONFIG_TTL = 24 * 3600


def _load_login() -> dict:
    """Load credentials from login"""
    try:
        return config_manager.login.get_cookies("timvision")
    except Exception:
        return {}


class TimvisionClient:
    _login_shown = False

    def __init__(self):
        self.cookies: dict = {}
        self.token = ""
        self._session = None
        self._load_credentials()
        self._session = create_client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                "Referer": TIMVISION_BASE + "/",
                "Origin": TIMVISION_BASE,
            }
        )
        self._apply_cookies()
        self._authenticate()
        if self.cookies and not TimvisionClient._login_shown:
            TimvisionClient._login_shown = True
            Login.show(Login.ACCOUNT)

    def _load_credentials(self):
        self.cookies = _load_login()
        cookie_token = self.cookies.get("tokenCMS", "")
        self.token = cookie_token if re.fullmatch(r"[0-9a-fA-F]{32}", cookie_token) else ""

    def _apply_cookies(self):
        """Set cookies on the session for all TIMvision domains."""
        if not self.cookies:
            return
        for name, value in self.cookies.items():
            for domain in TIMVISION_DOMAINS:
                self._session.cookies.set(name, value, domain=domain, path="/")

    def _authenticate(self) -> bool:
        """Get authentication token from hodor API."""
        cookie_token = self.cookies.get("tokenCMS", "")
        if re.fullmatch(r"[0-9a-fA-F]{32}", cookie_token):
            self.token = cookie_token
            return True
        if not self.cookies:
            return False
        try:
            resp = self._session.get(
                f"{TIMVISION_HODOR_API}/authenticate.json/webapp/6.0",
                params={"experiments": "beta-test-one-tv-guide:control"},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("token", "")
                if self.token:
                    logger.debug("TIMvision Hodor session initialized")
                    return True
            logger.warning(f"TIMvision auth failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"TIMvision auth error: {e}")
        return False

    def is_authenticated(self) -> bool:
        if self.token:
            return True
        if self.cookies:
            self._session.headers["Authorization"] = ""
            return True
        return False

    def login(self) -> bool:
        """Login with the browser cookies from login"""
        if self.cookies:
            return True
        
        console.print("[red]TIMvision: no credentials available")
        return False

    def _auth_headers(self) -> dict:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # ------------------------------------------------------------------
    # Player config (pass endpoints, hapi endpoints)
    # ------------------------------------------------------------------
    def _load_config(self) -> dict:
        cached = disk_cache.load_fresh("timvision", "player_config")
        if cached:
            return cached.get("config", {})
        
        try:
            resp = self._session.get(TIMVISION_PLAYER_CONFIG, timeout=30)
            if resp.status_code == 200:
                config = resp.json()
                disk_cache.save("timvision", "player_config", {"config": config}, ttl=_CONFIG_TTL)
                return config
            logger.warning(f"TIMvision config fetch failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"TIMvision config error: {e}")

        stale = disk_cache.load("timvision", "player_config")
        return stale.get("config", {}) if isinstance(stale, dict) else {}

    # ------------------------------------------------------------------
    # Pass token (required by the hapi stream API)
    # ------------------------------------------------------------------
    def get_pass_token(self) -> str:
        cached = disk_cache.load_fresh("timvision", "pass_token", buffer_seconds=300)
        if cached and cached.get("token"):
            Login.refresh_cached(disk_cache.cache_path("timvision", "pass_token"))
            return cached["token"]

        config = self._load_config()
        pass_cfg = config.get("pass", {})
        url = pass_cfg.get("url")
        portail_id = pass_cfg.get("portailId")
        media = pass_cfg.get("media", "PLAYER")
        vect = pass_cfg.get("vect", "INTERNET")
        pass_id = self.cookies.get("p_pass_token", "")
        if not url or not portail_id or not pass_id:
            logger.error("TIMvision: missing pass config or p_pass_token cookie")
            return ""

        data = {
            "portailId": portail_id,
            "media": media,
            "vect": vect,
            "passIdType": "pass",
            "noCache": "false",
            "passId": pass_id,
        }
        Login.refresh_try()
        try:
            resp = self._session.post(
                url,
                data=data,
                headers={
                    "accept": "*/*",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": TIMVISION_WWW,
                    "referer": TIMVISION_WWW + "/",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                result = resp.json()
                response = result.get("response", {})
                if response.get("errorCode") == 0 and response.get("passToken"):
                    token = response["passToken"]
                    ttl = pass_cfg.get("passTimeout", 28800000) / 1000
                    disk_cache.save("timvision", "pass_token", {"token": token}, ttl=ttl)
                    Login.refresh_ok(time.time() + ttl, disk_cache.cache_path("timvision", "pass_token"))
                    return token
                logger.warning(f"TIMvision createToken error: {response.get('errorMessage')}")
                Login.refresh_failed(str(response.get("errorMessage") or "createToken error"))
            else:
                logger.warning(f"TIMvision createToken failed: {resp.status_code} - {resp.text[:200]}")
                Login.refresh_failed(f"HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"TIMvision createToken error: {e}")
            Login.refresh_failed(e.__class__.__name__)
        return ""

    def _hapi_headers(self, pass_token: str) -> dict:
        return {
            "Authorization": f'PASS Token="{pass_token}"',
            "Xx-Device": f"pc {self.cookies.get('sessionId', '')}",
            "XX-DISTMODES": "catchup,live,svod,tvod,posttvod",
            "xx-domain": "tiita",
            "Xx-Operator": "pc",
            "Xx-Oz": "tiita",
            "Xx-Profile-Id": "0",
            "Xx-Request-Id": self.cookies.get("deviceId", ""),
            "Xx-Service": "mycanal",
            "XX-API-VERSION": "3.0",
            "XX-SPYRO-VERSION": "3.0",
            "origin": TIMVISION_WWW,
            "referer": TIMVISION_WWW + "/",
        }

    # ------------------------------------------------------------------
    # Stream playback
    # ------------------------------------------------------------------
    @staticmethod
    def _pick_best_stream(available: list) -> dict | None:
        """Prefer PlayReady DASH, then Widevine, then HD over SD."""
        if not available:
            return None
        
        pr = [a for a in available if "PLAYREADY" in str(a.get("drmType", ""))]
        wv = [a for a in available if "WIDEVINE" in str(a.get("drmType", ""))]
        pool = pr or wv or available
        for quality in ("HD", "SD"):
            for stream in pool:
                if stream.get("quality") == quality:
                    return stream
        return pool[0]

    def get_playback_info(self, content_id: str) -> dict | None:
        """Resolve a DASH MPD + Widevine license for a content ID (hapi playset/view)."""
        if not content_id:
            return None

        pass_token = self.get_pass_token()
        if not pass_token:
            logger.error("TIMvision: no pass token available for playback")
            return None
        headers = self._hapi_headers(pass_token)

        try:
            resp = self._session.get(
                f"{TIMVISION_HAPI}/conso/playset/unit/{content_id}",
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 403:
                logger.error(f"TIMvision: content {content_id} is not playable directly (brand container?)")
                return None
            if resp.status_code != 200:
                logger.error(f"TIMvision playset failed: {resp.status_code} - {resp.text[:200]}")
                return None
            playset = resp.json()
            available = playset.get("available", [])
        except Exception as e:
            logger.error(f"TIMvision playset error: {e}")
            return None

        # Unpurchased TVOD titles only expose the trailer (UNPROTECTED + "TR" hash)
        # while the full DRM version lives in "purchasable".
        purchasable = playset.get("purchasable", [])
        if purchasable and all(str(a.get("drmType", "")) == "UNPROTECTED" for a in available):
            console.print(
                f"[yellow]TIMvision: '{content_id}' has no active right "
                "(only the trailer is available — purchase required)"
            )
            return None

        stream = self._pick_best_stream(available)
        if not stream:
            logger.error(f"TIMvision: no stream available for {content_id} (purchase required?)")
            return None

        # Check for trailer (short duration) - TVOD content often only has trailer in available
        stream_duration = stream.get("duration", 0)
        if stream_duration and stream_duration < 300:  # Less than 5 minutes = trailer
            console.print(
                f"[yellow]TIMvision: '{content_id}' appears to be a trailer ({stream_duration}s) "
                "- full content requires purchase (TVOD)"
            )
            return None

        try:
            resp = self._session.put(
                f"{TIMVISION_HAPI}/conso/view",
                params={"include": "medias"},
                json=stream,
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                logger.error(f"TIMvision view failed: {resp.status_code} - {resp.text[:200]}")
                return None
            view = resp.json()
        except Exception as e:
            logger.error(f"TIMvision view error: {e}")
            return None

        mpd_url = ""
        license_path = ""
        drm_id = ""
        for media in view.get("medias", []):
            drm_id = media.get("drmId") or drm_id
            license_path = media.get("@licence") or license_path
            for file_data in media.get("files", []):
                if file_data.get("mimeType") == "application/dash+xml" and file_data.get("distribURL"):
                    mpd_url = file_data["distribURL"]
        if not mpd_url:
            logger.error(f"TIMvision: no DASH manifest for {content_id}")
            return None

        info = {
            "mpd_url": mpd_url,
            "drm_id": drm_id,
            "quality": stream.get("quality", ""),
            "drm_type": stream.get("drmType", ""),
        }
        if license_path:
            license_url = f"{TIMVISION_HAPI}{license_path}" if license_path.startswith("/") else license_path
            info["license_url"] = license_url
            
            # Determine DRM type from stream info
            drm_type = stream.get("drmType", "")
            if "PLAYREADY" in drm_type:
                # For PlayReady, use custom SOAP envelope expected by Canal+ MDRM
                info["license_request_fn"] = self._make_playready_license_request_fn(license_url, drm_id)
                info["license_headers"] = {
                    "Content-Type": "text/plain",
                    **self._hapi_headers(self.get_pass_token()),
                }
            else:
                # Widevine: use custom license request (base64 challenge)
                info["license_request_fn"] = self._make_license_request_fn(license_url, drm_id)
                info["license_headers"] = {"Content-Type": "text/plain", **self._hapi_headers(self.get_pass_token())}
                info["license_data"] = {"drmConfig": "mkpl::true", "drmId": drm_id}
        
        return info

    def _make_playready_license_request_fn(self, license_url: str, drm_id: str):
        """Create PlayReady license request function for Canal+ MDRM.
        
        The challenge from pyplayready is already a SOAP envelope (WRM header).
        We send it directly with Content-Type: text/plain.
        Returns the SOAP envelope part of the license response as raw XML string.
        """
        def license_request_fn(challenge: bytes, item: dict) -> str:
            # Challenge is already a SOAP envelope from pyplayready
            challenge_str = challenge.decode('utf-8') if isinstance(challenge, bytes) else challenge
            soap_start = challenge_str.find("<soap:Envelope")
            if soap_start >= 0:
                challenge_str = challenge_str[soap_start:]
            
            resp = self._session.post(
                license_url,
                params={"drmConfig": "mkpl::true", "drmId": drm_id},
                data=challenge_str.encode('utf-8'),
                headers={
                    "Content-Type": "text/plain",
                    **self._hapi_headers(self.get_pass_token()),
                },
                timeout=60,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"TIMvision PlayReady license failed: {resp.status_code} - {resp.text[:500]}")
            
            # Extract just the SOAP envelope from the licenseresponse wrapper
            # Response format: <licenseresponse ...><clientresponse ...><soap:Envelope>...</soap:Envelope></clientresponse></licenseresponse>
            soap_start = resp.text.find("<soap:Envelope")
            soap_end = resp.text.rfind("</soap:Envelope>")
            if soap_start >= 0 and soap_end >= 0:
                soap_envelope = resp.text[soap_start:soap_end + 16]  # +16 for '</soap:Envelope>'
                # Add XML declaration
                full_response = '<?xml version="1.0" encoding="utf-8"?>' + soap_envelope
            else:
                # Fallback: use full response
                full_response = resp.text
            
            # Return raw XML string (not base64) - CDM will encode as UTF-8
            return full_response
        
        return license_request_fn

    def _make_license_request_fn(self, license_url: str, drm_id: str):
        def license_request_fn(challenge: bytes, item: dict) -> str:
            resp = self._session.post(
                license_url,
                data=base64.b64encode(challenge),
                params={"drmConfig": "mkpl::true", "drmId": drm_id},
                headers={"Content-Type": "text/plain", **self._hapi_headers(self.get_pass_token())},
                timeout=60,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"TIMvision license failed: {resp.status_code} - {resp.text[:200]}")
            # Response is a SOAP envelope, extract base64 license from it
            match = re.search(r"<license(?:\s[^>]*)?>(.*?)</license>", resp.text, re.S)
            if not match:
                raise RuntimeError(f"TIMvision license response missing <license>: {resp.text[:200]}")
            # Return base64 string (not decoded) - CDM will decode it
            return match.group(1).strip()

        return license_request_fn

    def get_stream_url(self, content_id: str, content_type: str = "movie") -> str | None:
        """Get DASH MPD URL for content (hapi playset/view)."""
        info = self.get_playback_info(content_id)
        return info.get("mpd_url") if info else None

    def get_availability_type(self, content_id: str) -> str:
        """
        Determine if content is SVOD (included in subscription) or TVOD (pay-per-view).
        Returns: "SVOD", "TVOD", or "UNKNOWN"
        """
        if not content_id:
            return "UNKNOWN"
        
        pass_token = self.get_pass_token()
        if not pass_token:
            return "UNKNOWN"
        headers = self._hapi_headers(pass_token)

        try:
            resp = self._session.get(
                f"{TIMVISION_HAPI}/conso/playset/unit/{content_id}",
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                return "UNKNOWN"
            playset = resp.json()
            available = playset.get("available", [])
            purchasable = playset.get("purchasable", [])
            
            has_protected_available = any(
                str(a.get("drmType", "")).startswith("DRM_") for a in available
            )
            
            if has_protected_available:
                return "SVOD"
            elif purchasable and available and all(
                str(a.get("drmType", "")) == "UNPROTECTED" for a in available
            ):
                return "TVOD"
            elif available:
                return "SVOD"
            return "UNKNOWN"
        except Exception as e:
            logger.error(f"TIMvision availability check error for {content_id}: {e}")
            return "UNKNOWN"

    def get_availability_label(self, search_item: dict) -> str:
        """
        Get detailed availability label from search result item.
        Similar to RakutenTV: 'Purchase/Rental (TIMvision)', 'Included (Discovery+)', etc.
        """
        item_type = search_item.get("type", "").lower()
        is_tvod = search_item.get("isTVoD", False)
        is_in_offer = search_item.get("isInOffer", False)
        alt_logo = search_item.get("altLogoChannel", "")
        stickers = search_item.get("stickers", [])
        has_acquista = any(s.get("label") == "Acquista" for s in stickers)
        
        if item_type == "personality":
            return "Person"
        
        # Normalize partner names for display
        def normalize_partner(logo: str) -> str:
            logo_upper = logo.upper()
            if "TIMVISION" in logo_upper:
                return "TIMvision"
            if "DISCOVERY" in logo_upper:
                return "Discovery+"
            if "MEDIASET" in logo_upper:
                return "Mediaset"
            if "PARAMOUNT" in logo_upper:
                return "Paramount+"
            if "KIDS" in logo_upper:
                return "TIMvision Kids"
            if "LA7" in logo_upper:
                return "La7"
            return logo
        
        partner = normalize_partner(alt_logo)
        
        # For series (folder) and other content, apply detailed logic
        if is_tvod and "Timvision" in alt_logo:
            return "Purchase/Rental (TIMvision)"
        elif is_in_offer and "TIMVISION" in alt_logo:
            return "Included (TIMvision)"
        elif is_in_offer and alt_logo:
            return f"Included ({partner})"
        elif has_acquista and alt_logo:
            return f"Purchase/Rental ({partner})"
        elif is_in_offer:
            return "Included"
        elif is_tvod:
            return "Purchase/Rental"
        else:
            return "Unknown"

    # ------------------------------------------------------------------
    # Episodes (series)
    # ------------------------------------------------------------------
    def get_episodes(self, brand_id: str, season_id: str | None = None) -> list[dict]:
        """Get all episodes of a series brand ID with season/episode numbers.
        
        Args:
            brand_id: The brand/series ID
            season_id: Optional season ID to fetch episodes for a specific season
        """
        if not self.token:
            self._authenticate()
        if not self.token:
            return []
        try:
            params = {}
            if season_id:
                params["seasonID"] = season_id
            resp = self._session.get(
                f"{TIMVISION_HODOR_API}/episodes/{self.token}/{brand_id}",
                params=params,
                headers={"xx-operator": "pc", "xx-service": "mycanal", "xx-available-display-languages": "it-IT"},
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning(f"TIMvision episodes failed: {resp.status_code}")
                return []
            
            episodes = resp.json().get("episodes", {}).get("contents", [])
            processed = []
            for ep in episodes:
                ep_id = ep.get("contentID") or ep.get("id", "")
                if not ep_id:
                    continue
                processed.append({
                    "id": ep_id,
                    "name": ep.get("editorialTitle") or ep.get("title", ""),
                    "season": ep.get("seasonNumber"),
                    "episode": ep.get("episodeNumber"),
                    "image": ep.get("URLImage", ""),
                })
            return processed
        except Exception as e:
            logger.error(f"TIMvision episodes error: {e}")
            return []

    def search(self, query: str, page: int = 0) -> list:
        """Search TIMvision content using the web app's Hodor search endpoint."""
        if not self.is_authenticated():
            if not self.login():
                return []

        # Ensure we have a token
        if not self.token:
            self._authenticate()
        if not self.token:
            logger.error("TIMvision: no token available for search")
            return []

        try:
            params = {
                "distmodes": json.dumps(["catchup", "svod", "tvod"], separators=(",", ":")),
                "imageRatio": "169",
                "displayNBOLogo": "true",
                "displayLogo": "true",
                "trkBOLayout": "[Web/And TV/And mob] Search with TVOD",
            }
            if page:
                params.update({"page": page + 1, "pageSize": 20})
            search_url = (
                f"{TIMVISION_HODOR_API}/search/timvision_partner_discover/"
                f"{self.token}/query/{quote(query, safe='')}"
            )
            resp = self._session.get(
                search_url,
                params=params,
                headers={
                    "xx-available-display-languages": "it-IT",
                    "xx-operator": "pc",
                    "xx-service": "mycanal",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning(f"TIMvision search failed: {resp.status_code} - {resp.text[:200]}")
                return []
            data = resp.json()
            if isinstance(data, dict):
                results = data.get("contents", [])
                if isinstance(results, list):
                    return results
            logger.warning("TIMvision search returned an unexpected response structure")
            return []
        except Exception as e:
            logger.error(f"TIMvision search error: {e}")
            return []

    def get_content(self, content_id: str) -> dict | None:
        """Get content metadata by ID using hodor API."""
        if not self.is_authenticated():
            if not self.login():
                return None

        if not self.token:
            self._authenticate()
        if not self.token:
            return None

        try:
            resp = self._session.get(
                f"{TIMVISION_HODOR_API}/page/{self.token}/{content_id}.json",
                params={"aegon": "true"},
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning(f"TIMvision content failed: {resp.status_code}")
                return None
            return resp.json()
        except Exception as e:
            logger.error(f"TIMvision content error: {e}")
            return None
