# 29.10.26

import logging

from .base import Entries, Episode, Season
from .generic import GenericStreamingAPI

logger = logging.getLogger(__name__)


class YoutubeMusicAPI(GenericStreamingAPI):
    """YouTube Music: songs download directly, albums are a one-season tracklist."""
    site_name = "youtube_music"
    base_url = "https://music.youtube.com"
    entry_default_type = "song"
    log_label = "YouTube Music"

    def _build_scraper(self, media_item: Entries):
        return self._get_search_fn().get_module()._build_album_scraper(media_item)

    def get_series_metadata(self, media_item: Entries) -> list[Season] | None:
        """Tracklist of an album as one season; songs have none."""
        if str(getattr(media_item, "type", "")).lower() != "album":
            return None

        scraper = self.get_cached_scraper(media_item)
        if scraper is None:
            scraper = self._build_scraper(media_item)
            if scraper is None:
                logger.warning("YouTube Music album metadata skipped: no tracklist for '%s'", media_item.name)
                return None
            self.set_cached_scraper(media_item, scraper)

        seasons: list[Season] = []
        for season_obj in scraper.seasons_manager.seasons:
            tracks = scraper.getEpisodeSeasons(season_obj.number)
            seasons.append(
                Season(
                    number=season_obj.number,
                    name=season_obj.name,
                    episodes=[
                        Episode(
                            number=track.get("number") or (i + 1),
                            name=track.get("name", ""),
                            id=track.get("id"),
                            duration=round(track["duration_seconds"] / 60) if track.get("duration_seconds") else None,
                        )
                        for i, track in enumerate(tracks)
                    ],
                )
            )
        return seasons or None
