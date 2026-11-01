# 01.11.26

import logging
import re
import threading

from VibraVid.utils import config_manager
from VibraVid.utils.media import anime_id_map

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_identity_cache: dict[tuple, tuple[str, int] | None] = {}
_YEAR_TOKEN = "%(series_year)"
_MAL_URL_RE = re.compile(r"myanimelist\.net/anime/(\d+)")
_ANILIST_URL_RE = re.compile(r"anilist\.co/anime/(\d+)")


def external_ids_from_html(html: str) -> tuple[str | None, str | None]:
    """MyAnimeList and AniList ids linked from a provider detail page."""
    mal = _MAL_URL_RE.search(html or "")
    anilist = _ANILIST_URL_RE.search(html or "")
    return (mal.group(1) if mal else None, anilist.group(1) if anilist else None)


def _tmdb_identity(tmdb_id) -> tuple[str, int] | None:
    from VibraVid.provider.tmdb import tmdb_client

    if not tmdb_client.api_key:
        return None
    title = tmdb_client.get_title(int(tmdb_id), "tv", "en-US")
    year = tmdb_client.get_first_air_year(int(tmdb_id))
    return (title, year) if title and year else None


def canonical_identity(*, mal_id=None, anilist_id=None, tmdb_id=None) -> tuple[str, int] | None:
    """(TMDB title, first-air year) of the show, or None when it cannot be resolved."""
    key = (str(mal_id or ""), str(anilist_id or ""), str(tmdb_id or ""))
    if not any(key):
        return None
    with _lock:
        if key in _identity_cache:
            return _identity_cache[key]

    identity = None
    try:
        resolved = tmdb_id or anime_id_map.resolve_tmdb_id("tv", mal_id=mal_id, anilist_id=anilist_id)
        if resolved:
            identity = _tmdb_identity(resolved)
    except Exception as exc:
        logger.warning(f"[anime_naming] could not resolve canonical title: {exc}")

    with _lock:
        _identity_cache[key] = identity
    return identity


def _format_has_year_token() -> bool:
    try:
        return _YEAR_TOKEN in (config_manager.config.get("OUTPUT", "episode_format") or "")
    except Exception:
        return False


def series_name_and_year(fallback_name: str, *, mal_id=None, anilist_id=None, tmdb_id=None) -> tuple[str, str | None]:
    """The ``(series_name, series_year)`` to hand to ``map_episode_path``.

    Without a resolvable TMDB identity the provider's own name is kept untouched. With one, the year
    goes in ``series_year`` when ``OUTPUT.episode_format`` has a ``%(series_year)`` token, otherwise it
    is appended to the name (``My Hero Academia (2016)``) so the folder is right with any format.
    """
    identity = canonical_identity(mal_id=mal_id, anilist_id=anilist_id, tmdb_id=tmdb_id)
    if not identity:
        return fallback_name, None
    title, year = identity
    if _format_has_year_token():
        return title, str(year)
    return f"{title} ({year})", None
