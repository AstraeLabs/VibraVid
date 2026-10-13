# 26.11.2025

import json
import logging
import re
import time
import uuid
from urllib.parse import quote, urlencode, urljoin

from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

logger = logging.getLogger(__name__)

APP_NAME = "web"
APP_VERSION = "9.4.0-9ca51ca10c3047fbafa7297708f146243146d125"
BOOT_URL = "https://boot.pluto.tv/v4/start"
STITCHER_HLS = "https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv"
STITCHER_DASH = "https://ipv4.pluto.tv/api/tn/video/playout/v2/stitch"
LICENSE_BASE = "https://service-concierge.clusters.pluto.tv/v1"
_pluto_api = None

HUB_GRAPHQL_URL = "https://pluto.tv/api/tn/hubs/graphql/"
HUB_SEARCH_PERSISTED_HASH = "83cd9a7dd069413377771f9d9dc822d7ed5eacecf67c1cad09a2008fbe6d49f0"
_HASH_TTL = 7 * 24 * 3600
_hash_cache: dict[str, str] = {}
_HASH_RE = re.compile(
    r'hash:"([0-9a-f]{64})"\},kind:"Document",definitions:\['
    r'\{kind:"OperationDefinition",operation:"query",name:\{kind:"Name",value:"(GetVODContent|GetSearchSuggestions|GetSearchVideoCarousel|GetSearchConfig)"\}'
)
_CHUNK_SRC_RE = re.compile(r'src="([^"]*?_app-[^"]*?\.js[^"]*)"')
_SERIES_ID_RE = re.compile(r"/series/([a-f0-9]{24})/")
_SHOW_SLUG_RE = re.compile(r"^/shows/([^/]+)/?$")


def _refresh_hashes() -> dict[str, str]:
    """Download pluto.tv's ``_app`` bundle and extract persisted GraphQL hashes."""
    try:
        html = create_client(headers=get_headers()).get("https://pluto.tv/it/search/").text
    except Exception as e:
        logger.debug(f"Pluto TV hash refresh: search page fetch failed: {e}")
        return {}

    found: dict[str, str] = {}
    for src in _CHUNK_SRC_RE.findall(html):
        try:
            js = create_client(headers=get_headers()).get(urljoin("https://pluto.tv", src.split("?")[0])).text
        except Exception as e:
            logger.debug(f"Pluto TV hash refresh: chunk fetch failed ({src}): {e}")
            continue
        for match in _HASH_RE.finditer(js):
            found[match.group(2)] = match.group(1)
        if "GetVODContent" in found:
            break
    return found


def get_persisted_hash(operation: str = "GetVODContent") -> str:
    """Persisted hash for *operation*, refreshed from pluto.tv when stale."""
    if operation in _hash_cache:
        return _hash_cache[operation]

    try:
        from VibraVid.utils import disk_cache

        cached = disk_cache.load("plutotv", "hub_hashes")
        if isinstance(cached, dict) and cached.get(operation) and time.time() < float(cached.get("expiry", 0)):
            _hash_cache.update({k: v for k, v in cached.items() if k != "expiry"})
            return _hash_cache[operation]
    except Exception as e:
        logger.debug(f"Pluto TV hash cache load failed: {e}")

    refreshed = _refresh_hashes()
    if refreshed:
        _hash_cache.update(refreshed)
        try:
            from VibraVid.utils import disk_cache

            disk_cache.save("plutotv", "hub_hashes", {**refreshed, "expiry": time.time() + _HASH_TTL})
        except Exception as e:
            logger.debug(f"Pluto TV hash cache save failed: {e}")
        if operation in _hash_cache:
            return _hash_cache[operation]

    return HUB_SEARCH_PERSISTED_HASH


def _refresh_hashes_into_cache() -> dict[str, str]:
    """Refresh hashes from pluto.tv and persist them via ``disk_cache`` (7d TTL)."""
    refreshed = _refresh_hashes()
    if refreshed:
        _hash_cache.update(refreshed)
        try:
            from VibraVid.utils import disk_cache

            disk_cache.save("plutotv", "hub_hashes", {**refreshed, "expiry": time.time() + _HASH_TTL})
        except Exception as e:
            logger.debug(f"Pluto TV hash cache save failed: {e}")
    return refreshed


def hub_search(query: str) -> list[dict]:
    """Search Pluto TV's web hub GraphQL endpoint (same backend used by pluto.tv/search)."""
    headers = get_headers()
    headers["apollo-require-preflight"] = "true"
    headers["x-apollo-operation-name"] = "GetVODContent"
    headers["request-source"] = f"search-vod-content-{query}"
    headers["Referer"] = f"https://pluto.tv/it/search/?term={quote(query)}"
    headers["Content-Type"] = "application/json"

    def _fetch(persisted_hash: str) -> dict:
        extensions = json.dumps({"tnPersistedDocumentHash": persisted_hash}, separators=(",", ":"))
        variables = json.dumps({"query": query}, separators=(",", ":"))
        url = f"{HUB_GRAPHQL_URL}?extensions={quote(extensions)}&variables={quote(variables)}&operationName=GetVODContent"
        response = create_client(headers=headers).get(url)
        response.raise_for_status()
        return response.json()

    try:
        data = _fetch(get_persisted_hash("GetVODContent"))
    except Exception as e:
        # Stale hash after a frontend deploy -> drop the cache and retry once with a fresh value.
        status = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status_code", None)
        if status not in (400, None):
            raise

        logger.debug(f"Pluto TV hub search failed ({e}), refreshing persisted hash and retrying")
        _hash_cache.pop("GetVODContent", None)
        try:
            from VibraVid.utils import disk_cache

            disk_cache.invalidate("plutotv", "hub_hashes")
        except Exception:
            pass

        _refresh_hashes_into_cache()
        data = _fetch(get_persisted_hash("GetVODContent"))

    node = data.get("data", {}).get("searchVODContent")
    items = node.get("data") if isinstance(node, dict) else node
    if not isinstance(items, list):
        items = items or []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        
        content_type = item.get("contentType")
        item_id = item.get("id")
        slug = None

        if content_type == "show":
            for url_field in ("thumb", "thumbLandscape", "hero", "heroCompact"):
                match = _SERIES_ID_RE.search(item.get(url_field) or "")
                if match:
                    item_id = match.group(1)
                    break

            href = item.get("href") or ""
            slug_match = _SHOW_SLUG_RE.match(href)
            if slug_match:
                slug = slug_match.group(1)

        results.append(
            {
                "id": item_id,
                "name": item.get("title"),
                "type": "tv" if content_type == "show" else content_type,
                "slug": slug,
            }
        )

    return results


class PlutoAPI:
    def __init__(self):
        self.device_id = str(uuid.uuid4())
        self.user_agent = get_userAgent()
        self.session_data = None
        self._initialize()

    def _get_base_params(self, regione="IT"):
        """Get base parameters for boot requests"""
        return {
            "appName": APP_NAME,
            "appVersion": APP_VERSION,
            "clientID": self.device_id,
            "clientModelNumber": "1.0.0",
            "deviceDNT": "false",
            "deviceMake": "chrome",
            "deviceModel": "web",
            "deviceType": "web",
            "deviceVersion": "129.0.0.0",
            "marketingRegion": regione,
            "serverSideAds": "false",
            "sid": str(uuid.uuid4()),
            "userId": "",
        }

    def _initialize(self):
        """Initialize session with boot endpoint"""
        params = self._get_base_params()

        try:
            response = create_client(headers=get_headers()).get(BOOT_URL, params=params)
            response.raise_for_status()
            data = response.json()

            jwt = data.get("sessionToken", "")
            if not jwt:
                raise RuntimeError("JWT not found in boot response")

            self.session_data = {
                "jwt": jwt,
                "sid": params["sid"],
                "client_id": self.device_id,
                "regione": "IT",
                "vcc_id": "",
                "vpc_id": "",
            }

        except Exception as e:
            raise RuntimeError(f"Failed to initialize session: {e}") from e

    def get_session_for_content(self, content_ids: dict):
        """Get session data with content-specific parameters for vcc_id/vpc_id"""
        regione = content_ids.get("regione", "IT")
        params = self._get_base_params(regione)

        # Add content-specific params
        if "series_id" in content_ids:
            params["seriesIDs"] = content_ids["series_id"]
        if "episode_id" in content_ids:
            params["episodeSlugs"] = content_ids["episode_id"]
        if "movie_id" in content_ids:
            params["episodeSlugs"] = content_ids["movie_id"]

        try:
            response = create_client(headers=get_headers()).get(BOOT_URL, params=params)
            response.raise_for_status()
            data = response.json()

            jwt = data.get("sessionToken", "")
            vcc_id = (
                self._extract_from_data("vcc_id", data)
                or self._extract_from_data("vccId", data)
                or self._extract_from_data("contentCategoryId", data)
                or ""
            )
            vpc_id = (
                self._extract_from_data("vpc_id", data)
                or self._extract_from_data("vpcId", data)
                or self._extract_from_data("playlistContentId", data)
                or self._extract_from_data("contentId", data)
                or ""
            )

            # Also check in VOD episodes for vpc_id
            vod_episodes = self._extract_from_data("episodes", data) or []
            if isinstance(vod_episodes, list) and vod_episodes and not vpc_id:
                ep = vod_episodes[0]
                if isinstance(ep, dict):
                    vpc_id = ep.get("series", {}).get("_id", "") if isinstance(ep.get("series"), dict) else ""

            return {
                "jwt": jwt,
                "sid": params["sid"],
                "vcc_id": vcc_id,
                "vpc_id": vpc_id,
                "client_id": self.device_id,
                "regione": regione,
            }

        except Exception as e:
            raise RuntimeError(f"Failed to get session for content: {e}") from e

    def _extract_from_data(self, key, data):
        """Extract value from nested dict/list"""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for v in data.values():
                r = self._extract_from_data(key, v)
                if r is not None:
                    return r
        elif isinstance(data, list):
            for item in data:
                r = self._extract_from_data(key, item)
                if r is not None:
                    return r
        return None

    def get_request_headers(self):
        headers = get_headers()
        if self.session_data:
            headers["authorization"] = f"Bearer {self.session_data['jwt']}"
        return headers


def get_api():
    """Get or create Pluto API instance"""
    global _pluto_api
    if _pluto_api is None:
        _pluto_api = PlutoAPI()
    return _pluto_api


def get_playback_url_episode(episode_id: str, content_ids: dict = None):
    """Get the playback M3U8 URL for a given episode ID."""
    api = get_api()
    session = api.get_session_for_content(content_ids or {"episode_id": episode_id, "regione": "IT"})

    params = {
        "advertisingId": "",
        "appName": APP_NAME,
        "appVersion": APP_VERSION,
        "app_name": APP_NAME,
        "clientDeviceType": "0",
        "clientID": session["client_id"],
        "clientModelNumber": "1.0.0",
        "country": session["regione"],
        "deviceDNT": "false",
        "deviceId": session["client_id"],
        "deviceLat": "45.47",
        "deviceLon": "9.19",
        "deviceMake": "chrome",
        "deviceModel": "web",
        "deviceType": "web",
        "deviceVersion": "129.0.0.0",
        "marketingRegion": session["regione"],
        "serverSideAds": "false",
        "sessionID": session["sid"],
        "sid": session["sid"],
        "userId": "",
        "jwt": session["jwt"],
        "masterJWTPassthrough": "true",
        "includeExtendedEvents": "true",
    }

    if session.get("vcc_id"):
        params["vcc_id"] = session["vcc_id"]
    if session.get("vpc_id"):
        params["vpc_id"] = session["vpc_id"]

    qs = urlencode(params)
    return f"{STITCHER_HLS}/v2/stitch/hls/episode/{episode_id}/master.m3u8?{qs}"


def get_playback_dash_episode(episode_id: str, content_ids: dict = None):
    """Get the playback DASH MPD URL and auth headers for a given episode ID."""
    api = get_api()
    session = api.get_session_for_content(content_ids or {"episode_id": episode_id, "regione": "IT"})

    params = {"includeExtendedEvents": "true"}
    mpd_url = f"{STITCHER_DASH}/dash/episode/{episode_id}/main.mpd?{urlencode(params)}"

    mpd_headers = {
        "authorization": f"Bearer {session['jwt']}",
        "User-Agent": api.user_agent,
        "Accept": "*/*",
        "Origin": "https://pluto.tv",
        "Referer": "https://pluto.tv/",
    }
    return mpd_url, mpd_headers


def get_playready_license(mpd_headers: dict):
    """PlayReady license server URL + headers, reusing the session bearer token from the MPD request."""
    license_url = f"{LICENSE_BASE}/pr"
    license_headers = {
        "authorization": mpd_headers["authorization"],
        "origin": "https://pluto.tv",
        "referer": "https://pluto.tv/",
        "soapaction": '"http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense"',
    }
    return license_url, license_headers
