# 29.09.26

import logging
from collections.abc import Callable
from typing import Any

from VibraVid.player.cinezo import CinezoResolverChain, CinezoSourceProbe
from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

PLAYER_BASE = "https://player.cinezo.live"
REFERER = "https://cinezo.live/"

AuthorizedMediaResolver = Callable[
    [CinezoSourceProbe, int, str, int | None, int | None],
    tuple[str, dict[str, str] | None, list[dict] | None] | dict[str, Any] | None,
]


def get_player_url(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> str:
    """Build the current public Cinezo player URL for a TMDB entry."""
    media_type = str(media_type or "").lower()

    if media_type == "movie":
        return f"{PLAYER_BASE}/embed/movie/{int(tmdb_id)}"

    if media_type == "tv":
        if season is None or episode is None:
            raise ValueError("[Cinezo] season and episode are required for TV player URLs")
        return f"{PLAYER_BASE}/embed/tv/{int(tmdb_id)}/{int(season)}/{int(episode)}"

    raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")


def player_is_available(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> bool:
    """Return whether the current Cinezo public player page is reachable."""
    url = get_player_url(tmdb_id, media_type, season, episode)
    headers = {
        "user-agent": get_userAgent(),
        "referer": REFERER,
    }

    try:
        with create_client(headers=headers) as client:
            response = client.get(url, timeout=30)
    except Exception as error:
        logger.warning(f"[Cinezo] Player request failed for {url}: {error}")
        return False

    if not response.ok:
        logger.warning(f"[Cinezo] Player returned HTTP {response.status_code} for {url}")
        return False

    content_type = (response.headers.get("content-type") or "").lower()
    if "html" not in content_type:
        logger.warning(f"[Cinezo] Player returned unexpected content type {content_type or 'unknown'} for {url}")
        return False

    return True


def probe_sources(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
    resolver_chain: CinezoResolverChain | None = None,
) -> list[CinezoSourceProbe]:
    """Inspect current Cinezo source availability without returning media URLs."""
    chain = resolver_chain or CinezoResolverChain()
    return chain.probe_sources(
        tmdb_id=tmdb_id,
        media_type=media_type,
        season=season,
        episode=episode,
    )


def _normalize_authorized_stream(
    result: tuple[str, dict[str, str] | None, list[dict] | None] | dict[str, Any] | None,
) -> tuple[str, dict[str, str], list[dict]]:
    """Normalize an authorized resolver result for downloader.py."""
    if result is None:
        raise RuntimeError("[Cinezo] Authorized media resolver returned no stream")

    if isinstance(result, tuple):
        if len(result) != 3:
            raise RuntimeError("[Cinezo] Authorized media resolver returned an invalid tuple")
        stream_url, headers, subtitles = result
    elif isinstance(result, dict):
        stream_url = result.get("url")
        headers = result.get("headers")
        subtitles = result.get("subtitles")
    else:
        raise RuntimeError(
            f"[Cinezo] Authorized media resolver returned unsupported type: {type(result).__name__}"
        )

    stream_url = str(stream_url or "").strip()
    if not stream_url:
        raise RuntimeError("[Cinezo] Authorized media resolver returned an empty stream URL")

    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise RuntimeError("[Cinezo] Authorized media resolver returned invalid headers")

    if subtitles is None:
        subtitles = []
    if not isinstance(subtitles, list):
        raise RuntimeError("[Cinezo] Authorized media resolver returned invalid subtitles")

    return stream_url, dict(headers), [dict(track or {}) for track in subtitles]


def get_stream(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
    media_resolver: AuthorizedMediaResolver | None = None,
):
    """Resolve Cinezo playback information for the service downloader.

    Source discovery and fallback are handled here. The provider-specific step
    that obtains a playable media URL must be supplied through media_resolver
    when it is authorized for the source being used.
    """
    player_url = get_player_url(tmdb_id, media_type, season, episode)
    sources = probe_sources(tmdb_id, media_type, season, episode)
    available = [source for source in sources if source.available]

    if available:
        selected = available[0]

        if media_resolver is None:
            # TODO: provide an authorized resolver that converts the selected
            # backend into (url, headers, subtitle_tracks).
            raise RuntimeError(
                f"[Cinezo] Source backend available ({', '.join(source.name for source in available)}), "
                f"but media URL extraction is not implemented: {player_url}"
            )

        result = media_resolver(
            selected,
            int(tmdb_id),
            str(media_type),
            season,
            episode,
        )
        return _normalize_authorized_stream(result)

    errors = [f"{source.name}={source.error}" for source in sources if source.error]
    detail = f" ({'; '.join(errors)})" if errors else ""

    raise RuntimeError(
        f"[Cinezo] No source backend is currently available for: {player_url}{detail}"
    )
