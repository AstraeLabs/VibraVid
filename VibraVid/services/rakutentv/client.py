# 09.10.26

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid

from VibraVid.services._base.login_status import ACCOUNT, print_login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

logger = logging.getLogger(__name__)

GIZMO = "https://gizmo.rakuten.tv/v3"
ORIGIN = "https://www.rakuten.tv"
REFERER = "https://www.rakuten.tv/"

DEFAULT_MARKET = "it"
CLASSIFICATION_IT = "36"  # only the fallback for the default market if /classifications cannot be reached
MARKETS = (
    "uk", "es", "fr", "de", "it", "at", "ie", "nl", "ch", "pt", "lu", "be", "ro", "bg", "rs", "al", "ba", "hr", "me",
    "si", "cz", "sk", "hu", "mk", "ua", "pl", "mt", "gr", "cy", "se", "dk", "fi", "no", "is", "ee", "lv", "lt", "jp",
    "us", "nz", "sa", "ae",
)
REGIONS = ["GB" if code == "uk" else code.upper() for code in MARKETS]
MARKET_AUDIO = {
    "it": "ITA", "nl": "NLD", "fr": "FRA", "de": "DEU", "at": "DEU", "ch": "DEU", "es": "SPA", "pt": "POR",
    "uk": "ENG", "ie": "ENG", "pl": "POL", "cz": "CES", "hu": "HUN", "se": "SWE", "dk": "DAN", "fi": "FIN",
    "no": "NOR", "gr": "ELL", "ro": "RON", "bg": "BUL", "sk": "SLK", "hr": "HRV", "si": "SLV", "ua": "UKR",
    "jp": "JPN", "lt": "LIT", "lv": "LAV", "ee": "EST",
}
_CLASSIFICATION_TTL = 7 * 24 * 3600
_classification_cache: dict[str, str] = {}
DEVICES = {
    "lgui40": {"player": "lgui40:DASH-CENC:PR", "drm": "playready"},
    "atvui40": {"player": "atvui40:DASH-CENC:WVM", "drm": "widevine"},
    "web": {"player": "web:DASH-CENC:WVM", "drm": "widevine"},
}
APP_VERSION_LG = "v2.77.0"
DEVICE_SERIAL_LG = "203WRMD8U920"


def normalize_market(code: str | None) -> str:
    value = str(code or "").strip().lower()
    value = "uk" if value == "gb" else value
    if value not in MARKETS:
        raise ValueError(f"Unknown Rakuten TV country '{code}'. Known: {', '.join(MARKETS)}")
    return value


def default_market() -> str:
    from VibraVid.core.ui.tracker import context_tracker

    country = (context_tracker.site_options or {}).get("country")
    if not country:
        country = config_manager.login.get_section("rakutentv").get("country")
    return normalize_market(country) if country else DEFAULT_MARKET


def resolve_market(market: str | None = None) -> str:
    return normalize_market(market) if market else default_market()


def locale_for(market: str) -> str:
    return "en" if market in ("uk", "ie") else market


def classification_id(market: str | None = None) -> str:
    market = resolve_market(market)
    if market in _classification_cache:
        return _classification_cache[market]

    cached = disk_cache.load("rakutentv", f"classification_{market}")
    if cached and cached.get("id") and disk_cache.is_fresh(cached):
        _classification_cache[market] = str(cached["id"])
        return _classification_cache[market]

    try:
        params = {"device_identifier": "web", "locale": locale_for(market), "market_code": market}
        entries = _get("/classifications", params).get("data") or []
        chosen = next((e for e in entries if e.get("default")), None) or (entries[-1] if entries else None)
        value = str(chosen["numerical_id"])
    except Exception as e:
        if market == DEFAULT_MARKET:
            logger.warning(f"Could not read the classification of '{market}' ({e}), using {CLASSIFICATION_IT}")
            return CLASSIFICATION_IT
        raise RuntimeError(f"Could not read the Rakuten TV classification for country '{market}': {e}") from e

    _classification_cache[market] = value
    disk_cache.save("rakutentv", f"classification_{market}", {"id": value, "expiry": time.time() + _CLASSIFICATION_TTL})
    return value


def audio_language(market: str | None = None, detail: dict | None = None, requested: str | None = None) -> str:
    if requested:
        return requested.upper()

    market = resolve_market(market)
    wanted = MARKET_AUDIO.get(market)
    offered: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "audio_languages" and isinstance(value, list):
                    offered.extend(str(v.get("id")) for v in value if isinstance(v, dict) and v.get("id"))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk((detail or {}).get("view_options"))
    if wanted and (not offered or wanted in offered):
        return wanted
    return offered[0] if offered else (wanted or "ENG")


def _api_headers(content_type: str | None = None) -> dict:
    headers = get_headers()
    headers["Origin"] = ORIGIN
    headers["Referer"] = REFERER
    headers["User-Agent"] = get_userAgent()
    headers["Accept"] = "application/json, text/plain, */*"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _base_params(extra: dict | None = None, market: str | None = None) -> dict:
    market = resolve_market(market)
    params = {
        "classification_id": classification_id(market),
        "device_identifier": "web",
        "device_stream_audio_quality": "2.0",
        "device_stream_hdr_type": "NONE",
        "device_stream_video_quality": "FHD",
        "locale": locale_for(market),
        "market_code": market,
    }
    if extra:
        params.update(extra)
    return params


def _get(path: str, params: dict | None = None) -> dict:
    with create_client(headers=_api_headers()) as client:
        response = client.get(f"{GIZMO}{path}", params=params or _base_params())
    response.raise_for_status()
    return response.json()


def _post_json(path: str, params: dict, body: dict) -> dict:
    with create_client(headers=_api_headers("application/json")) as client:
        response = client.post(f"{GIZMO}{path}", params=params, json=body)
    response.raise_for_status()
    return response.json()


def search_endpoint(query: str, endpoint: str, per_page: int = 20, market: str | None = None) -> list[dict]:
    params = _base_params(
        {
            "page": "1",
            "per_page": str(per_page),
            "personalization_consent": "true",
            "query": query,
            "search_engine": "external",
        },
        market,
    )
    data = _get(f"/{endpoint}", params)
    return data.get("data") or []


def search_live_channels(query: str, per_page: int = 50, pages: int = 2, market: str | None = None) -> list[dict]:
    needle = (query or "").strip().lower()
    out: list[dict] = []
    for page in range(1, pages + 1):
        params = _base_params({"page": str(page), "per_page": str(per_page)}, market)
        try:
            data = _get("/live_channels", params)
        except Exception as e:
            logger.warning(f"live_channels page {page} error: {e}")
            break
        items = data.get("data") or []
        if not items:
            break
        for item in items:
            title = str(item.get("title") or "").lower()
            if needle in title:
                out.append(item)
        meta = (data.get("meta") or {}).get("pagination") or {}
        if page >= int(meta.get("total_pages") or page):
            break
    return out


def _detail_params(market: str | None = None) -> dict:
    return _base_params({"disable_dash_legacy_packages": "false", "support_closed_captions": "true"}, market)


def get_movie_detail(movie_id: str, market: str | None = None) -> dict:
    return _get(f"/movies/{movie_id}", _detail_params(market)).get("data") or {}


def get_tvshow_detail(show_id: str, market: str | None = None) -> dict:
    return _get(f"/tv_shows/{show_id}", _detail_params(market)).get("data") or {}


def get_season_detail(season_id: str, market: str | None = None) -> dict:
    return _get(f"/seasons/{season_id}", _detail_params(market)).get("data") or {}


def purchase_kind(detail: dict) -> str:
    try:
        return detail["labels"]["purchase_types"][0]["kind"]
    except (KeyError, IndexError, TypeError):
        return "unknown"


def price_label(detail: dict) -> str:
    try:
        pts = detail["labels"]["purchase_types"]
        return " / ".join(f"{p.get('label', '')} {p.get('price', '')}".strip() for p in pts)
    except (KeyError, TypeError, AttributeError):
        return ""


def artwork(detail: dict) -> str:
    images = detail.get("images") or {}
    for key in ("artwork", "artwork_webp", "snapshot", "standard_artwork"):
        if images.get(key):
            return images[key]
    return ""


def get_avod_streaming(
    content_id: str,
    content_type: str = "movies",
    audio_language: str = "ITA",
    video_quality: str = "FHD",
    device: str = "lgui40",
    hdr_type: str = "NONE",
    session: dict | None = None,
    market: str | None = None,
) -> dict:
    """POST /avod/streamings -> first stream_infos entry."""
    market = resolve_market(market)
    classification = classification_id(market)
    player = DEVICES.get(device, DEVICES["lgui40"])["player"]
    params = {
        "classification_id": classification,
        "device_identifier": device,
        "device_stream_audio_quality": "2.0",
        "device_stream_hdr_type": hdr_type,
        "device_stream_video_quality": video_quality,
        "disable_dash_legacy_packages": "false",
        "locale": locale_for(market),
        "market_code": market,
    }

    body = {
        "audio_language": audio_language,
        "audio_quality": "2.0",
        "classification_id": int(classification),
        "content_id": content_id,
        "content_type": content_type,
        "device_serial": "not implemented",
        "device_uid": str(uuid.uuid4()),
        "publisher_provided_id": str(uuid.uuid4()),
        "player": player,
        "device_make": "chrome",
        "device_model": "GENERIC",
        "device_year": 1970,
        "strict_video_quality": False,
        "support_thumbnails": True,
        "hdr_type": hdr_type,
        "video_type": "stream",
        "subtitle_formats": ["vtt"],
        "subtitle_language": "MIS",
        "support_closed_captions": True,
    }

    if session:
        params = {
            "device_stream_video_quality": video_quality,
            "device_identifier": device,
            "market_code": market,
            "session_uuid": session["session_uuid"],
            "timestamp": f"{int(time.time())}122",
        }
        body = {
            "hdr_type": hdr_type,
            "audio_quality": "2.0",
            "app_version": APP_VERSION_LG,
            "content_id": content_id,
            "video_quality": video_quality,
            "audio_language": audio_language,
            "video_type": "stream",
            "device_serial": DEVICE_SERIAL_LG,
            "content_type": content_type,
            "classification_id": int(session.get("classification_id") or classification),
            "subtitle_language": "MIS",
            "player": player,
        }
        url = f"{GIZMO}/avod/streamings?{urllib.parse.urlencode(params)}"
        url += "&signature=" + _signature(session["access_token"], url)
        data = _post_json_url(url, body)
    else:
        data = _post_json("/avod/streamings", params, body)

    _raise_if_error(data, content_id)
    stream_infos = data.get("data", {}).get("stream_infos") or []
    if not stream_infos:
        raise RuntimeError(f"No stream_infos for '{content_id}' (keys: {list(data.get('data', {}).keys())})")
    return stream_infos[0]


def get_live_streaming(channel_id: str, audio_language: str = "ITA", market: str | None = None) -> dict:
    """Live/FAST channel -> HLS stream_infos entry (player web:HLS-NONE:NONE)."""
    market = resolve_market(market)
    params = _base_params({"disable_dash_legacy_packages": "false"}, market)
    body = {
        "audio_language": audio_language,
        "audio_quality": "2.0",
        "classification_id": int(classification_id(market)),
        "content_id": channel_id,
        "content_type": "live_channels",
        "device_serial": "not implemented",
        "device_uid": str(uuid.uuid4()),
        "publisher_provided_id": str(uuid.uuid4()),
        "player": "web:HLS-NONE:NONE",
        "device_make": "chrome",
        "device_model": "GENERIC",
        "device_year": 1970,
        "strict_video_quality": False,
        "hdr_type": "NONE",
        "video_type": "stream",
        "subtitle_language": "MIS",
    }
    data = _post_json("/avod/streamings", params, body)
    _raise_if_error(data, channel_id)
    stream_infos = data.get("data", {}).get("stream_infos") or []
    if not stream_infos:
        raise RuntimeError(f"No stream_infos for live channel '{channel_id}'")
    return stream_infos[0]


def _raise_if_error(data: dict, content_id: str) -> None:
    if isinstance(data, dict) and data.get("errors"):
        err = data["errors"][0]
        code = err.get("code", "")
        if "no_active_right" in code:
            raise RuntimeError(
                f"No rights for '{content_id}': rental/purchase required (login + owned title needed)."
            )
        raise RuntimeError(f"Streaming error for '{content_id}': {err.get('message')} [{code}]")


def _post_json_url(url: str, body: dict) -> dict:
    with create_client(headers=_api_headers("application/json")) as client:
        response = client.post(url, json=body)
    response.raise_for_status()
    return response.json()


def _signature(access_token: str, url: str, method: str = "POST") -> str:
    up = urllib.parse.urlparse(url)
    digester = hmac.new(access_token.encode(), f"{method}{up.path}{up.query}".encode(), hashlib.sha1)
    return base64.b64encode(digester.digest()).decode("utf-8").replace("+", "-").replace("/", "_")


def _credentials() -> tuple[str | None, str | None]:
    login_cfg = config_manager.login.get_section("rakutentv")
    email = (login_cfg.get("email") or "").strip() or None
    password = login_cfg.get("password") or None
    return email, password


def login(force: bool = False, market: str | None = None) -> dict:
    """Login via /me/login_or_wuaki_link. Cached on disk (~12h), one session per country."""
    market = resolve_market(market)
    cache_name = "session" if market == DEFAULT_MARKET else f"session_{market}"
    if not force:
        cached = disk_cache.load("rakutentv", cache_name)
        if cached and cached.get("access_token") and disk_cache.is_fresh(cached, buffer_seconds=60):
            return cached

    email, password = _credentials()
    if not email or not password:
        raise RuntimeError("Rakuten TV email/password not set in Conf/login.json under 'rakutentv'.")

    params = _base_params(market=market)
    body = {
        "user": {"password": password, "username": email},
        "remote_pairing_activation_code": "",
        "device_identifier": "web",
        "device_metadata": {
            "app_version": "v5.5.356",
            "audio_quality": "2.0",
            "brand": "edge-chromium",
            "firmware": "XX.XX.XX",
            "hdr": False,
            "model": "GENERIC",
            "os": "Windows 10",
            "sdk": "154.0.0",
            "serial_number": "not implemented",
            "trusted_uid": False,
            "uid": str(uuid.uuid4()),
            "video_quality": "FHD",
            "year": 1970,
        },
        "ifa_id": str(uuid.uuid4()),
    }
    data = _post_json("/me/login_or_wuaki_link", params, body)
    if data.get("errors"):
        err = data["errors"][0]
        raise RuntimeError(f"Rakuten TV login failed: {err.get('message')} [{err.get('code')}]")

    user = data["data"]["user"]
    market_info = data["data"].get("market") or {}
    print_login(ACCOUNT, user=user.get("email") or email)
    session = {
        "access_token": user["access_token"],
        "session_uuid": user["session_uuid"],
        "classification_id": str(user.get("profile", {}).get("classification", {}).get("id") or classification_id(market)),
        "locale": market_info.get("locale") or locale_for(market),
        "market_code": market_info.get("code") or market,
        "expiry": time.time() + 12 * 3600,
    }
    disk_cache.save("rakutentv", cache_name, session)
    return session


def get_me_streaming(
    content_id: str,
    content_type: str = "movies",
    audio_language: str = "ITA",
    video_quality: str = "FHD",
    device: str = "lgui40",
    hdr_type: str = "NONE",
    market: str | None = None,
) -> dict:
    """POST /me/streamings (signed) for owned/rented titles. Requires login."""
    session = login(market=market)
    player = DEVICES.get(device, DEVICES["lgui40"])["player"]
    params = {
        "audio_language": audio_language,
        "audio_quality": "2.0",
        "classification_id": session["classification_id"],
        "content_id": content_id,
        "content_type": content_type,
        "device_identifier": device,
        "device_serial": "not_implemented",
        "device_stream_audio_quality": "2.0",
        "device_stream_hdr_type": hdr_type,
        "device_stream_video_quality": video_quality,
        "device_uid": str(uuid.uuid4()),
        "device_year": "2021",
        "disable_dash_legacy_packages": "false",
        "locale": session["locale"],
        "market_code": session["market_code"],
        "player": player,
        "player_height": "1080",
        "player_width": "1920",
        "session_uuid": session["session_uuid"],
        "strict_video_quality": "false",
        "subtitle_language": "MIS",
        "timestamp": f"{int(time.time())}122",
        "video_type": "stream",
    }
    url = f"{GIZMO}/me/streamings?{urllib.parse.urlencode(params)}"
    url += "&signature=" + _signature(session["access_token"], url)
    data = _post_json_url(url, {})
    _raise_if_error(data, content_id)
    stream_infos = data.get("data", {}).get("stream_infos") or []
    if not stream_infos:
        raise RuntimeError(f"No stream_infos (me) for '{content_id}'")
    return stream_infos[0]


def playready_license_headers(streaming_uuid: str) -> dict:
    """Headers for prod-playready license POST (challenge bytes as body)."""
    customdata = base64.b64encode(streaming_uuid.encode("utf-8")).decode("utf-8")
    return {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": get_userAgent(),
        "Content-Type": "text/xml; charset=utf-8",
        "soapaction": '"http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense"',
        "http-header-customdata": customdata,
    }


def widevine_license_headers() -> dict:
    return {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": get_userAgent(),
        "Content-Type": "application/octet-stream",
    }


def mpd_headers() -> dict:
    return {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": get_userAgent(),
        "Accept": "*/*",
    }


def streaming_uuid(stream_info: dict) -> str:
    for key in ("wrid", "id"):
        value = stream_info.get(key)
        if value:
            return str(value)
    
    license_url = stream_info.get("license_url") or ""
    match = urllib.parse.urlparse(license_url)
    qs = urllib.parse.parse_qs(match.query)
    if qs.get("uuid"):
        return qs["uuid"][0]
    return ""


def is_playready_license(license_url: str) -> bool:
    return "playready" in (license_url or "").lower()
