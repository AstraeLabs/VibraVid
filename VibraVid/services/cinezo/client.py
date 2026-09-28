# 28.09.26

import logging

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

PLAYER_BASE = "https://player.cinezo.live"
REFERER = "https://cinezo.live/"


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


def get_stream(tmdb_id: int, media_type: str, season: int | None = None, episode: int | None = None):
    """Resolve a downloadable media source for Cinezo."""
    player_url = get_player_url(tmdb_id, media_type, season, episode)

    # TODO: implement media source resolution from the current Cinezo player.
    # The old api.cinezo.live /sources backend is no longer used by the public
    # player and now returns HTML instead of the JSON contract this provider
    # previously consumed.
    raise RuntimeError(
        f"[Cinezo] Media source resolution is not implemented for the current player: {player_url}"
    )
