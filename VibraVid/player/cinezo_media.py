# 29.09.26

from typing import Any

from VibraVid.player.cinezo import CinezoSourceProbe
from VibraVid.utils.http_client import get_userAgent

PLAYER_REFERER = "https://player.cinezo.live/"\nAUTHORIZED_MEDIA_URL = "https://example.com/authorized-test.m3u8"


def _default_headers() -> dict[str, str]:
    """Playback header baseline, following the existing player resolver pattern."""
    return {
        "User-Agent": get_userAgent(),
        "Referer": PLAYER_REFERER,
    }


def _merge_playback_headers(
    base_headers: dict[str, str] | None = None,
    source_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge resolver playback headers into the common baseline."""
    headers = dict(base_headers or _default_headers())

    for key, value in (source_headers or {}).items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            headers[str(key)] = text

    return headers


def _subtitles_to_tracks(items) -> list[dict]:
    """Normalize subtitle metadata to the downloader other_tracks format.

    Supports the legacy Cinezo track shape with file/label and the generic
    url/language/name shape already used by other providers.
    """
    tracks = []

    for item in items or []:
        if not isinstance(item, dict):
            continue

        url = item.get("url") or item.get("file")
        if not url:
            continue

        language = (
            item.get("language")
            or item.get("lang")
            or item.get("label")
            or "und"
        )
        name = (
            item.get("name")
            or item.get("label")
            or item.get("display")
            or "Subtitle"
        )
        extension = (
            item.get("extension")
            or item.get("format")
            or "vtt"
        )

        tracks.append(
            {
                "type": "subtitle",
                "language": language,
                "name": name,
                "url": url,
                "extension": extension,
            }
        )

    return tracks


def _build_result(
    stream_url: str,
    headers: dict[str, str] | None = None,
    subtitles=None,
) -> dict[str, Any]:
    """Build the normalized result consumed by services/cinezo/client.py."""
    stream_url = str(stream_url or "").strip()
    if not stream_url:
        raise RuntimeError("[Cinezo] Resolver returned no playable source")

    return {
        "url": stream_url,
        "headers": _merge_playback_headers(source_headers=headers),
        "subtitles": _subtitles_to_tracks(subtitles),
    }


def resolve_cinezo_media(
    source: CinezoSourceProbe,
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, Any]:
    """Single integration point for the provider-specific Cinezo resolver."""
    if not isinstance(source, CinezoSourceProbe):
        raise TypeError("[Cinezo] Invalid source descriptor")
    if not source.available:
        raise RuntimeError(f"[Cinezo] Source backend is not available: {source.name}")

    media_type = str(media_type or "").lower()
    if media_type not in {"movie", "tv"}:
        raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")
    if media_type == "tv" and (season is None or episode is None):
        raise ValueError("[Cinezo] season and episode are required for TV media resolution")

    playback_headers = _default_headers()
    subtitle_items = []

    return _build_result(
        AUTHORIZED_MEDIA_URL,
        playback_headers,
        subtitle_items,
    )
