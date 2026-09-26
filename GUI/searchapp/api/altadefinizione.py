# 26.05.24

import importlib

from VibraVid.services._base.site_loader import get_folder_name
from VibraVid.services.altadefinizione.scrapper import GetSerieInfo

from .base import BaseStreamingAPI, Entries, Episode, Season


class AltadefinizioneApi(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "altadefinizione"
        self._search_fn = None
        self.scrape_serie = None

    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = module.search
        return self._search_fn

    def search(self, query: str) -> list[Entries]:
        """Search for content on Altadefinizione."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results = []
        if database and hasattr(database, "media_list"):
            items = list(database.media_list)
            for element in items:
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}
                tmdb_id = item_dict.get("tmdb_id") or item_dict.get("id")

                media_item = Entries(
                    id=item_dict.get("id"),
                    name=item_dict.get("name"),
                    slug=item_dict.get("slug", ""),
                    path_id=item_dict.get("path_id"),
                    type=item_dict.get("type"),
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    tmdb_id=tmdb_id,
                    provider_language=item_dict.get("provider_language"),
                )
                results.append(media_item)

        return results

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for a Altadefinizione series."""
        if media_item.is_movie:
            return None

        tmdb_id = getattr(media_item, "tmdb_id", None) or getattr(media_item, "id", None)
        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = GetSerieInfo(media_item.name, tmdb_id, getattr(media_item, "year", None))
            self.set_cached_scraper(media_item, scrape_serie)

        seasons_count = scrape_serie.getNumberSeason()
        if not seasons_count:
            return None

        seasons: list[Season] = []
        for s in scrape_serie.seasons_manager.seasons:
            season_num = s.number
            season_name = getattr(s, "name", None)

            episodes_raw = scrape_serie.getEpisodeSeasons(s.number)
            episodes: list[Episode] = []
            seen_numbers = set()

            for idx, ep in enumerate(episodes_raw or [], 1):
                ep_number = getattr(ep, "number", None)
                if not ep_number and ep_number != 0:
                    ep_number = idx

                if ep_number in seen_numbers:
                    continue

                seen_numbers.add(ep_number)
                episode = Episode(
                    number=ep_number,
                    name=getattr(ep, "name", f"Episodio {idx}"),
                    id=getattr(ep, "id", idx),
                    language=getattr(ep, "language", None),
                    duration=getattr(ep, "duration", None),
                )
                episodes.append(episode)

            seasons.append(Season(number=season_num, episodes=episodes, name=season_name))

        return seasons if seasons else None

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start downloading from Altadefinizione."""
        search_fn = self._get_search_fn()

        selections = None
        if season or episodes:
            selections = {"season": season, "episode": episodes}

        scrape_serie = self.get_cached_scraper(media_item)
        return bool(search_fn(direct_item=media_item.__dict__.copy(), selections=selections, scrape_serie=scrape_serie))
