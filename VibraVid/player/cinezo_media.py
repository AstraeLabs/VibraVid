# 29.09.26

from typing import Any

from VibraVid.player.cinezo import CinezoSourceProbe
from VibraVid.utils.http_client import create_client, get_userAgent

PLAYER_REFERER = "https://player.cinezo.live/"


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


def _stream_url_from_payload(data: dict[str, Any]) -> str:
    """Extract the playable media URL from the current Cinezo backend payload."""
    source = data.get("source")
    if isinstance(source, dict) and source.get("url"):
        return str(source["url"]).strip()

    stream = data.get("stream")
    if isinstance(stream, dict):
        for key in ("original", "hls", "url"):
            if stream.get(key):
                return str(stream[key]).strip()

    if data.get("url"):
        return str(data["url"]).strip()

    return ""


def _headers_from_payload(data: dict[str, Any]) -> dict[str, str]:
    """Extract optional playback headers from backend payloads."""
    for key in ("headers", "requestHeaders", "playback_headers"):
        headers = data.get(key)
        if isinstance(headers, dict):
            return headers

    source = data.get("source")
    if isinstance(source, dict):
        headers = source.get("headers")
        if isinstance(headers, dict):
            return headers

    stream = data.get("stream")
    if isinstance(stream, dict):
        headers = stream.get("headers")
        if isinstance(headers, dict):
            return headers

    return {}


def _subtitles_from_payload(data: dict[str, Any]) -> list[dict]:
    """Extract subtitle metadata from backend payloads."""
    for key in ("subtitles", "tracks", "captions"):
        items = data.get(key)
        if isinstance(items, list):
            return items

    return []


def _fetch_source_payload(source: CinezoSourceProbe) -> dict[str, Any]:
    """Fetch and validate the selected Cinezo source endpoint payload."""
    headers = _default_headers()

    with create_client(headers=headers) as client:
        response = client.get(source.endpoint, timeout=30)

    if not response.ok:
        raise RuntimeError(
            f"[Cinezo] Source backend returned HTTP {response.status_code}: {source.name}"
        )

    try:
        data = response.json()
    except Exception as error:
        raise RuntimeError(
            f"[Cinezo] Source backend returned invalid JSON: {source.name}"
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError(
            f"[Cinezo] Source backend returned unexpected payload: {type(data).__name__}"
        )

    return data


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

    payload = _fetch_source_payload(source)
    stream_url = _stream_url_from_payload(payload)
    playback_headers = _headers_from_payload(payload)
    subtitle_items = _subtitles_from_payload(payload)

    return _build_result(
        stream_url,
        playback_headers,
        subtitle_items,
    )
