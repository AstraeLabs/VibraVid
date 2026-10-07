# 07.10.26

import logging
import re
import time

from rich.console import Console

from VibraVid.player.vixcloud import VideoSource
from VibraVid.services._base import site_constants
from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)
console = Console()

EMBED_HOST = "https://embed.vidrift.net"
_PRIMARY_PROVIDER = "moviebox"
_REMAINING_PROVIDERS = ["vaplayer", "vidlove", "vidrock", "castle"]
_FALLBACK_PROVIDERS = [_PRIMARY_PROVIDER, *_REMAINING_PROVIDERS]
_PROVIDER_RETRY_DELAY = 1.0
_MIN_PREFERRED_HEIGHT = 1080
_PROVIDER_KIND = {
    "bingr": "embed",
    "moviebox": "direct",
    "vaplayer": "embed",
    "vidlove": "embed",
    "vidrock": "embed",
    "castle": "embed",
    "evion": "embed",
    "selfhost": "direct",
}

BINGR_API = "https://api.bingr.one"
VIXSRC_API = "https://vixsrc.to"
_AUDIO_LANG_RE = re.compile(r'#EXT-X-MEDIA:TYPE=AUDIO[^\n]*LANGUAGE="([a-z]{3})"[^\n]*URI="([^"]+)"')
_RESOLUTION_RE = re.compile(r"RESOLUTION=\d+x(\d+)")


def base_url() -> str:
    return site_constants.FULL_URL


def _get_playback_token(tmdb_id: int, media_type: str, season: int | None, episode: int | None) -> str:
    """Mint a playback token from 7movies' own API (requires Origin/Referer, no Cloudflare challenge)."""
    base = base_url()
    watch_path = f"/{media_type}/{tmdb_id}/watch"
    body = {"tmdbId": int(tmdb_id), "type": media_type}
    if media_type == "tv":
        watch_path += f"?season={season}&episode={episode}"
        body["season"] = int(season)
        body["episode"] = int(episode)

    headers = {
        "user-agent": get_userAgent(),
        "origin": base,
        "referer": f"{base}{watch_path}",
    }

    with create_client(headers=headers) as client:
        response = client.post(f"{base}/api/playback-token", json=body)
    response.raise_for_status()

    token = response.json().get("token")
    if not token:
        raise RuntimeError(f"[7Movies] No playback token returned for tmdb_id={tmdb_id}")
    return token


def _abs_embed_url(url: str) -> str:
    return url if url.startswith("http") else f"{EMBED_HOST}{url}"


def _infer_type(url: str, explicit: str | None = None) -> str:
    """Infer the stream type from the URL or an explicit type if provided."""
    if explicit:
        return explicit
    return "hls" if ".m3u8" in url else "mp4"


def _source_path(tmdb_id: int, media_type: str, season: int | None, episode: int | None) -> str:
    if media_type == "tv":
        return f"tv/{tmdb_id}/{season}/{episode}"
    return f"movie/{tmdb_id}"


def _embed_headers() -> dict:
    return {
        "user-agent": get_userAgent(),
        "x-embed-parent": base_url(),
        "referer": f"{EMBED_HOST}/embed2/play",
    }


def _try_server(name: str) -> None:
    kind = _PROVIDER_KIND.get(name, "embed")
    console.print(f"[dim]Try server: {name} ({kind})[/dim]")


def _manifest_text(url: str) -> str:
    """Fetch an HLS manifest's raw text, or "" on any failure."""
    try:
        with create_client(headers={"user-agent": get_userAgent()}, timeout=8) as client:
            response = client.get(url)
        return response.text if response.ok else ""
    except Exception as e:
        logger.debug(f"[7Movies] Could not fetch manifest {url}: {e}")
        return ""


def _manifest_max_height(text: str) -> int:
    heights = [int(h) for h in _RESOLUTION_RE.findall(text)]
    return max(heights) if heights else 0


def _manifest_has_italian(text: str) -> bool:
    return any(lang == "ita" for lang, _ in _AUDIO_LANG_RE.findall(text))


def _boot(tmdb_id: int, media_type: str, season: int | None, episode: int | None) -> dict:
    """Boot the 7Movies embed API to get a playback token and meta data for a movie or episode."""
    token = _get_playback_token(tmdb_id, media_type, season, episode)
    path = _source_path(tmdb_id, media_type, season, episode)
    params = {"token": token, "type": media_type, "id": tmdb_id, "shell": "1"}
    if media_type == "tv":
        params["season"] = season
        params["episode"] = episode

    with create_client(headers=_embed_headers()) as client:
        response = client.get(f"{EMBED_HOST}/api/boot/{path}", params=params)
    response.raise_for_status()
    return response.json()


def _fetch_provider_stream(
    token: str, tmdb_id: int, media_type: str, season: int | None, episode: int | None, provider: str
) -> list[dict]:
    """Fetch playable candidates from a specific provider: [{"url","type","height","has_ita"}, ...]."""
    path = _source_path(tmdb_id, media_type, season, episode)
    params = {"token": token, "provider": provider}

    with create_client(headers=_embed_headers()) as client:
        response = client.get(f"{EMBED_HOST}/api/source/{path}", params=params)
    if not response.ok:
        return []

    try:
        data = response.json()
    except ValueError:
        return []

    streams = data.get("streams") or []
    usable = [
        s for s in streams
        if (s.get("name") or "").strip().lower().endswith("original") or "italian" in (s.get("name") or "").lower()
    ] or streams

    candidates = []
    for stream in usable:
        url = stream.get("url") or stream.get("proxyUrl")
        if not url:
            continue
        url = _abs_embed_url(url)
        rungs = stream.get("rungs") or []
        height = max((r.get("height", 0) for r in rungs), default=0)
        candidates.append({
            "url": url,
            "type": _infer_type(url, stream.get("type")),
            "height": height,
            "has_ita": "italian" in (stream.get("name") or "").lower(),
        })
    return candidates


def _fetch_bingr_stream(tmdb_id: int, media_type: str, season: int | None, episode: int | None) -> tuple[str, str] | None:
    """Fetch a playable stream (url, type) from bingr.one's API, which bundles all dubs into one HLS manifest for TV."""
    if media_type != "tv":
        return None

    url = f"{BINGR_API}/api/stream/aphelion-tv/{tmdb_id}/{season}/{episode}"
    try:
        with create_client(headers={"user-agent": get_userAgent()}, timeout=8) as client:
            response = client.get(url)
        if not response.ok:
            return None
        data = response.json()
    except Exception as e:
        logger.debug(f"[7Movies] bingr.one lookup failed, falling back to vidrift: {e}")
        return None

    for source in data.get("sources") or []:
        stream_url = source.get("url")
        if stream_url:
            return stream_url, _infer_type(stream_url)
    return None


def _resolve_stream(token: str, tmdb_id: int, media_type: str, season, episode, meta: dict) -> tuple[str, str] | None:
    """Resolve a playable stream (url, type)."""
    pool: list[dict] = []

    if media_type == "tv":
        _try_server("bingr")
    bingr_result = _fetch_bingr_stream(tmdb_id, media_type, season, episode)
    if bingr_result:
        bingr_url, bingr_type = bingr_result
        text = _manifest_text(bingr_url) if bingr_type == "hls" else ""
        pool.append({
            "url": bingr_url, "type": bingr_type,
            "height": _manifest_max_height(text), "has_ita": _manifest_has_italian(text),
        })

    _try_server("moviebox")
    moviebox_candidates = _fetch_provider_stream(token, tmdb_id, media_type, season, episode, _PRIMARY_PROVIDER)
    pool.extend(moviebox_candidates)

    tier1 = [c for c in pool if c["height"] >= _MIN_PREFERRED_HEIGHT and c["has_ita"]]
    tier2 = [c for c in pool if c["height"] >= _MIN_PREFERRED_HEIGHT]
    best = max(tier1, key=lambda c: c["height"]) if tier1 else (max(tier2, key=lambda c: c["height"]) if tier2 else None)
    if best:
        return best["url"], best["type"]

    # Nothing reached 1080p: same fallback order/behaviour as before.
    if bingr_result:
        return bingr_result
    if moviebox_candidates:
        return moviebox_candidates[0]["url"], moviebox_candidates[0]["type"]

    evion_url = meta.get("evionUrl")
    if evion_url:
        _try_server("evion")
        return evion_url, _infer_type(evion_url)

    for i, provider in enumerate(_REMAINING_PROVIDERS):
        if i > 0:
            time.sleep(_PROVIDER_RETRY_DELAY)
        _try_server(provider)
        candidates = _fetch_provider_stream(token, tmdb_id, media_type, season, episode, provider)
        if candidates:
            return candidates[0]["url"], candidates[0]["type"]

    selfhost_url = meta.get("selfhostUrl")
    if selfhost_url:
        _try_server("selfhost")
        return _abs_embed_url(selfhost_url), _infer_type(selfhost_url, meta.get("selfhostKind"))

    return None


def _fetch_vixsrc_italian_audio(tmdb_id: int, media_type: str, season: int | None, episode: int | None) -> str | None:
    """The Italian audio-track URL from vixsrc.to's own master playlist, or None."""
    try:
        tmdb_data = {"id": tmdb_id}
        if media_type == "tv":
            tmdb_data["s"], tmdb_data["e"] = season, episode
        video_source = VideoSource(VIXSRC_API, media_type == "tv", tmdb_data=tmdb_data)
        video_source._resolve_tmdb_embed_url()
        if not video_source.iframe_src:
            return None
        video_source.get_content()
        master_url = video_source.get_playlist()
        if not master_url:
            return None

        with create_client(headers={"user-agent": get_userAgent()}, timeout=8) as client:
            response = client.get(master_url)
        if not response.ok:
            return None
    except Exception as e:
        logger.debug(f"[7Movies] vixsrc.to italian-audio lookup failed: {e}")
        return None

    for lang, uri in _AUDIO_LANG_RE.findall(response.text):
        if lang == "ita":
            return uri
    return None


def _italian_audio_track(tmdb_id: int, media_type: str, season, episode, manifest_url: str | None) -> dict | None:
    """Return a dict with the Italian audio track from vixsrc.to if the resolved stream doesn't already
    carry one, else None. manifest_url is None for a direct mp4 (no manifest to inspect -- always try vixsrc)."""
    if manifest_url and _manifest_has_italian(_manifest_text(manifest_url)):
        return None

    audio_url = _fetch_vixsrc_italian_audio(tmdb_id, media_type, season, episode)
    if not audio_url:
        return None

    return {"type": "audio", "url": audio_url, "language": "ita", "name": "Italian (vixsrc)"}


def get_stream(tmdb_id: int, media_type: str, season: int | None = None, episode: int | None = None):
    """Get a playable stream URL, headers, type, and any extra tracks for a movie or episode from 7Movies."""
    if media_type == "tv":
        season, episode = season or 1, episode or 1

    payload = _boot(tmdb_id, media_type, season, episode)
    meta = payload.get("meta") or {}
    token = meta.get("playbackToken")
    result = _resolve_stream(token, tmdb_id, media_type, season, episode, meta)

    if not result:
        raise RuntimeError(
            f"[7Movies] No playable stream found for tmdb_id={tmdb_id} "
            f"after trying evion + {', '.join(_FALLBACK_PROVIDERS)}"
        )

    stream_url, stream_type = result
    stream_headers = {"user-agent": get_userAgent(), "referer": f"{EMBED_HOST}/embed2/play"}

    extra_tracks = []
    italian_track = _italian_audio_track(
        tmdb_id, media_type, season, episode, stream_url if stream_type == "hls" else None
    )
    if italian_track:
        extra_tracks.append(italian_track)

    return stream_url, stream_headers, stream_type, extra_tracks
