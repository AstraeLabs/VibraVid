# 06.06.25

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from VibraVid.services._base.site_loader import load_search_functions
from VibraVid.utils import config_manager


@dataclass
class Entries:
    """Standardized media item representation."""

    name: str
    type: str
    slug: str = None
    id: Any = None
    path_id: str | None = None
    url: str | None = None
    poster: str | None = None
    year: int | None = None
    provider_language: str | None = None
    tmdb_id: str | None = None
    raw_data: dict[str, Any] | None = None
    audio_format: str | None = None
    desc: str | None = None

    @property
    def is_movie(self) -> bool:
        return self.type.lower() in ["film", "movie", "ova"]

    @property
    def is_song(self) -> bool:
        return str(self.type or "").lower() in ["song", "track", "music"]

    @property
    def is_album(self) -> bool:
        return str(self.type or "").lower() == "album"


@dataclass
class Episode:
    """Episode information."""

    number: int
    name: str
    id: Any | None = None
    language: str | None = None
    duration: int | None = None
    image: str | None = None


@dataclass
class Season:
    """Season information."""

    number: int
    episodes: list[Episode]
    name: str | None = None
    image: str | None = None

    @property
    def episode_count(self) -> int:
        return len(self.episodes)


_SCRAPER_CACHE_TTL = 900  # 15 minutes — allows the GUI to see new episodes without a container restart


class BaseStreamingAPI(ABC):
    _scraper_cache: dict[str, tuple[Any, float]] = {}  # key → (scraper, timestamp)

    def __init__(self):
        self.site_name: str = ""
        self.base_url: str = ""
        self._search_fn = None

    def _get_search_fn(self):
        """Lazy-load the service's ``search`` entry point via the source-agnostic loader"""
        if self._search_fn is None:
            lazy = load_search_functions().get(f"{self.site_name}_search")
            if lazy is None:
                raise ModuleNotFoundError(f"No module named '{self.site_name}' (not found in any imp_service source)")
            self._search_fn = lazy
        return self._search_fn

    def _get_cache_key(self, media_item: Entries) -> str:
        """Generate a unique key for the scraper cache."""
        return f"{self.site_name}_{media_item.url or media_item.path_id or media_item.id or media_item.slug}"

    def _scraper_cache_enabled(self) -> bool:
        return not bool(config_manager.config.get_bool("DEFAULT", "disable_scraper_cache", default=False))

    def get_cached_scraper(self, media_item: Entries) -> Any | None:
        """Retrieve a cached scraper instance from the global cache."""
        if not self._scraper_cache_enabled():
            return None
        key = self._get_cache_key(media_item)
        entry = self._scraper_cache.get(key)
        if entry is None:
            return None
        scraper, ts = entry
        if time.monotonic() - ts > _SCRAPER_CACHE_TTL:
            del self._scraper_cache[key]
            return None
        return scraper

    def set_cached_scraper(self, media_item: Entries, scraper: Any):
        """Store a scraper instance in the global cache."""
        if not self._scraper_cache_enabled():
            return
        key = self._get_cache_key(media_item)
        self._scraper_cache[key] = (scraper, time.monotonic())

    def resolve_tmdb_id(self, media_item: Entries) -> str | int | None:
        """Return a provider-attested TMDB id for ``media_item``, if availables"""
        direct_id = getattr(media_item, "tmdb_id", None)
        if direct_id not in (None, ""):
            return direct_id

        raw_data = getattr(media_item, "raw_data", None)
        if isinstance(raw_data, dict):
            return raw_data.get("tmdb_id") or raw_data.get("tmdbId")
        return None

    @abstractmethod
    def search(self, query: str) -> list[Entries]:
        """
        Search for content on the streaming site.

        Args:
            query: Search term

        Returns:
            List of Entries objects
        """
        pass

    @abstractmethod
    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """
        Get seasons and episodes for a series.

        Args:
            media_item: Entries to get metadata for

        Returns:
            List of Season objects, or None if not a series
        """
        pass

    @abstractmethod
    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """
        Start downloading content.

        Args:
            media_item: Entries to download
            season: Season number (for series)
            episodes: Episode selection (e.g., "1-5" or "1,3,5" or "*" for all)

        Returns:
            True if download started successfully
        """
        pass
