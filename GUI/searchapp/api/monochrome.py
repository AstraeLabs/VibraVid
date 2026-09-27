# 16.07.26

import importlib
import logging

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base.site_loader import get_folder_name

from .base import BaseStreamingAPI, Entries, Episode, Season

logger = logging.getLogger(__name__)


class MonochromeAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "monochrome"
        self.base_url = None
        self._search_fn = None

    def _get_album_scraper(self, media_item: Entries):
        """Resolve the album tracklist directly from Amazon Music (no lucida.to involved)."""
        album_mod = importlib.import_module(f"VibraVid.{get_folder_name()}.monochrome.album")
        AmazonAlbumScraper = album_mod.AmazonAlbumScraper

        raw_data = media_item.raw_data if isinstance(media_item.raw_data, dict) else {}
        album_id = str(getattr(media_item, "id", None) or raw_data.get("id") or "").strip()
        if not album_id:
            return None

        scraper = AmazonAlbumScraper(album_id)
        scraper.fetch()
        return scraper

    def search(self, query: str) -> list[Entries]:
        """Search the monochrome (Amazon Music) catalog and return Entries for the GUI."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results: list[Entries] = []
        if database and hasattr(database, "media_list"):
            for element in database.media_list:
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}
                results.append(
                    Entries(
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
                )
        return results

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """For albums: resolve the tracklist (via Amazon Music) as a single Season."""
        if str(getattr(media_item, "type", "")).lower() != "album":
            return None

        try:
            scraper = self._get_album_scraper(media_item)
            if scraper is None:
                logger.warning("Monochrome album metadata skipped: url not found for '%s'", media_item.name)
                return None

            self.set_cached_scraper(media_item, scraper)

            seasons: list[Season] = []
            for season_obj in scraper.seasons_manager.seasons:
                episodes = scraper.getEpisodeSeasons(season_obj.number)
                seasons.append(
                    Season(
                        number=season_obj.number,
                        name=season_obj.name,
                        episodes=[
                            Episode(
                                number=ep.get("number") or (i + 1),
                                name=ep.get("name", ""),
                                id=ep.get("id"),
                                duration=round(ep["duration_seconds"] / 60) if ep.get("duration_seconds") else None,
                            )
                            for i, ep in enumerate(episodes)
                        ],
                    )
                )
            return seasons

        except Exception:
            logger.exception("Monochrome get_series_metadata failed for '%s'", media_item.name)
            return None

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Delegate the download to the monochrome service."""
        search_fn = self._get_search_fn()

        selections = {}
        if season:
            selections["season"] = season
        if episodes:
            selections["episode"] = episodes

        context_tracker.reset_download_result()
        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections or None, scrape_serie=scrape_serie)

        errors = context_tracker.download_errors
        if errors and context_tracker.download_ok_count == 0:
            raise RuntimeError("; ".join(dict.fromkeys(errors)))
        return True
