# 07.10.26

import logging
import re
import time

from VibraVid.player.vixcloud import VideoSource
from VibraVid.services._base import site_constants
from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

EMBED_HOST = "https://embed.vidrift.net"
_PRIMARY_PROVIDER = "moviebox"
_REMAINING_PROVIDERS = ["vaplayer", "vidlove", "vidrock", "castle"]
_FALLBACK_PROVIDERS = [_PRIMARY_PROVIDER, *_REMAINING_PROVIDERS]
_PROVIDER_RETRY_DELAY = 1.0
BINGR_API = "https://api.bingr.one"
VIXSRC_API = "https://vixsrc.to"
_AUDIO_LANG_RE = re.compile(r'#EXT-X-MEDIA:TYPE=AUDIO[^\n]*LANGUAGE="([a-z]{3})"[^\n]*URI="([^"]+)"')


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
) -> tuple[str, str] | None:
    """Fetch a playable stream (url, type) from a specific provider."""
    path = _source_path(tmdb_id, media_type, season, episode)
    params = {"token": token, "provider": provider}

    with create_client(headers=_embed_headers()) as client:
        response = client.get(f"{EMBED_HOST}/api/source/{path}", params=params)
    if not response.ok:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    for stream in data.get("streams") or []:
        url = stream.get("url") or stream.get("proxyUrl")
        if url:
            url = _abs_embed_url(url)
            return url, _infer_type(url, stream.get("type"))
    return None


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
    """Resolve a playable stream (url, type) from the embed API, trying bingr first, then the primary provider,"""
    result = _fetch_bingr_stream(tmdb_id, media_type, season, episode)
    if result:
        return result

    result = _fetch_provider_stream(token, tmdb_id, media_type, season, episode, _PRIMARY_PROVIDER)
    if result:
        return result

    evion_url = meta.get("evionUrl")
    if evion_url:
        return evion_url, _infer_type(evion_url)

    for i, provider in enumerate(_REMAINING_PROVIDERS):
        if i > 0:
            time.sleep(_PROVIDER_RETRY_DELAY)
        result = _fetch_provider_stream(token, tmdb_id, media_type, season, episode, provider)
        if result:
            return result

    selfhost_url = meta.get("selfhostUrl")
    if selfhost_url:
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


def _italian_audio_track(tmdb_id: int, media_type: str, season, episode, manifest_url: str) -> dict | None:
    """Return a dict with the Italian audio track from vixsrc.to if the stream's own manifest has none, else None."""
    try:
        with create_client(headers={"user-agent": get_userAgent()}, timeout=8) as client:
            response = client.get(manifest_url)
        if response.ok and any(lang == "ita" for lang, _ in _AUDIO_LANG_RE.findall(response.text)):
            return None
    except Exception as e:
        logger.debug(f"[7Movies] Could not inspect manifest for an existing Italian audio track: {e}")
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
    if stream_type == "hls":
        italian_track = _italian_audio_track(tmdb_id, media_type, season, episode, stream_url)
        if italian_track:
            extra_tracks.append(italian_track)

    return stream_url, stream_headers, stream_type, extra_tracks
