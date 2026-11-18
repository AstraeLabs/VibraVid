# 11.07.26

import importlib
import logging

from VibraVid.services._base.site_loader import get_folder_name

from .base import BaseStreamingAPI, Entries, Episode, Season

logger = logging.getLogger(__name__)


class AmazonMusicAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "amazon_music"
        self._load_config()
        self._search_fn = None

    def _load_config(self):
        """Load site configuration."""
        self.base_url = None

    def _get_album_scraper(self, media_item: Entries):
        """Build and fetch an AlbumScraper for an album Entries item."""
        module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}.scrapper")
        AlbumScraper = module.AlbumScraper

        raw_data = media_item.raw_data if isinstance(media_item.raw_data, dict) else {}
        raw_url = str(media_item.url or raw_data.get("url") or "").strip()
        album_id = raw_url.split(":", 1)[1] if ":" in raw_url else raw_url
        if not album_id:
            album_id = str(media_item.id or raw_data.get("id") or "").strip()
        if not album_id:
            return None

        audio_format = getattr(media_item, "audio_format", None)
        scraper = AlbumScraper(album_id, audio_format=audio_format)
        scraper._audio_format_raw = audio_format
        scraper.fetch()
        return scraper

    def search(self, query: str) -> list[Entries]:
        """Search for tracks and albums and return a list of Entries for the GUI."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results: list[Entries] = []
        if database and hasattr(database, "media_list"):
            for element in database.media_list:
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}

                media_item = Entries(
                    id=item_dict.get("id"),
                    name=item_dict.get("name"),
                    slug=item_dict.get("slug", ""),
                    path_id=item_dict.get("path_id"),
                    type=item_dict.get("type", "song"),  # "song" or "album"
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    tmdb_id=item_dict.get("tmdb_id"),
                    raw_data=item_dict,
                )
                results.append(media_item)

        return results

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for an Amazon Music album (treated as a series)."""
        if str(getattr(media_item, "type", "")).lower() != "album":
            return None

        try:
            scraper = self._get_album_scraper(media_item)
            if scraper is None:
                logger.warning("Amazon Music album metadata skipped: album_id not found for media item '%s'", media_item.name)
                return None

            # Cache the scraper so start_download can reuse it
            self.set_cached_scraper(media_item, scraper)

            seasons: list[Season] = []
            for season_obj in scraper.seasons_manager.seasons:
                disc_number = season_obj.number
                episodes = scraper.getEpisodeSeasons(disc_number)

                gui_season = Season(
                    number=disc_number,
                    name=season_obj.name,
                    episodes=[
                        Episode(
                            number=ep.get("number") or (i + 1),
                            name=ep.get("name", ""),
                            id=ep.get("id"),
                            duration=round(ep["duration"] / 60) if ep.get("duration") else None,
                        )
                        for i, ep in enumerate(episodes)
                    ],
                )
                seasons.append(gui_season)

            return seasons

        except Exception:
            logger.exception("Amazon Music get_series_metadata failed for '%s'", media_item.name)
            return None

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start a download for a selected track or album by delegating to the service."""
        search_fn = self._get_search_fn()

        selections = {}
        if season:
            selections["season"] = season
        if episodes:
            selections["episode"] = episodes

        # Propagate audio_format if set on the media_item (flac / mp3)
        audio_format = getattr(media_item, "audio_format", None)
        if audio_format:
            selections["audio_format"] = audio_format

        scrape_serie = self.get_cached_scraper(media_item)

        search_fn(direct_item=media_item.raw_data, selections=selections or None, scrape_serie=scrape_serie)
        return True
