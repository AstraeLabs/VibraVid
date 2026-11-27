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
_LANG_SUFFIX_RE = re.compile(r"(?:\s*[\(\[]\s*(?:sub\s*[-_ ]?\s*)?ita\s*[\)\]]|[\s_-]+(?:sub[\s_-]+)?ita)\s*$", re.IGNORECASE)


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


def _imdb_identity(imdb_id) -> tuple[str, int] | None:
    """(title, first year) from IMDb, which needs no API key: the fallback when TMDB is not configured."""
    from VibraVid.provider.imdb import IMDbClient

    title = IMDbClient().get_title(str(imdb_id)) or {}
    name = (title.get("titleText") or {}).get("text")
    year = (title.get("releaseYear") or {}).get("year")
    return (name, int(year)) if name and year else None


def canonical_identity(*, mal_id=None, anilist_id=None, tmdb_id=None) -> tuple[str, int] | None:
    """(title, first-air year) of the whole show from TMDB, or from IMDb when TMDB has no key; None when unresolved."""
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

        if not identity and (mal_id or anilist_id):
            imdb_id = (anime_id_map.lookup(mal_id=mal_id, anilist_id=anilist_id) or {}).get("imdb_id")
            if imdb_id:
                identity = _imdb_identity(imdb_id)
        
    except Exception as exc:
        logger.warning(f"[anime_naming] could not resolve canonical title: {exc}")

    with _lock:
        _identity_cache[key] = identity
    return identity


def crosswalk_placement(*, mal_id=None, anilist_id=None) -> tuple[int | None, int]:
    """(TMDB season, TMDB episode offset) of one provider entry, so "Vinland Saga 2" lands in S02 and a "Part 2"
    continues the numbering of its season. ``(None, 0)`` when the entry is not in the crosswalk."""
    if not (mal_id or anilist_id):
        return None, 0
    try:
        record = anime_id_map.lookup(mal_id=mal_id, anilist_id=anilist_id) or {}
    except Exception as exc:
        logger.warning(f"[anime_naming] crosswalk lookup failed: {exc}")
        return None, 0
    season = (record.get("season") or {}).get("tmdb") if isinstance(record.get("season"), dict) else None
    offset = (record.get("episode_offset") or {}).get("tmdb") if isinstance(record.get("episode_offset"), dict) else None
    season = season if isinstance(season, int) and season > 0 else None
    offset = offset if isinstance(offset, int) and offset > 0 else 0
    return season, offset


def strip_language_suffix(name: str) -> str:
    """``Vinland Saga (ITA)`` -> ``Vinland Saga``: the dub and the sub of a show share one folder."""
    stripped = _LANG_SUFFIX_RE.sub("", name or "").strip()
    return stripped or name


def _format_has_year_token() -> bool:
    try:
        return _YEAR_TOKEN in (config_manager.config.get("OUTPUT", "episode_format") or "")
    except Exception:
        return False


def _clean_year(value) -> str | None:
    """A plausible 4-digit year from a provider value, else None (empty, 0 and the 9999 placeholder are rejected)."""
    match = re.fullmatch(r"\s*(\d{4})\s*", str(value or ""))
    if not match or match.group(1) in ("0000", "9999"):
        return None
    return match.group(1)


def series_name_and_year(fallback_name: str, *, mal_id=None, anilist_id=None, tmdb_id=None, fallback_year=None) -> tuple[str, str | None]:
    """The ``(series_name, series_year)`` to hand to ``map_episode_path``.

    Without a resolvable TMDB identity the provider's own name is kept, with ``fallback_year`` (the year the
    provider itself reports) when it gives one. With an identity, the TMDB title and year are used. The year
    goes in ``series_year`` when ``OUTPUT.episode_format`` has a ``%(series_year)`` token, otherwise it
    is appended to the name (``My Hero Academia (2016)``) so the folder is right with any format.
    """
    identity = canonical_identity(mal_id=mal_id, anilist_id=anilist_id, tmdb_id=tmdb_id)
    if identity:
        title, year = identity
    else:
        fallback_name = strip_language_suffix(fallback_name)
        year = _clean_year(fallback_year)
        if not year:
            return fallback_name, None
        title = fallback_name
    if _format_has_year_token():
        return title, str(year)
    return f"{title} ({year})", None
