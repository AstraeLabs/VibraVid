# 06.06.25

from VibraVid.services.animeunity.scrapper import ScrapeSerieAnime
from VibraVid.utils import anime_id_map, config_manager

from .base import BaseStreamingAPI, Entries, Episode, Season


class AnimeUnityAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "animeunity"
        self._load_config()
        self._search_fn = None
        self.scrape_serie = None

    def _load_config(self):
        """Load site configuration."""
        self.base_url = config_manager.domain.get(self.site_name, "full_url").rstrip("/")
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")

    def search(self, query: str) -> list[Entries]:
        """Search for content on AnimeUnity."""
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
                    tmdb_id=item_dict.get("tmdb_id"),
                    raw_data=item_dict,
                )
                results.append(media_item)

        return results

    def resolve_tmdb_id(self, media_item: Entries) -> str | int | None:
        """AnimeUnity never carries a TMDB id itself; resolve one via the MAL/AniList crosswalk."""
        direct_id = super().resolve_tmdb_id(media_item)
        if direct_id not in (None, ""):
            return direct_id

        raw_data = media_item.raw_data if isinstance(media_item.raw_data, dict) else {}
        mal_id = raw_data.get("mal_id")
        anilist_id = raw_data.get("anilist_id")
        if mal_id in (None, "") and anilist_id in (None, ""):
            return None

        media_type = "movie" if str(media_item.type or "").lower() in ("film", "movie") else "tv"
        tmdb_id = anime_id_map.resolve_tmdb_id(media_type, mal_id=mal_id, anilist_id=anilist_id)
        if tmdb_id:
            media_item.tmdb_id = tmdb_id
            raw_data["tmdb_id"] = tmdb_id
        return tmdb_id

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for an AnimeUnity series."""
        if media_item.is_movie:
            return None

        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = ScrapeSerieAnime(self.base_url)
            scrape_serie.setup(series_name=media_item.slug, media_id=media_item.id)
            self.set_cached_scraper(media_item, scrape_serie)

        episodes_count = scrape_serie.get_count_episodes()
        if not episodes_count:
            return None

        # AnimeUnity typically has single season
        episodes = []
        for ep_num in range(1, episodes_count + 1):
            episode = Episode(number=ep_num, name=f"Episodio {ep_num}", id=ep_num)
            episodes.append(episode)

        season = Season(number=1, episodes=episodes, name="Stagione 1")
        return [season]

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start downloading from AnimeUnity."""
        search_fn = self._get_search_fn()
        media_type = str(media_item.type or "").lower()
        is_series_like = media_type in {"tv", "serie", "series", "ova", "ona", "show", "tv short"}

        # For AnimeUnity, always pass an episode selection for series-like titles.
        selections = None
        if is_series_like:
            selections = {"episode": episodes or "*"}
            if season:
                selections["season"] = season

        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True
