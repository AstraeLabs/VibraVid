# 14.10.26
# By @Syncs-Luca98

import logging
import time
import uuid

from VibraVid.services._base.login_status import Login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

# Chili web endpoints
BASE_URL = "https://www.chili.com"
API_BASE = "https://www.chili.com/api"
ORIGIN = "https://www.chili.com"
REFERER = "https://www.chili.com/"

DEFAULT_MARKET = "it"
MARKETS = ("it", "uk", "de", "at", "pl", "es")
REGIONS = [code.upper() for code in MARKETS]
MARKET_AUDIO = {
    "it": "ITA",
    "uk": "ENG",
    "de": "DEU",
    "at": "DEU",
    "pl": "POL",
    "es": "SPA",
}

# Cookie domains for Chili
CHILI_DOMAINS = [
    ".chili.com",
    ".it.chili.com",
    ".uk.chili.com",
    ".de.chili.com",
    ".at.chili.com",
    ".pl.chili.com",
    ".es.chili.com",
]


def normalize_market(code: str | None) -> str:
    value = str(code or "").strip().lower()
    value = "uk" if value == "gb" else value
    if value not in MARKETS:
        raise ValueError(f"Unknown Chili country '{code}'. Known: {', '.join(MARKETS)}")
    return value


def default_market() -> str:
    from VibraVid.core.ui.tracker import context_tracker

    country = (context_tracker.site_options or {}).get("country")
    if not country:
        country = config_manager.login.get_section("chili").get("country")
    return normalize_market(country) if country else DEFAULT_MARKET


def resolve_market(market: str | None = None) -> str:
    return normalize_market(market) if market else default_market()


def locale_for(market: str) -> str:
    return "en" if market in ("uk",) else market


class ChiliClient:
    _login_shown: set[str] = set()
    _TOKEN_BUFFER = 60.0

    def __init__(self, market: str | None = None):
        self.market = resolve_market(market)
        cached_device = disk_cache.load("chili", f"device_{self.market}")
        self._device_id = cached_device.get("id") if isinstance(cached_device, dict) else None
        self._device_registered = bool(self._device_id)
        if not self._device_id:
            self._device_id = str(uuid.uuid4())

        self._gatekeeper_token: str | None = None
        self._gatekeeper_expires: float = 0
        self._configured_access_token: str | None = None
        self._email: str | None = None
        self.cookies: dict = {}
        self._session = None
        self._load_credentials()
        self._init_session()
        self._apply_cookies()
        if self.market not in ChiliClient._login_shown:
            ChiliClient._login_shown.add(self.market)
            if self._email or self._configured_access_token or self.cookies:
                Login.show(Login.ACCOUNT, user=self._email or self.cookies.get("email", "chili-user"))
            else:
                Login.show(Login.ANONYMOUS)

    def _token_cache_name(self) -> str:
        return f"gatekeeper_{self.market}"

    def _load_credentials(self):
        """Load cookies from login chili section."""
        try:
            self.cookies = config_manager.login.get_section("chili", {})
            email = self.cookies.get("email")
            self._email = email.strip() if isinstance(email, str) and email.strip() else None
            access_token = self.cookies.get("access_token")
            self._configured_access_token = (
                access_token.strip() if isinstance(access_token, str) and access_token.strip() else None
            )

            # Remove config keys that aren't cookies
            for key in ("email", "password", "country", "access_token", "refresh_token", "cdm"):
                self.cookies.pop(key, None)
        except Exception:
            self.cookies = {}
            self._configured_access_token = None
            self._email = None

    def _init_session(self):
        """Initialize HTTP session with proper headers."""
        self._session = create_client(
            headers={
                "User-Agent": get_userAgent(),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": f"{locale_for(self.market)}-{self.market.upper()},{locale_for(self.market)};q=0.9,en;q=0.8",
                "Referer": BASE_URL + "/",
                "Origin": ORIGIN,
                "X-Chili-Api-Version": "1.1",
                "X-Chili-Accept-Language": locale_for(self.market),
                "X-Chili-Device-Profile": "WEB",
                "X-Chili-Device-Id": self._device_id,
                "X-Chili-User-Country": self.market.upper(),
                "X-Chili-Device-Store": "CHILI",
            }
        )

    def _apply_cookies(self):
        """Set cookies on session for all Chili domains."""
        if not self.cookies:
            return
        for name, value in self.cookies.items():
            if isinstance(value, str):
                for domain in CHILI_DOMAINS:
                    self._session.cookies.set(name, value, domain=domain, path="/")

    def is_authenticated(self) -> bool:
        """Check if we have valid session cookies."""
        # Check for key session cookies
        key_cookies = ("sessionId", "access_token", "userId", "auth_token", "ch_session")
        return any(self.cookies.get(k) for k in key_cookies) or len(self.cookies) > 3

    def _auth_headers(self) -> dict:
        """Additional auth headers if needed."""
        return {}

    def _register_device(self) -> None:
        """Register a web device and keep the ID assigned by Chili."""
        url = f"https://{self.market}.chili.com/gatekeeper/api/v1/devices/"
        payload = {
            "appVersion": "2.5.953",
            "displayName": "VibraVid",
            "model": "Chrome",
            "manufacturer": "Google Inc.",
            "osName": "Windows",
            "osVersion": "10",
            "platform": "Chrome",
            "platformVersion": "151.0.0.0",
            "architecture": "x64",
            "profile": "WEB",
            "store": "CHILI",
            "screenWidth": 1920,
            "screenHeight": 1080,
        }
        with create_client(headers=self._session.headers) as client:
            for name, value in self._session.cookies.items():
                client.cookies.set(name, value)
            response = client.post(url, json=payload, timeout=30)
        response.raise_for_status()

        data = response.json()
        device_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(device_id, str) or not device_id.strip():
            raise RuntimeError("Chili device registration returned no device ID")

        self._device_id = device_id
        self._device_registered = True
        self._session.headers["X-Chili-Device-Id"] = device_id
        disk_cache.save("chili", f"device_{self.market}", {"id": device_id})

    def _is_token_expired(self, token: str) -> bool:
        """Check if a JWT bearer token is expired."""
        import base64
        import json

        try:
            parts = token.split(".")
            if len(parts) < 2:
                return False
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding < 4:
                payload_b64 += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            if exp:
                return time.time() >= exp
        except Exception:
            return False
        return False

    def _jwt_expiry_ts(self, token: str) -> float | None:
        """Epoch seconds from a JWT `exp` claim, None if undecodable."""
        import base64
        import json

        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            return float(json.loads(base64.urlsafe_b64decode(payload_b64)).get("exp"))
        except Exception:
            return None

    def _request_gatekeeper_token(self) -> dict:
        """Single network round-trip for a fresh gatekeeper token (refresh grant, else password grant)."""
        login_section = config_manager.login.get_section("chili", {})

        # Try refresh_token grant first
        refresh_token = login_section.get("refresh_token")
        if refresh_token:
            if not self._device_registered:
                self._register_device()
            url = f"https://{self.market}.chili.com/gatekeeper/api/v1/oauth2/token"
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "x_device_id": self._device_id,
            }
            headers = {
                "User-Agent": get_userAgent(),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Chili-Device-Id": self._device_id,
            }
            with create_client(headers=headers) as client:
                resp = client.post(url, data=data, timeout=30)
            if resp.status_code == 200:
                token_data = resp.json()
                if isinstance(token_data, dict) and token_data.get("access_token"):
                    return token_data
                raise RuntimeError("gatekeeper refresh returned no access_token")
            logger.warning("Chili refresh_token grant failed (%s), trying password grant", resp.status_code)

        # Fallback to password grant
        email = login_section.get("email")
        password = login_section.get("password")
        if not email or not password:
            raise RuntimeError("Chili credentials not configured in login (need email/password or refresh_token)")

        if not self._device_registered:
            self._register_device()

        url = f"https://{self.market}.chili.com/gatekeeper/api/v1/oauth2/token"
        data = {
            "grant_type": "password",
            "username": email,
            "password": password,
            "x_device_id": self._device_id,
        }
        headers = {
            "User-Agent": get_userAgent(),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Chili-Device-Id": self._device_id,
        }
        with create_client(headers=headers) as client:
            resp = client.post(url, data=data, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()
        if not isinstance(token_data, dict) or not token_data.get("access_token"):
            raise RuntimeError("gatekeeper password grant returned no access_token")
        return token_data

    def _get_gatekeeper_token(self) -> str:
        """Get gatekeeper OAuth2 token for streaming API (memory -> disk_cache -> network)."""
        if self._gatekeeper_token and time.time() < self._gatekeeper_expires - self._TOKEN_BUFFER:
            return self._gatekeeper_token
        if self._configured_access_token and not self._is_token_expired(self._configured_access_token):
            return self._configured_access_token

        cache_name = self._token_cache_name()
        with disk_cache.lock("chili", cache_name):
            if self._gatekeeper_token and time.time() < self._gatekeeper_expires - self._TOKEN_BUFFER:
                return self._gatekeeper_token
            cached = disk_cache.load_fresh("chili", cache_name, buffer_seconds=self._TOKEN_BUFFER)
            if isinstance(cached, dict) and cached.get("access_token"):
                try:
                    self._gatekeeper_expires = float(cached.get("expiry") or 0)
                except (TypeError, ValueError):
                    self._gatekeeper_expires = 0
                if not self._gatekeeper_expires:
                    self._gatekeeper_expires = self._jwt_expiry_ts(cached["access_token"]) or 0
                if time.time() < self._gatekeeper_expires - self._TOKEN_BUFFER:
                    self._gatekeeper_token = cached["access_token"]
                    Login.refresh_cached(disk_cache.cache_path("chili", cache_name))
                    return self._gatekeeper_token

            Login.refresh_try()
            try:
                token_data = self._request_gatekeeper_token()
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                Login.refresh_failed(f"HTTP {status}" if status else (str(e)[:60] or e.__class__.__name__))
                raise
            access_token = token_data["access_token"]
            try:
                expires_in = int(token_data.get("expires_in", 1200) or 1200)
            except (TypeError, ValueError):
                expires_in = 1200
            expiry = self._jwt_expiry_ts(access_token) or (time.time() + max(0, expires_in))
            self._gatekeeper_token = access_token
            self._gatekeeper_expires = expiry
            disk_cache.save("chili", cache_name, {"access_token": access_token, "expiry": expiry})
            Login.refresh_ok(expiry)
            return self._gatekeeper_token

    def _streaming_headers(self) -> dict:
        """Headers for streaming API calls using gatekeeper token."""
        token = self._get_gatekeeper_token()
        return {
            "User-Agent": get_userAgent(),
            "Accept": "application/json",
            "Accept-Language": f"{locale_for(self.market)}-{self.market.upper()},{locale_for(self.market)};q=0.9",
            "Origin": "https://www.chili.com",
            "Referer": f"https://{self.market}.chili.com/",
            "X-Chili-Api-Version": "1.1",
            "X-Chili-Accept-Language": locale_for(self.market),
            "X-Chili-Device-Profile": "WEB",
            "X-Chili-Device-Id": self._device_id,
            "X-Chili-User-Country": self.market.upper(),
            "X-Chili-Device-Store": "CHILI",
            "Authorization": f"Bearer {token}",
        }

    def _invalidate_token_cache(self) -> None:
        self._gatekeeper_token = None
        self._gatekeeper_expires = 0
        disk_cache.invalidate("chili", self._token_cache_name())

    def _streaming_get(self, url: str, params: dict | None = None) -> dict:
        """GET request to streaming API with gatekeeper auth."""
        headers = self._streaming_headers()
        with create_client(headers=headers) as http_client:
            resp = http_client.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            self._invalidate_token_cache()
            headers = self._streaming_headers()
            with create_client(headers=headers) as http_client:
                resp = http_client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET request to Chili API."""
        url = f"{API_BASE}{path}" if path.startswith("/") else f"{API_BASE}/{path}"
        with create_client(headers=self._session.headers) as client:
            # Copy cookies from our session
            for name, value in self._session.cookies.items():
                client.cookies.set(name, value)
            resp = client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "json" not in ct.lower():
            raise RuntimeError(f"Chili API returned non-JSON: {ct} - {resp.text[:200]}")
        return resp.json()

    def _post(self, path: str, json_data: dict | None = None, data: dict | None = None) -> dict:
        """POST request to Chili API."""
        url = f"{API_BASE}{path}" if path.startswith("/") else f"{API_BASE}/{path}"
        headers = dict(self._session.headers)
        if json_data is not None:
            headers["Content-Type"] = "application/json"
        with create_client(headers=headers) as client:
            for name, value in self._session.cookies.items():
                client.cookies.set(name, value)
            if json_data is not None:
                resp = client.post(url, json=json_data, timeout=30)
            else:
                resp = client.post(url, data=data, timeout=30)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "json" not in ct.lower():
            raise RuntimeError(f"Chili API returned non-JSON: {ct} - {resp.text[:200]}")
        return resp.json()

    # --- Search endpoints ---
    def _search_catalogue(self, query: str, content_type: str, page: int, per_page: int) -> list[dict]:
        url = f"https://{self.market}.chili.com/api/v1/catalogue/search"
        params = {"query": query, "page": page, "limit": per_page, "type": content_type}
        response = self._catalogue_get(url, params)
        response.raise_for_status()
        payload = response.json()

        results = payload
        while isinstance(results, dict):
            for key in ("results", "data", "items"):
                if key in results:
                    results = results[key]
                    break
            else:
                raise RuntimeError("Chili catalogue search returned an unexpected JSON object")

        if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
            raise RuntimeError("Chili catalogue search returned an unexpected result list")
        return results

    def _catalogue_get(self, url: str, params: dict):
        if not self._device_registered:
            self._register_device()

        response = self._send_catalogue_get(url, params)
        if response.status_code == 400:
            try:
                error = response.json()
            except ValueError:
                error = {}
            if isinstance(error, dict) and error.get("code") == "DEVICE_NOT_FOUND":
                self._device_registered = False
                self._device_id = str(uuid.uuid4())
                self._session.headers["X-Chili-Device-Id"] = self._device_id
                self._register_device()
                response = self._send_catalogue_get(url, params)
        return response

    def _send_catalogue_get(self, url: str, params: dict):
        with create_client(headers=self._session.headers) as client:
            for name, value in self._session.cookies.items():
                client.cookies.set(name, value)
            return client.get(url, params=params, timeout=30)

    def search(self, query: str, page: int = 1, per_page: int = 20) -> list[dict]:
        """Search movies and series."""
        return self.search_movies(query, page, per_page) + self.search_series(query, page, per_page)

    def search_movies(self, query: str, page: int = 1, per_page: int = 20) -> list[dict]:
        return self._search_catalogue(query, "MOVIE", page, per_page)

    def search_series(self, query: str, page: int = 1, per_page: int = 20) -> list[dict]:
        results = self._search_catalogue(query, "SERIES", page, per_page)
        unique_results = []
        seen_slugs = set()
        for item in results:
            slug = item.get("urlSlug") or item.get("id")
            if slug and slug in seen_slugs:
                continue
            if slug:
                seen_slugs.add(slug)
            unique_results.append(item)

        try:
            seasons = self._search_catalogue(query, "SEASON", page, per_page)
        except Exception as e:
            logger.warning(f"Could not read Chili season availability for '{query}': {e}")
            seasons = []

        availability_by_slug: dict[str, list[tuple[str, str]]] = {}
        for season in seasons:
            slug = season.get("urlSlug")
            if not slug:
                continue
            label = availability_label(season)
            if label == "Availability not indicated":
                continue
            status = purchase_kind(season)
            availability_by_slug.setdefault(slug, []).append((status, label))

        for item in unique_results:
            season_availability = availability_by_slug.get(item.get("urlSlug"), [])
            labels = list(dict.fromkeys(label for _, label in season_availability))
            if labels:
                item["availability"] = " / ".join(labels)
                item["kind"] = "avod" if all(status == "avod" for status, _ in season_availability) else "paid"
        return unique_results

    # --- Content detail ---
    def get_content(self, content_id: str) -> dict | None:
        """Get content detail by ID."""
        url = f"https://{self.market}.chili.com/entertainment/api/v1/contents/{content_id}"
        response = self._catalogue_get(url, {})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Chili content detail for '{content_id}' returned an unexpected response")
        return data

    def get_movie(self, movie_id: str) -> dict | None:
        return self.get_content(movie_id)

    def get_series(self, series_id: str) -> dict | None:
        return self.get_content(series_id)

    def get_seasons(self, series_id: str) -> list[dict]:
        return [item for item in self.get_children(series_id) if item.get("type") == "SEASON"]

    def get_episodes(self, series_id: str, season_number: int) -> list[dict]:
        season = next(
            (item for item in self.get_seasons(series_id) if item.get("orderIndex") == season_number),
            None,
        )
        return self.get_children(season["id"]) if season else []

    def get_children(self, content_id: str) -> list[dict]:
        url = f"https://{self.market}.chili.com/entertainment/api/v1/contents/{content_id}/child"
        response = self._catalogue_get(url, {})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RuntimeError(f"Chili child list for '{content_id}' returned an unexpected response")
        return data

    # --- Streaming ---
    def get_stream_url(self, content_id: str, content_type: str = "movie") -> str | None:
        """Get streaming URL (HLS/DASH) for content."""
        try:
            data = self._get(f"/stream/{content_type}/{content_id}")
            return data.get("url") or data.get("stream_url") or data.get("hls_url")
        except Exception as e:
            logger.error(f"Chili get_stream_url error: {e}")
            return None

    def get_license_url(self, content_id: str) -> str | None:
        """Get DRM license URL."""
        try:
            data = self._get(f"/license/{content_id}")
            return data.get("license_url") or data.get("url")
        except Exception as e:
            logger.error(f"Chili get_license_url error: {e}")
            return None

    # --- User library ---
    def get_library(self) -> list[dict]:
        """Get user's purchased/rented content."""
        try:
            data = self._get("/user/library")
            return data.get("library", data.get("data", []))
        except Exception as e:
            logger.error(f"Chili get_library error: {e}")
            return []

    # --- Streaming ---
    def get_streaming_rights(self, content_id: str) -> dict:
        """Get streaming rights for content via gatekeeper auth."""
        url = f"https://{self.market}.chili.com/room-of-requirement/api/v1/streaming/rights/{content_id}"
        return self._streaming_get(url)

    def get_video_asset(self, catalog_id: str) -> dict:
        """Get video asset info for content via gatekeeper auth."""
        url = f"https://{self.market}.chili.com/flux-capacitor/api/v1/videoasset"
        data = self._streaming_get(url, params={"catalog": catalog_id, "format": "DASH"})
        return data[0] if isinstance(data, list) and data else data

    def get_play_info(self, video_asset_id: str, cost_category_id: str) -> dict:
        """Get play info (manifest URL) for content via gatekeeper auth."""
        url = f"https://{self.market}.chili.com/flux-capacitor/api/v1/videoasset/{video_asset_id}/play"
        return self._streaming_get(url, params={"costCategoryId": cost_category_id})

    def get_streaming_urls(self, video_asset_id: str) -> list[dict]:
        """Create a streaming session and resolve its available DASH URLs."""
        base_headers = self._streaming_headers()
        session_headers = {
            **base_headers,
            "X-Chili-Avod-Compatibility": "free,free-ads",
            "X-Chili-Event-Compatibility": "true",
            "X-Chili-Accept-Stream": "mpd/cenc+h264;q=0.9, mpd/clear+h264;q=0.7, mp4/;q=0.1",
            "X-Chili-Streaming-Proto": "https",
            "X-Chili-Manifest-Properties": "subtitles",
            "X-Chili-Accept-Stream-Mode": "multi/codec-compatibility;q=0.8, mono/strict;q=0.7",
        }
        session_url = f"https://{self.market}.chili.com/flux-capacitor/api/v1/streaming/session"
        with create_client(headers=session_headers) as http_client:
            response = http_client.post(session_url, json={"videoAssetId": video_asset_id}, timeout=30)
        response.raise_for_status()

        session_data = response.json()
        session_id = session_data.get("id") if isinstance(session_data, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("Chili streaming session response did not contain an ID")
        self._streaming_session_id = session_id

        urls_headers = {
            key: value
            for key, value in base_headers.items()
            if key.lower() != "authorization" and key.lower() != "x-chili-api-version"
        }
        urls_headers.update(
            {
                "X-Chili-Avod-Compatibility": "free,free-ads",
                "X-Chili-Event-Compatibility": "true",
                "X-Chili-Streaming-Session": session_id,
            }
        )
        urls_url = f"https://{self.market}.chili.com/flux-capacitor/api/v1/streaming/urls"
        with create_client(headers=urls_headers) as http_client:
            response = http_client.get(urls_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise RuntimeError("Chili streaming URLs endpoint returned an unexpected response")
        # NOTE: Session is intentionally NOT deleted here — the license request
        # needs the sessionId. Let it expire on the server side.
        return data

    @staticmethod
    def _select_stream_url(streams: list[dict], audio: str, quality: str) -> dict:
        """Choose the best returned manifest for the requested language and quality."""
        available = [item for item in streams if isinstance(item.get("url"), str) and item["url"]]
        if not available:
            raise RuntimeError("Chili streaming URLs response did not contain a DASH URL")

        language = (audio or "").strip().lower()
        language_matches = [item for item in available if str(item.get("language") or "").strip().lower() == language]
        candidates = language_matches or available
        if language and not language_matches:
            logger.warning("Chili did not return a %s audio stream; using the first available language", audio)

        quality_order = {
            "UHD": ("UHD", "HDP", "HD", "SD"),
            "FHD": ("HDP", "HD", "SD", "UHD"),
            "HD": ("HD", "HDP", "SD", "UHD"),
            "SD": ("SD", "HD", "HDP", "UHD"),
        }.get((quality or "").upper(), ("HDP", "HD", "SD", "UHD"))
        for requested_quality in quality_order:
            match = next(
                (item for item in candidates if str(item.get("quality") or "").upper() == requested_quality),
                None,
            )
            if match:
                return match
        return candidates[0]

    def get_avod_streaming(
        self,
        content_id: str,
        content_type: str = "movies",
        audio: str = "ITA",
        quality: str = "FHD",
        device: str = "lgui40",
    ) -> dict:
        """Resolve an AVOD DASH manifest through CHILI's streaming-session API."""
        rights = self.get_streaming_rights(content_id)
        if not rights.get("playable"):
            reason = rights.get("reason", "Unknown")
            raise RuntimeError(f"Content not playable: {reason}")

        cost_category_id = rights.get("costCategoryId")
        video_asset = self.get_video_asset(content_id)
        video_asset_id = video_asset.get("id")

        if not video_asset_id or not cost_category_id:
            raise RuntimeError("Missing video asset or cost category ID")

        streams = self.get_streaming_urls(video_asset_id)
        selected = self._select_stream_url(streams, audio, quality)

        # Chili license endpoint is flux-capacitor/api/v1/licensing/{drm}?sessionId={id}
        if self._streaming_session_id:
            license_url = f"https://{self.market}.chili.com/flux-capacitor/api/v1/licensing/playready?sessionId={self._streaming_session_id}"
        else:
            license_url = PLAYREADY_LICENSE_URL_AVOD
        drm_type = selected.get("drm") or "playready"

        return {
            "url": selected["url"],
            "license_url": license_url,
            "id": video_asset_id,
            "wrid": video_asset_id,
            "session_id": self._streaming_session_id,
            "all_subtitles": selected.get("subtitles") or video_asset.get("availableSubtitles", {}),
            "audio": selected.get("language") or audio,
            "quality": selected.get("quality") or quality,
            "drm": drm_type,
        }

    def get_me_streaming(
        self,
        content_id: str,
        content_type: str = "movies",
        audio: str = "ITA",
        quality: str = "FHD",
        device: str = "lgui40",
    ) -> dict:
        """Get authenticated (purchased/rented) streaming info."""
        return self.get_avod_streaming(content_id, content_type, audio, quality, device)


_client_instance: ChiliClient | None = None
PLAYREADY_LICENSE_URL_AVOD = "https://it.chili.com/flux-capacitor/api/v1/licensing/playready"
WIDEVINE_LICENSE_URL_AVOD = "https://it.chili.com/flux-capacitor/api/v1/licensing/widevine"


def _get_client(market: str | None = None) -> ChiliClient:
    global _client_instance
    if _client_instance is None or _client_instance.market != resolve_market(market):
        _client_instance = ChiliClient(market)
    return _client_instance


def search_endpoint(query: str, endpoint: str, per_page: int = 20, market: str | None = None) -> list[dict]:
    """Compatibility function for existing code."""
    client = _get_client(market)
    if endpoint == "movies":
        return client.search_movies(query, per_page=per_page)
    elif endpoint in ("tv_shows", "series"):
        return client.search_series(query, per_page=per_page)
    return client.search(query, per_page=per_page)


def get_movie_detail(movie_id: str, market: str | None = None) -> dict:
    client = _get_client(market)
    return client.get_movie(movie_id) or {}


def get_tvshow_detail(show_id: str, market: str | None = None) -> dict:
    client = _get_client(market)
    return client.get_series(show_id) or {}


def get_season_detail(season_id: str, market: str | None = None) -> dict:
    client = _get_client(market)
    return client.get_content(season_id) or {}


def get_seasons(show_id: str, market: str | None = None) -> list[dict]:
    return _get_client(market).get_seasons(show_id)


def get_episodes(season_id: str, market: str | None = None) -> list[dict]:
    return _get_client(market).get_children(season_id)


def purchase_kind(detail: dict) -> str:
    try:
        kinds = detail["labels"]["purchase_types"]
        return str(kinds[0].get("kind") or "unknown")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass

    free_mode = str(detail.get("freeMode") or "").upper()
    if detail.get("freeForStreaming") is True or free_mode.startswith("FREE"):
        return "avod"

    prices = detail.get("shownPrices")
    if isinstance(prices, dict) and prices:
        return "paid"
    if isinstance(detail.get("shownPrice"), dict) and detail["shownPrice"].get("price") is not None:
        return "paid"
    return "unknown"


def _format_price(price: dict) -> str:
    amount = price.get("discountedPrice")
    if amount is None:
        amount = price.get("price")
    if amount is None:
        return ""

    currency = str(price.get("currency") or "").strip()
    return f"{amount:g} {currency}".strip() if isinstance(amount, (int, float)) else f"{amount} {currency}".strip()


def price_label(detail: dict) -> str:
    try:
        kinds = detail["labels"]["purchase_types"]
        return " / ".join(
            f"{entry.get('label', '')} {entry.get('price', '')}".strip() for entry in kinds if isinstance(entry, dict)
        )
    except (KeyError, TypeError, AttributeError):
        pass

    prices = detail.get("shownPrices")
    if not isinstance(prices, dict):
        prices = {}

    labels = []
    for key, label in (("VOD48h", "Rental"), ("EST", "Purchase")):
        amount = _format_price(prices.get(key, {})) if isinstance(prices.get(key), dict) else ""
        if amount:
            labels.append(f"{label} {amount}")

    if not labels:
        amount = _format_price(detail.get("shownPrice", {})) if isinstance(detail.get("shownPrice"), dict) else ""
        if amount:
            labels.append(amount)
    return " / ".join(labels)


def availability_label(detail: dict) -> str:
    kind = purchase_kind(detail)
    label = price_label(detail)
    free_mode = str(detail.get("freeMode") or "").upper()
    if kind == "avod":
        return "Free with ads" if "ADS" in free_mode else "Free"
    if kind == "paid":
        return f"Paid: {label}" if label else "Paid"
    return "Availability not indicated"


def artwork(detail: dict) -> str:
    for key in ("coverUrl", "wideCoverUrl", "backdropUrl", "posterUrl"):
        if detail.get(key):
            return detail[key]

    images = detail.get("images") or detail.get("poster") or {}
    if isinstance(images, dict):
        for key in ("artwork", "artwork_webp", "snapshot", "standard_artwork", "poster", "cover"):
            if images.get(key):
                return images[key]
    elif isinstance(images, str):
        return images
    return ""


def login(*args, **kwargs) -> dict:
    """Return session info using gatekeeper OAuth."""
    client = _get_client()
    return {
        "access_token": client._get_gatekeeper_token(),
        "session_uuid": str(uuid.uuid4()),
        "classification_id": "36",
        "locale": locale_for(client.market),
        "market_code": client.market,
    }


def get_avod_streaming(
    content_id: str,
    content_type: str = "movies",
    audio: str = "ITA",
    quality: str = "FHD",
    device: str = "lgui40",
    market: str | None = None,
) -> dict:
    """Resolve an AVOD DASH manifest through CHILI's streaming-session API."""
    client = _get_client(market)
    return client.get_avod_streaming(content_id, content_type, audio, quality, device)


def get_me_streaming(
    content_id: str,
    content_type: str = "movies",
    audio: str = "ITA",
    quality: str = "FHD",
    device: str = "lgui40",
    market: str | None = None,
) -> dict:
    """Get authenticated streaming info."""
    client = _get_client(market)
    return client.get_me_streaming(content_id, content_type, audio, quality, device)


def _credentials() -> tuple[str | None, str | None, str | None, str | None]:
    """Not used with cookie-based auth."""
    return None, None, None, None


DEVICES = {
    "lgui40": {"player": "lgui40:DASH-CENC:PR", "drm": "playready"},
    "atvui40": {"player": "atvui40:DASH-CENC:WVM", "drm": "widevine"},
    "web": {"player": "web:DASH-CENC:WVM", "drm": "widevine"},
}
APP_VERSION_LG = "v2.77.0"
DEVICE_SERIAL_LG = "203WRMD8U920"
GIZMO = "https://gizmo.chili.com/v3"


def classification_id(market: str | None = None) -> str:
    return "36"


def audio_language(market: str | None = None, detail: dict | None = None, requested: str | None = None) -> str:
    if requested:
        return requested.upper()
    market = resolve_market(market)
    return MARKET_AUDIO.get(market, "ENG")


def is_playready_license(license_url: str) -> bool:
    return "playready" in (license_url or "").lower()


def streaming_uuid(stream_info: dict) -> str:
    return str(stream_info.get("id", stream_info.get("wrid", "")))


def playready_license_headers(streaming_uuid: str, cookies: dict | None = None) -> dict:
    import base64

    customdata = base64.b64encode(streaming_uuid.encode("utf-8")).decode("utf-8")
    headers = {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": get_userAgent(),
        "Content-Type": "text/xml; charset=utf-8",
        "soapaction": '"http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense"',
        "http-header-customdata": customdata,
    }
    return headers


def widevine_license_headers(cookies: dict | None = None) -> dict:
    headers = {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": get_userAgent(),
        "Content-Type": "application/octet-stream",
    }
    return headers


def mpd_headers() -> dict:
    return {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": get_userAgent(),
        "Accept": "*/*",
    }
