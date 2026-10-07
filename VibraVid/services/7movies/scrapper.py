# 07.10.26

import logging
import threading

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base.object import Episode, EpisodeManager, Season, SeasonManager

logger = logging.getLogger(__name__)


class GetSerieInfo:
    """Fetches season/episode metadata for a 7Movies series via TMDB (the site itself is addressed by TMDB id)."""
    def __init__(self, tmdb_id: int, series_name: str):
        self.tmdb_id = tmdb_id
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
            details = tmdb_client._make_request(f"tv/{self.tmdb_id}", {"language": "it"}) or {}
            first_air = details.get("first_air_date", "") or ""
            if first_air:
                self.series_year = int(first_air[:4])

            episode_run_time = details.get("episode_run_time") or []
            duration = episode_run_time[0] if episode_run_time else None

            for raw_s in details.get("seasons", []):
                sn = raw_s.get("season_number", 0)
                if sn == 0:
                    continue  # skip specials

                ep_count = raw_s.get("episode_count", 0)
                em = EpisodeManager()
                for ep_num in range(1, ep_count + 1):
                    em.add(Episode(id=ep_num, number=ep_num, name=f"Episodio {ep_num}", duration=duration))

                s = Season(id=sn, number=sn, name=raw_s.get("name", f"Stagione {sn}"), slug="")
                s.episodes = em
                self.seasons_manager.add(s)
        except Exception as e:
            logger.error(f"[7Movies] TMDB series load failed: {e}")

    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season."""
        self._load()
        season = self.seasons_manager.get_season_by_number(season_number)

        if not season:
            return []

        return season.episodes.episodes
