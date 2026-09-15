# 17.04.26

from VibraVid.services.cinezo.scrapper import GetSerieInfo

from .base import BaseStreamingAPI, Entries, Episode, Season


class CinezoAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "cinezo"
        self._search_fn = None

    def search(self, query: str) -> list[Entries]:
        """Search for movies and series on Cinezo."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)
        results = []
        if database and hasattr(database, "media_list"):
            for element in database.media_list:
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}
                results.append(
                    Entries(
                        id=item_dict.get("id"),
                        name=item_dict.get("name"),
                        slug=item_dict.get("slug", ""),
                        type=item_dict.get("type"),
                        url=item_dict.get("url"),
                        poster=item_dict.get("image"),
                        year=item_dict.get("year"),
                        tmdb_id=item_dict.get("id"),
                        raw_data=item_dict,
                    )
                )
        return results

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for a Cinezo series."""
        if media_item.is_movie:
            return None

        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            tmdb_id = int(media_item.id or 0)
            scrape_serie = GetSerieInfo(tmdb_id, media_item.name or "")
            self.set_cached_scraper(media_item, scrape_serie)

        count = scrape_serie.getNumberSeason()
        if not count:
            return None

        seasons = []
        for s in scrape_serie.seasons_manager.seasons:
            episodes = [Episode(number=ep.number, name=ep.name, id=ep.id) for ep in s.episodes.episodes]
            seasons.append(
                Season(
                    number=s.number,
                    episodes=episodes,
                    name=s.name,
                )
            )

        return seasons if seasons else None

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start downloading from Cinezo."""
        search_fn = self._get_search_fn()
        selections = None

        if season or episodes:
            selections = {"season": season, "episode": episodes}

        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True
