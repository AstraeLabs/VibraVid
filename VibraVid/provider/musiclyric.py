# 19.08.26

import logging
import re

from rich.console import Console

from VibraVid.utils.http_client import create_client

console = Console()
logger = logging.getLogger(__name__)

LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
BINILYRICS_URL = "https://lyrics-api.binimum.org/"
GENIUS_URL = "https://fetch-genius.samidy.workers.dev/"
_REQUEST_TIMEOUT = 10


def _ttml_begin_to_lrc_tag(begin: str) -> str | None:
    """Convert a TTML `begin` attribute (`"27.395"` seconds, or `"01:27.395"` mm:ss) to an `[mm:ss.xx]` LRC tag."""
    match = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", begin)
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = float(match.group(2))
    minutes += int(seconds // 60)
    seconds = seconds % 60
    return f"[{minutes:02d}:{seconds:05.2f}]"


def _ttml_to_lrc(ttml_text: str) -> str | None:
    """Extract `<p begin="...">text</p>` lines from TTML into LRC text."""
    lines = re.findall(r'<p\b[^>]*\bbegin="([^"]+)"[^>]*>(.*?)</p>', ttml_text, re.DOTALL)
    lrc_lines = []
    for begin, inner in lines:
        text = re.sub(r"<[^>]+>", "", inner).strip()
        tag = _ttml_begin_to_lrc_tag(begin)
        if text and tag:
            lrc_lines.append(f"{tag}{text}")
    return "\n".join(lrc_lines) if lrc_lines else None


def _fetch_from_lrclib(title: str, artist: str, album: str = "", duration_seconds: int | None = None) -> dict | None:
    """Query LRCLIB (free, no auth) by track+artist. Returns synced LRC text when available."""
    try:
        params = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration_seconds:
            params["duration"] = str(duration_seconds)

        with create_client() as client:
            response = client.get(LRCLIB_SEARCH_URL, params=params, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        results = response.json()
    except Exception as e:
        logger.debug(f"[musiclyric] LRCLIB search failed for '{artist} - {title}': {e}")
        return None

    if not isinstance(results, list) or not results:
        return None

    best = next((r for r in results if r.get("syncedLyrics")), results[0])
    synced = best.get("syncedLyrics")
    plain = best.get("plainLyrics")
    if not synced and not plain:
        return None

    return {
        "synced": bool(synced),
        "lyrics": synced or plain,
        "source": "LRCLIB",
    }


def _fetch_from_binilyrics(title: str, artist: str, isrc: str | None = None) -> dict | None:
    """Query BiniLyrics by ISRC (preferred) or track+artist. Returns TTML-derived synced lyrics."""
    try:
        params = {"isrc": isrc} if isrc else {"track": title, "artist": artist}

        with create_client() as client:
            response = client.get(BINILYRICS_URL, params=params, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.debug(f"[musiclyric] BiniLyrics lookup failed for '{artist} - {title}': {e}")
        return None

    results = data.get("results") or []
    if not results:
        return None

    lyrics_url = results[0].get("lyricsUrl")
    if not lyrics_url:
        return None

    try:
        with create_client() as client:
            ttml_response = client.get(lyrics_url, timeout=_REQUEST_TIMEOUT)
        ttml_response.raise_for_status()
    except Exception as e:
        logger.debug(f"[musiclyric] BiniLyrics TTML fetch failed: {e}")
        return None

    lrc_text = _ttml_to_lrc(ttml_response.text)
    if not lrc_text:
        return None

    return {"synced": True, "lyrics": lrc_text, "source": "BiniLyrics"}


def _fetch_from_genius(title: str, artist: str) -> dict | None:
    """Last-resort fallback: plain (unsynced) lyrics via a Genius scraper worker."""
    try:
        params = {"title": title, "artist": artist}
        with create_client() as client:
            response = client.get(GENIUS_URL, params=params, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.debug(f"[musiclyric] Genius fallback failed for '{artist} - {title}': {e}")
        return None

    text = data.get("lyrics") if isinstance(data, dict) else None
    if not text or not isinstance(text, str):
        return None

    return {"synced": False, "lyrics": text.strip(), "source": "Genius"}


def get_lyrics(
    title: str, artist: str, album: str = "", duration_seconds: int | None = None, isrc: str | None = None
) -> dict | None:
    """
    Look up lyrics for a track by title+artist (album/duration/isrc help disambiguate).

    Returns:
        {"synced": bool, "lyrics": str, "source": str} — `lyrics` is LRC-format
        text (`[mm:ss.xx]line`) when `synced` is True, plain newline-joined text otherwise.
    """
    if not title or not artist:
        return None

    fetchers = (
        lambda: _fetch_from_lrclib(title, artist, album, duration_seconds),
        lambda: _fetch_from_binilyrics(title, artist, isrc),
        lambda: _fetch_from_genius(title, artist),
    )
    for fetch in fetchers:
        result = fetch()
        if result:
            logger.info(f"[musiclyric] lyrics found via {result['source']} for '{artist} - {title}'")
            return result

    logger.info(f"[musiclyric] no lyrics found for '{artist} - {title}'")
    return None
