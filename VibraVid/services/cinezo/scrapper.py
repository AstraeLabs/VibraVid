# 28.09.26

import logging
import threading

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base.object import Episode, EpisodeManager, Season, SeasonManager

logger = logging.getLogger(__name__)


class GetSerieInfo:
    """Fetch Cinezo series metadata from TMDB."""

    def __init__(self, tmdb_id: int, series_name: str):
        self.tmdb_id = int(tmdb_id)
        self.series_name = series_name
        self.series_year = None
        self.seasons_manager = SeasonManager()
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load(self):
        with self._load_lock:
            self._load_locked()

    def _load_locked(self):
        if self._loaded:
            return

        self._loaded = True

        try:
            details = tmdb_client._make_request(
                f"tv/{self.tmdb_id}",
                {"language": "it"},
            ) or {}

            if details.get("name"):
                self.series_name = details["name"]

            first_air = details.get("first_air_date") or ""
            if first_air:
                try:
                    self.series_year = int(first_air[:4])
                except ValueError:
                    self.series_year = None

            for raw_season in details.get("seasons", []):
                season_number = raw_season.get("season_number")
                if season_number in (None, 0):
                    continue

                season = Season(
                    id=raw_season.get("id"),
                    number=season_number,
                    name=raw_season.get("name") or f"Stagione {season_number}",
                    slug="",
                    tmdb_id=raw_season.get("id"),
                )
                self.seasons_manager.add(season)

        except Exception as error:
            logger.error(f"[Cinezo] TMDB series load failed: {error}")

    def getNumberSeason(self) -> int:
        """Get the total number of regular seasons."""
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get full TMDB episode metadata for one season."""
        self._load()

        season = self.seasons_manager.get_season_by_number(int(season_number))
        if season is None:
            return []

        if len(season.episodes):
            return season.episodes.episodes

        try:
            details = tmdb_client._make_request(
                f"tv/{self.tmdb_id}/season/{int(season_number)}",
                {"language": "it"},
            ) or {}
        except Exception as error:
            logger.error(
                f"[Cinezo] TMDB episode load failed for S{int(season_number)}: {error}"
            )
            return []

        episodes = EpisodeManager()

        for raw_episode in details.get("episodes", []):
            episode_number = raw_episode.get("episode_number")
            if episode_number is None:
                continue

            still_path = raw_episode.get("still_path")
            episodes.add(
                Episode(
                    id=raw_episode.get("id"),
                    tmdb_id=raw_episode.get("id"),
                    number=episode_number,
                    name=raw_episode.get("name") or f"Episodio {episode_number}",
                    duration=raw_episode.get("runtime"),
                    description=raw_episode.get("overview"),
                    image=f"https://image.tmdb.org/t/p/w780{still_path}" if still_path else None,
                    year=(raw_episode.get("air_date") or "")[:4] or None,
                )
            )

        season.episodes = episodes
        return season.episodes.episodes
