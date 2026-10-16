# 14.10.26
# By @Syncs-Luca98

import logging
import re
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager

from . import client as api

logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, show_id: str, series_name: str | None = None, year=None, market: str | None = None):
        self.show_id = show_id
        self.market = market
        self.series_name = series_name or show_id
        self.year = year
        self.seasons_manager = SeasonManager()
        self._season_ids: dict[int, str] = {}
        self._collect_lock = threading.Lock()

    def load_stream_info(self) -> None:
        """Read once how the show is streamed: purchase kind (free / rental...) and the audio language to ask for."""
        if getattr(self, "kind", None) and getattr(self, "audio", None):
            return
        try:
            detail = api.get_tvshow_detail(self.show_id, self.market)
            self.kind = api.purchase_kind(detail)
            self.audio = api.audio_language(self.market, detail)
        except Exception as e:
            logger.error(f"Could not read the stream info of '{self.show_id}', assuming a free title: {e}")
            self.kind = "avod"
            self.audio = None

    def collect_info_title(self) -> None:
        """Load the seasons attached to a Chili series catalogue entry."""
        try:
            show = api.get_tvshow_detail(self.show_id, self.market)
        except Exception as e:
            logger.error(f"Error fetching show '{self.show_id}': {e}")
            raise

        if not self.series_name or self.series_name == self.show_id:
            self.series_name = show.get("title") or self.show_id
        self.year = self.year or show.get("productionYear") or show.get("year")

        seasons = api.get_seasons(self.show_id, self.market)
        for index, entry in enumerate(seasons):
            season_id = entry.get("id")
            try:
                number = int(entry.get("orderIndex") or entry.get("season_number") or entry.get("number") or 0)
            except (ValueError, TypeError):
                number = 0
            if not number:
                number = index + 1
            if not season_id or number in self._season_ids:
                continue
            self._season_ids[number] = season_id
            season_name = entry.get("subtitle") or f"Stagione {number}"
            self.seasons_manager.add(
                Season(
                    id=season_id,
                    number=number,
                    name=season_name,
                    slug=season_id,
                    image=entry.get("coverUrl") or "",
                )
            )

    def collect_info_season(self, number_season: int) -> None:
        season = self.seasons_manager.get_season_by_number(number_season)
        if not season:
            logger.error(f"Season {number_season} not found")
            return

        season_id = self._season_ids.get(number_season) or season.id
        try:
            episodes = api.get_episodes(season_id, self.market)
        except Exception as e:
            logger.error(f"Error fetching episodes for season '{season_id}': {e}")
            raise

        try:
            episodes = sorted(
                episodes,
                key=lambda x: int(x.get("orderIndex") or x.get("number") or x.get("episode_number") or 0),
            )
        except (ValueError, TypeError):
            pass

        for ep in episodes:
            ep_id = ep.get("id")
            if not ep_id:
                continue
            subtitle = ep.get("subtitle") or ""
            match = re.search(r"(?:episode|episodio)\s*(\d+)", subtitle, re.IGNORECASE)
            episode_number = (
                ep.get("orderIndex")
                or ep.get("number")
                or ep.get("episode_number")
                or (int(match.group(1)) if match else None)
            )
            season.episodes.add(
                Episode(
                    id=ep_id,
                    name=ep.get("title") or ep.get("display_name") or subtitle or f"Episodio {episode_number}",
                    number=episode_number,
                    image=ep.get("coverUrl") or ep.get("wideCoverUrl") or "",
                    year=ep.get("productionYear") or ep.get("year"),
                    duration=ep.get("duration"),
                )
            )

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        with self._collect_lock:
            if not self.seasons_manager.seasons:
                self.collect_info_title()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        season = self.seasons_manager.get_season_by_number(season_number)
        if not season:
            logger.error(f"Season {season_number} not found")
            return []
        with self._collect_lock:
            if not season.episodes.episodes:
                self.collect_info_season(season_number)
        return season.episodes.episodes
