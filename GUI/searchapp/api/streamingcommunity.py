# 06.06.25


from VibraVid.services.streamingcommunity import _effective_languages
from VibraVid.services.streamingcommunity.scrapper import GetSerieInfo
from VibraVid.utils import config_manager

from .base import BaseStreamingAPI, Entries, Episode, Season


class StreamingCommunityAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "streamingcommunity"
        self._load_config()
        self._search_fn = None
        self.scrape_serie = None

    def _load_config(self):
        """Load site configuration."""
        self.base_url = config_manager.domain.get(self.site_name, "full_url")
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")


    def search(self, query: str) -> list[Entries]:
        """Search for content on StreamingCommunity."""
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results = []
        if database and hasattr(database, "media_list"):
            items = list(database.media_list)
            for element in items:
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}

                media_item = Entries(
                    id=item_dict.get("id"),
                    name=item_dict.get("name"),
                    slug=item_dict.get("slug", ""),
                    path_id=item_dict.get("path_id"),
                    type=item_dict.get("type"),
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    tmdb_id=item_dict.get("tmdb_id"),
                    provider_language=item_dict.get("provider_language"),
                    raw_data=item_dict,
                )
                results.append(media_item)

        # Dedup by ID: search runs for both 'it' and 'en', same title appears twice.
        # Keep 'it' version when both exist for the same ID.
        seen: dict = {}
        deduped = []
        for item in results:
            key = item.id
            if key not in seen:
                seen[key] = len(deduped)
                deduped.append(item)
            elif item.provider_language == "it" and deduped[seen[key]].provider_language != "it":
                deduped[seen[key]] = item
        return deduped

    def resolve_tmdb_id(self, media_item: Entries) -> str | int | None:
        """Read StreamingCommunity's own TMDB id from the exact title detail."""
        direct_id = super().resolve_tmdb_id(media_item)
        if direct_id not in (None, ""):
            return direct_id

        scraper = self.get_cached_scraper(media_item)
        if not scraper:
            scraper = GetSerieInfo(
                url=f"{self.base_url}{media_item.provider_language or 'it'}",
                media_id=media_item.id,
                series_name=media_item.slug,
                year=media_item.year,
                provider_language=media_item.provider_language or "it",
                series_display_name=media_item.name,
            )
            self.set_cached_scraper(media_item, scraper)

        tmdb_id = scraper.get_tmdb_id()
        if tmdb_id:
            media_item.tmdb_id = str(tmdb_id)
            if isinstance(media_item.raw_data, dict):
                media_item.raw_data["tmdb_id"] = str(tmdb_id)
        return tmdb_id

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for a StreamingCommunity series."""
        if media_item.is_movie:
            return None

        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = GetSerieInfo(
                url=f"{self.base_url}{media_item.provider_language}",
                media_id=media_item.id,
                series_name=media_item.slug,
                year=media_item.year,
                provider_language=media_item.provider_language,
                series_display_name=media_item.name,
                languages=_effective_languages(),
            )
            self.set_cached_scraper(media_item, scrape_serie)

        seasons_count = scrape_serie.getNumberSeason()
        if not seasons_count:
            return None

        seasons = []
        for s in scrape_serie.seasons_manager.seasons:
            season_num = s.number
            season_name = getattr(s, "name", None)

            episodes_raw = scrape_serie.getEpisodeSeasons(s.number)
            episodes = []
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

            season = Season(number=season_num, episodes=episodes, name=season_name)
            seasons.append(season)
            print(f"[StreamingCommunity] Season {season_num} ({season_name or f'Season {season_num}'}): {len(episodes)} episodes")

        return seasons if seasons else None

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start downloading from StreamingCommunity."""
        search_fn = self._get_search_fn()

        # Prepare selections
        selections = None
        if season or episodes:
            selections = {"season": season, "episode": episodes}

        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True
