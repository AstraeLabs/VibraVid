# 06.06.25

import functools
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base.site_extra_args import persisted_site_options
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
    catalog_languages: list[str] | None = None
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
_OPTION_METHODS = ("search", "get_series_metadata", "start_download")


def _with_login_args(method):
    """Run *method* with the site's `args` from login.toml as site options, unless the caller already set options (a download task, a remote request)."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if context_tracker.site_options:
            return method(self, *args, **kwargs)
        with persisted_site_options(self.site_name):
            return method(self, *args, **kwargs)

    return wrapper


class BaseStreamingAPI(ABC):
    _scraper_cache: dict[str, tuple[Any, float]] = {}  # key → (scraper, timestamp)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name in _OPTION_METHODS:
            method = cls.__dict__.get(name)
            if callable(method):
                setattr(cls, name, _with_login_args(method))

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
        from VibraVid.utils.scope import current_scope

        # A scraper carries the identity it was built with: on a remote server it must never be handed to another client.
        scope = current_scope()
        prefix = f"{scope}:" if scope else ""
        return f"{prefix}{self.site_name}_{media_item.url or media_item.path_id or media_item.id or media_item.slug}"

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

    def get_available_qualities(self, media_item: Entries, season: int | None = None, episode: int | None = None) -> list[str]:
        """Return only provider-confirmed resolutions for this exact video."""
        raise NotImplementedError("This provider does not expose video quality discovery yet")

    def get_available_audio_languages(self, media_item: Entries, season=None, episode=None) -> list[dict]:
        """Inspect actual audio tracks, without downloading video or returning media URLs."""
        raise NotImplementedError("Audio languages will be checked when the manifest is loaded for download")

    @property
    def supports_quality_discovery(self) -> bool:
        """True when the provider overrides get_available_qualities (can honor a chosen quality)."""
        return type(self).get_available_qualities is not BaseStreamingAPI.get_available_qualities

    @property
    def supports_audio_selection(self) -> bool:
        """True when the provider overrides get_available_audio_languages (can refuse a missing audio language)."""
        return type(self).get_available_audio_languages is not BaseStreamingAPI.get_available_audio_languages

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
