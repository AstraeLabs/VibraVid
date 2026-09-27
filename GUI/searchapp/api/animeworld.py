# 27.01.26

import re
import time

from VibraVid.services.animeworld.scrapper import ScrapSerie
from VibraVid.utils import anime_id_map, config_manager
from VibraVid.utils.http_client import create_client, get_headers

from .base import BaseStreamingAPI, Entries, Episode, Season

_MAL_URL_RE = re.compile(r"myanimelist\.net/anime/(\d+)")
_ANILIST_URL_RE = re.compile(r"anilist\.co/anime/(\d+)")
_EXTERNAL_ID_CACHE_TTL = 900


class AnimeWorldAPI(BaseStreamingAPI):
    # url -> ((mal_id, anilist_id), timestamp). Class-level: get_api() builds a
    # fresh instance per request, so an instance attribute would never be reused.
    _external_id_cache: dict[str, tuple[tuple[str | None, str | None], float]] = {}

    def __init__(self):
        super().__init__()
        self.site_name = "animeworld"
        self._load_config()
        self._search_fn = None
        self.scrape_serie = None

    def _load_config(self):
        """Load site configuration."""
        self.base_url = config_manager.domain.get(self.site_name, "full_url")
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")


    def search(self, query: str) -> list[Entries]:
        """Search for anime content on AnimeWorld."""
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
                    type=item_dict.get("type", "TV"),
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    tmdb_id=item_dict.get("tmdb_id"),
                    raw_data=item_dict,
                )
                results.append(media_item)

        return results

    def _fetch_external_ids(self, url: str) -> tuple[str | None, str | None]:
        """Scrape the AnimeWorld detail page for its MyAnimeList/AniList links."""
        cached = self._external_id_cache.get(url)
        if cached and (time.monotonic() - cached[1]) < _EXTERNAL_ID_CACHE_TTL:
            return cached[0]

        mal_id, anilist_id = None, None
        try:
            with create_client(headers=get_headers()) as client:
                response = client.get(url)
            response.raise_for_status()
            mal_match = _MAL_URL_RE.search(response.text)
            anilist_match = _ANILIST_URL_RE.search(response.text)
            mal_id = mal_match.group(1) if mal_match else None
            anilist_id = anilist_match.group(1) if anilist_match else None
        except Exception as e:
            print(f"[{self.site_name}] Could not fetch external ids from {url}: {e}")

        self._external_id_cache[url] = ((mal_id, anilist_id), time.monotonic())
        return mal_id, anilist_id

    def resolve_tmdb_id(self, media_item: Entries) -> str | int | None:
        """AnimeWorld never carries a TMDB id itself; resolve one via the MAL/AniList crosswalk."""
        direct_id = super().resolve_tmdb_id(media_item)
        if direct_id not in (None, ""):
            return direct_id

        if not media_item.url:
            return None

        mal_id, anilist_id = self._fetch_external_ids(media_item.url)
        if not mal_id and not anilist_id:
            return None

        media_type = "movie" if str(media_item.type or "").lower() in ("film", "movie") else "tv"
        tmdb_id = anime_id_map.resolve_tmdb_id(media_type, mal_id=mal_id, anilist_id=anilist_id)
        if tmdb_id:
            media_item.tmdb_id = tmdb_id
            if isinstance(media_item.raw_data, dict):
                media_item.raw_data["tmdb_id"] = tmdb_id
        return tmdb_id

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get episodes for an AnimeWorld series."""
        if media_item.type == "Movie":
            return None

        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = ScrapSerie(media_item.url, self.base_url)
            self.set_cached_scraper(media_item, scrape_serie)

        episodes_data = scrape_serie.get_episodes()

        if not episodes_data:
            print(f"[AnimeWorld] No episodes found for: {media_item.name}")
            return None

        # Create episodes list
        episodes = []
        for idx, ep_data in enumerate(episodes_data, 1):
            episode = Episode(
                number=idx, name=getattr(ep_data, "name", f"Episode {idx}"), id=getattr(ep_data, "id", idx)
            )
            episodes.append(episode)

        season = Season(number=1, episodes=episodes, name="Episodes")
        print(f"[AnimeWorld] Found {len(episodes)} episodes for: {media_item.name}")

        return [season]

    def start_download(self, media_item: Entries, season: str | None = None, episodes: str | None = None) -> bool:
        """Start downloading from AnimeWorld."""
        search_fn = self._get_search_fn()

        # Prepare selections
        selections = None
        if episodes:
            selections = {"episode": episodes}

        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True
