# 29.05.26

from VibraVid.services.eurostreaming.scrapper import GetSerieInfo
from VibraVid.utils import config_manager

from .base import BaseStreamingAPI, Entries, Episode, Season


class EurostreamingAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "eurostreaming"
        self._search_fn = None

    def _get_base_url(self) -> str:
        """Get the base URL for Eurostreaming from the configuration."""
        return config_manager.domain.get("eurostreaming", "full_url").rstrip("/")

    def search(self, query: str) -> list[Entries]:
        """Search for content on Eurostreaming and return a list of Entries."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results = []
        if database and hasattr(database, "media_list"):
            for element in list(database.media_list):
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}
                media_item = Entries(
                    id=item_dict.get("id"),
                    name=item_dict.get("name") or "",
                    slug=item_dict.get("slug", ""),
                    path_id=item_dict.get("path_id"),
                    type=item_dict.get("type", "tv"),
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                )
                results.append(media_item)

        return results

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for a Eurostreaming series."""
        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = GetSerieInfo(media_item.name, self._get_base_url())
            self.set_cached_scraper(media_item, scrape_serie)

        seasons_count = scrape_serie.getNumberSeason()
        if not seasons_count:
            return None

        seasons: list[Season] = []
        for s in scrape_serie.seasons_manager.seasons:
            season_num = s.number
            season_name = getattr(s, "name", None)

            episodes_raw = scrape_serie.getEpisodeSeasons(s.number or 0)
            episodes: list[Episode] = []
            seen_numbers = set()

            for idx, ep in enumerate(episodes_raw or [], 1):
                ep_number = getattr(ep, "number", None)
                if not ep_number and ep_number != 0:
                    ep_number = idx

                if ep_number in seen_numbers:
                    continue

                seen_numbers.add(ep_number)
                episodes.append(
                    Episode(
                        number=ep_number or 0,
                        name=getattr(ep, "name", f"Episodio {idx}"),
                        id=getattr(ep, "id", idx),
                        language=getattr(ep, "language", None),
                    )
                )

            seasons.append(Season(number=season_num, episodes=episodes, name=season_name))

        return seasons if seasons else None

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start downloading from Eurostreaming."""
        search_fn = self._get_search_fn()

        selections = None
        if season or episodes:
            selections = {
                "season": season,
                "episode": episodes,
            }

        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.__dict__.copy(), selections=selections, scrape_serie=scrape_serie)
        return True
