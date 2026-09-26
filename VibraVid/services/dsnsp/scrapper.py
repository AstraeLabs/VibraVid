# 21.09.26

import logging
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager

logger = logging.getLogger(__name__)


class GetContentInfo:
    """Fetch content metadata (movie or series)."""

    def __init__(self, client, entity_id: str):
        self.client = client
        self.entity_id = entity_id
        self.content_info = None
        self.content_type = None
        self._fetch()

    def _fetch(self):
        try:
            page = GetPageInfo(self.client, self.entity_id)
            page_data = page.data or {}
            containers = page_data.get("containers", [])
            if any(container.get("type") == "episodes" for container in containers):
                self.content_type = "series"
                self.content_info = page
                logger.debug(f"Content type: {self.content_type}")
                return

            actions = page_data.get("actions", [])
            playback_action = next((a for a in actions if a.get("type") == "playback"), None)
            if playback_action:
                content_type = str(playback_action.get("contentType", "")).lower()
                self.content_type = "movie" if content_type in {"movie", "film", "vod"} else "other"
            else:
                self.content_type = "other"
            self.content_info = page
            logger.debug(f"Content type: {self.content_type}")
        except Exception as e:
            logger.error(f"Error fetching content info: {e}")

    def get_type(self) -> str:
        return self.content_type or "other"


class GetPageInfo:
    """Fetch page info for a title."""

    def __init__(self, client, entity_id: str):
        self.client = client
        self.entity_id = entity_id
        self.data = None
        self._fetch()

    def _fetch(self):
        try:
            endpoint = f"https://disney.api.edge.bamgrid.com/explore/v1.20/page/{self.entity_id}"
            params = {"limit": "999"}
            resp = self.client.sdk.request("GET", endpoint, params=params)
            self.data = resp.get("data", {}).get("page", {})
            logger.debug(f"Loaded page info for {self.entity_id}")
        except Exception as e:
            logger.error(f"Error fetching page info: {e}")
            raise

    def get_visuals(self) -> dict:
        return self.data.get("visuals", {}) if self.data else {}

    def get_id(self) -> str:
        return self.data.get("id", self.entity_id) if self.data else self.entity_id


class GetSerieInfo:
    """Scrape series info with seasons and episodes."""

    def __init__(self, client, series_id: str):
        self.client = client
        self.series_id = series_id
        self.series_name = ""
        self.year = None
        self.seasons_manager = SeasonManager()
        self._all_episodes = None
        self._lock = threading.Lock()
        self._fetch_series_info()

    def _fetch_series_info(self):
        try:
            page = GetPageInfo(self.client, self.series_id)
            data = page.data
            visuals = data.get("visuals", {}) if data else {}
            self.series_name = visuals.get("title", self.series_id)
            self.year = visuals.get("metastringParts", {}).get("releaseYearRange", {}).get("startYear")

            containers = data.get("containers", []) if data else []
            episode_container = next((c for c in containers if c.get("type") == "episodes"), None)
            if episode_container:
                seasons = episode_container.get("seasons", [])
                for season_index, season in enumerate(seasons, start=1):
                    season_id = season.get("id", "")
                    season_num = season.get("seasonNumber") or season_index
                    self._add_season_episodes(season, season_id, season_num)

            logger.info(f"Series: {self.series_name} ({len(self.seasons_manager)} seasons)")
        except Exception as e:
            logger.error(f"Error fetching series info: {e}")
            raise

    def _add_season_episodes(self, season: dict, season_id: str, season_number: int):
        season_obj = self.seasons_manager.add(
            Season(number=season_number, name=f"Season {season_number}", id=season_id)
        )
        if not season_obj:
            return

        for item in season.get("items", []):
            if item.get("type") != "view":
                continue
            visuals = item.get("visuals", {})
            episode_id = item.get("id", "")
            playback_action = next(
                (action for action in item.get("actions", []) if action.get("resourceId")),
                {},
            )
            playback_id = playback_action.get("resourceId") or episode_id
            episode_number = visuals.get("episodeNumber") or len(season_obj.episodes.episodes) + 1
            season_obj.episodes.add(
                Episode(
                    id=episode_id,
                    video_id=playback_id,
                    name=visuals.get("episodeTitle") or visuals.get("title", episode_id),
                    number=episode_number,
                    duration=(visuals.get("durationMs") or 0) // 1000,
                )
            )
        logger.debug(f"Season {season_number}: {len(season_obj.episodes.episodes)} episodes")

    def getNumberSeason(self) -> int:
        with self._lock:
            if not self.seasons_manager.seasons:
                self._fetch_series_info()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        with self._lock:
            if not self.seasons_manager.seasons:
                self._fetch_series_info()
            season = self.seasons_manager.get_season_by_number(season_number)
            return season.episodes.episodes if season else []
