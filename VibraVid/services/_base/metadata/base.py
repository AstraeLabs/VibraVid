# 01.10.26

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import tmdb_artwork

logger = logging.getLogger(__name__)


@dataclass
class Person:
    name: str
    role: str | None = None
    image_url: str | None = None


@dataclass
class MovieInfo:
    title: str
    original_title: str | None = None
    year: int | None = None
    premiered: str | None = None
    plot: str | None = None
    tagline: str | None = None
    runtime: int | None = None  # minutes
    genres: list[str] = field(default_factory=list)
    rating: float | None = None  # 0-10
    votes: int | None = None
    mpaa: str | None = None  # age certification
    tags: list[str] = field(default_factory=list)  # keywords
    countries: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    collection: str | None = None  # saga / franchise
    collection_overview: str | None = None
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    actors: list[Person] = field(default_factory=list)
    trailer_url: str | None = None
    ids: dict[str, str] = field(default_factory=dict)  # provider name -> id ("tmdb", "imdb", "tvdb")
    image_url: str | None = None  # poster
    fanart_url: str | None = None


@dataclass
class EpisodeInfo:
    title: str
    show_title: str | None = None
    season: int = 0
    episode: int = 0
    plot: str | None = None
    aired: str | None = None
    runtime: int | None = None  # minutes
    rating: float | None = None
    votes: int | None = None
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    actors: list[Person] = field(default_factory=list)  # guest stars
    ids: dict[str, str] = field(default_factory=dict)
    image_url: str | None = None


@dataclass
class SeriesInfo:
    title: str
    original_title: str | None = None
    year: int | None = None
    premiered: str | None = None
    status: str | None = None  # "Continuing" or "Ended"
    plot: str | None = None
    mpaa: str | None = None
    runtime: int | None = None  # typical episode length, minutes
    rating: float | None = None
    votes: int | None = None
    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)  # networks
    actors: list[Person] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    image_url: str | None = None  # series poster
    fanart_url: str | None = None
    season_posters: dict[int, str] = field(default_factory=dict)  # season number -> poster URL


class BaseMetadataProvider:
    NAME = ""

    def available(self) -> bool:
        """False when the provider cannot be used right now (e.g. no API key)."""
        return True

    def find(self, media_type: str, name: str | None, slug: str | None, year, site_tmdb_id) -> str | None:
        """Id of the reliable match for a film (``media_type="movie"``) or series (``"tv"``), else None."""
        return None

    def find_by_title(self, media_type: str, name: str | None) -> str | None:
        """
        Id of the one result titled *name*, ignoring the year (for sites that give none). Weaker than :meth:`find`:
        the caller must confirm the id some other way, e.g. by the episode title (see ``Sidecars``).
        """
        return None

    def movie(self, item_id: str) -> MovieInfo | None:
        return None

    def episode(self, series_id: str, season: int, episode: int) -> EpisodeInfo | None:
        return None

    def series(self, series_id: str) -> SeriesInfo | None:
        """Series-level data for ``tvshow.nfo`` and the series / season artwork."""
        return None

    @staticmethod
    def _slug(text: str | None) -> str:
        return tmdb_client._slugify(text) if text else ""

    @classmethod
    def titles_match(cls, candidates: Iterable[str | None], name: str | None) -> bool:
        """Whether any of *candidates* is the same title as *name* (fuzzy slug match, stricter than the search's: see TRUSTED_TITLE_SIMILARITY)."""
        wanted = cls._slug(name)
        return bool(wanted) and any(c and tmdb_client._slugs_match(cls._slug(c), wanted, tmdb_artwork.TRUSTED_TITLE_SIMILARITY) for c in candidates)

    @classmethod
    def title_ratio(cls, candidates: Iterable[str | None], name: str | None) -> float:
        """Best similarity between *name* and *candidates* (to choose among several matching results)."""
        wanted = cls._slug(name)
        return max((SequenceMatcher(None, cls._slug(c), wanted).ratio() for c in candidates if c), default=0.0)

    @staticmethod
    def year_close(found, wanted) -> bool:
        """Years within one of each other (providers often disagree by one); a missing year never matches."""
        found, wanted = tmdb_artwork.year_of(found), tmdb_artwork.year_of(wanted)
        return found is not None and wanted is not None and abs(found - wanted) <= 1

    @classmethod
    def best_match(cls, results: Iterable[dict], name: str | None, year, titles_of: Callable[[dict], list], year_of: Callable[[dict], object]) -> dict | None:
        """The best result whose title is *name* and year is *year* (or within one)."""
        if not name or tmdb_artwork.year_of(year) is None:
            return None
        
        best, best_ratio = None, 0.0
        for result in results:
            titles = titles_of(result)
            if not (cls.titles_match(titles, name) and cls.year_close(year_of(result), year)):
                continue
            ratio = cls.title_ratio(titles, name)
            if ratio > best_ratio:
                best, best_ratio = result, ratio
        
        return best

    @classmethod
    def unique_match(cls, results: Iterable[dict], name: str | None, titles_of: Callable[[dict], list], id_of: Callable[[dict], object]) -> str | None:
        """The id of the only result whose title is *name*; None when none or several (remakes, namesakes) match."""
        ids = {str(id_of(r)) for r in results if id_of(r) and cls.titles_match(titles_of(r), name)}
        return ids.pop() if len(ids) == 1 else None

    @staticmethod
    def imdb_id_of_site_tmdb(media_type: str, site_tmdb_id) -> str | None:
        """IMDb id behind a TMDB id the site itself supplied (needs a TMDB key); the bridge to the other providers."""
        if not (site_tmdb_id and tmdb_client.api_key):
            return None
        
        try:
            return tmdb_client.get_imdb_id(int(site_tmdb_id), media_type)
        except Exception as exc:
            logger.debug(f"could not map TMDB {site_tmdb_id} to IMDb: {exc}")
            return None

    @staticmethod
    def iso_date(parts: dict | None) -> str | None:
        """``{"year": 2019, "month": 11, "day": 27}`` -> ``2019-11-27`` (None unless complete)."""
        if parts and parts.get("year") and parts.get("month") and parts.get("day"):
            return f"{int(parts['year']):04d}-{int(parts['month']):02d}-{int(parts['day']):02d}"
        return None

    @staticmethod
    def minutes(seconds) -> int | None:
        return round(int(seconds) / 60) if seconds else None
