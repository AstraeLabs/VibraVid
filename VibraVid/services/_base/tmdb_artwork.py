# 22.07.26

import logging

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)
_TV_MATCH_SITES = {
    "streamingcommunity",
    "eurostreaming",
    "altadefinizione",
    "cinezo",
    "tubitv",
    "animeunity",
    "animeworld",
    "crunchyroll",
    "appletv",
    "primevideo",
}


def _tv_matching_allowed(site_name: str | None) -> bool:
    """Whether TMDB series/episode matching is allowed for this site."""
    site_name = site_name or context_tracker.site_name
    return bool(site_name) and site_name.lower() in _TV_MATCH_SITES


def embed_enabled() -> bool:
    """Whether resolved artwork (TMDB, or the site's own poster as fallback) should be written into the downloaded file."""
    return config_manager.config.get_bool("DOWNLOAD", "embed_poster", default=True)


def _resolve_tmdb_id(
    media_type: str, tmdb_id=None, name: str | None = None, slug: str | None = None, year=None
) -> int | None:
    """Resolve a raw TMDB id: use tmdb_id if already known, else fall back to a slug+year lookup"""
    if not tmdb_client.api_key:
        return None

    if tmdb_id:
        try:
            return int(tmdb_id)
        except (TypeError, ValueError):
            return None

    slug = (tmdb_client._slugify(name) if name else None) or slug
    if not slug:
        return None

    result = tmdb_client.get_type_and_id_by_slug_year(slug, str(year) if year else None, media_type)
    if result and result.get("type") == media_type and result.get("id"):
        return result["id"]

    return None


def resolve_movie_poster_url(
    tmdb_id=None, name: str | None = None, slug: str | None = None, year=None
) -> str | None:
    """Resolve a movie's poster URL, service-agnostic (works from just name+year if no tmdb_id is known)."""
    resolved = _resolve_tmdb_id("movie", tmdb_id, name, slug, year)
    return tmdb_client.get_poster_url("movie", resolved) if resolved else None


def resolve_series_tmdb_id(
    tmdb_id=None, name: str | None = None, slug: str | None = None, year=None, site_name: str | None = None
) -> int | None:
    """Resolve a series' raw TMDB id, to be reused for every episode's artwork lookup."""
    if tmdb_id:
        try:
            return int(tmdb_id)
        except (TypeError, ValueError):
            pass
    if not _tv_matching_allowed(site_name):
        return None
    return _resolve_tmdb_id("tv", tmdb_id, name, slug, year)


def resolve_episode_artwork_url(series_tmdb_id, season_number, episode_number) -> str | None:
    """Episode still -> season poster -> series poster fallback chain."""
    if not series_tmdb_id:
        return None
    season_number, episode_number = tmdb_client.resolve_actual_season_episode(
        series_tmdb_id, season_number, episode_number
    )
    return tmdb_client.get_episode_artwork_url(series_tmdb_id, season_number, episode_number)


def resolve_series_poster_url(series_tmdb_id: int | None) -> str | None:
    """Poster URL for an already-resolved series id (see resolve_series_tmdb_id)."""
    return tmdb_client.get_poster_url("tv", series_tmdb_id) if series_tmdb_id else None


def resolve_backdrop_url(
    tmdb_id=None, name: str | None = None, slug: str | None = None, year=None,
    is_movie: bool = False, site_name: str | None = None, size: str = "w1280",
) -> str | None:
    """Backdrop URL for a movie or series, service-agnostic (works from just name+year if no tmdb_id is known)."""
    if is_movie:
        media_type, resolved = "movie", _resolve_tmdb_id("movie", tmdb_id, name, slug, year)
    else:
        media_type, resolved = "tv", resolve_series_tmdb_id(tmdb_id, name, slug, year, site_name)

    return tmdb_client.get_backdrop_url(media_type, resolved, size) if resolved else None


def resolve_season_artwork(
    series_tmdb_id, season_number, size: str = "w780"
) -> tuple[dict[int, str], dict[int, dict], str | None]:
    """Still URLs and episode info for a season, plus a fallback poster URL if no stills are found."""
    if not series_tmdb_id:
        return {}, {}, None

    details = tmdb_client._make_request(f"tv/{series_tmdb_id}/season/{season_number}", {"language": "it"})

    stills: dict[int, str] = {}
    info: dict[int, dict] = {}
    for episode in details.get("episodes") or []:
        number = episode.get("episode_number")
        if number is None:
            continue
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue

        url = tmdb_client._image_url(episode.get("still_path"), size)
        if url:
            stills[number] = url

        name = (episode.get("name") or "").strip()
        overview = (episode.get("overview") or "").strip()
        if name or overview:
            info[number] = {"name": name, "overview": overview}

    fallback = tmdb_client._image_url(details.get("poster_path"), size) or tmdb_client.get_poster_url("tv", series_tmdb_id)
    return stills, info, fallback
