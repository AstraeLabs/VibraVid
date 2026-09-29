# 29.09.26

from VibraVid.services.cinezo.client import get_player_url, player_is_available, probe_sources
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
                entry = Entries(
                    id=item_dict.get("id"),
                    name=item_dict.get("name"),
                    slug=item_dict.get("slug", ""),
                    type=item_dict.get("type"),
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    tmdb_id=item_dict.get("tmdb_id") or item_dict.get("id"),
                    raw_data=item_dict,
                )

                if not self._has_available_search_source(entry):
                    continue

                results.append(entry)

        return results

    def _has_available_search_source(self, media_item: Entries) -> bool:
        """Hide Cinezo movie results that have no playable source backend."""
        if not media_item.is_movie:
            return True

        tmdb_id = int(media_item.tmdb_id or media_item.id or 0)
        return any(source.available for source in probe_sources(tmdb_id, "movie"))

    def get_player_url(
        self,
        media_item: Entries,
        season: int | None = None,
        episode: int | None = None,
    ) -> str:
        """Return the current public Cinezo player URL for a GUI item."""
        tmdb_id = int(media_item.tmdb_id or media_item.id)
        media_type = "movie" if media_item.is_movie else "tv"
        return get_player_url(tmdb_id, media_type, season, episode)

    def player_is_available(
        self,
        media_item: Entries,
        season: int | None = None,
        episode: int | None = None,
    ) -> bool:
        """Check whether the current public player page is reachable."""
        tmdb_id = int(media_item.tmdb_id or media_item.id)
        media_type = "movie" if media_item.is_movie else "tv"
        return player_is_available(tmdb_id, media_type, season, episode)

    def get_source_status(
        self,
        media_item: Entries,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[dict]:
        """Return non-media source health information for diagnostics."""
        tmdb_id = int(media_item.tmdb_id or media_item.id)
        media_type = "movie" if media_item.is_movie else "tv"
        return [
            {
                "name": source.name,
                "status_code": source.status_code,
                "content_type": source.content_type,
                "available": source.available,
                "source_shape": source.source_shape,
                "source_keys": source.source_keys,
                "subtitle_count": source.subtitle_count,
                "error": source.error,
            }
            for source in probe_sources(tmdb_id, media_type, season, episode)
        ]

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Get seasons and episodes for a Cinezo series."""
        if media_item.is_movie:
            return None

        tmdb_id = int(media_item.tmdb_id or media_item.id or 0)
        scrape_serie = self.get_cached_scraper(media_item)

        if not scrape_serie:
            scrape_serie = GetSerieInfo(tmdb_id, media_item.name or "")
            self.set_cached_scraper(media_item, scrape_serie)

        if not scrape_serie.getNumberSeason():
            return None

        seasons = []

        for season in scrape_serie.seasons_manager.seasons:
            episodes_raw = scrape_serie.getEpisodeSeasons(season.number)
            episodes = [
                Episode(
                    number=episode.number,
                    name=episode.name,
                    id=episode.id,
                    duration=getattr(episode, "duration", None),
                    image=getattr(episode, "image", None),
                )
                for episode in episodes_raw
            ]

            seasons.append(
                Season(
                    number=season.number,
                    episodes=episodes,
                    name=season.name,
                )
            )

        return seasons or None

    def start_download(
        self,
        media_item: Entries,
        season: str | None = None,
        episodes: str | None = None,
    ) -> bool:
        """Start the Cinezo download pipeline."""
        search_fn = self._get_search_fn()
        selections = None

        if season or episodes:
            selections = {"season": season, "episode": episodes}

        scrape_serie = self.get_cached_scraper(media_item)
        direct_item = dict(media_item.raw_data or media_item.__dict__.copy())
        return bool(
            search_fn(
                direct_item=direct_item,
                selections=selections,
                scrape_serie=scrape_serie,
            )
        )
