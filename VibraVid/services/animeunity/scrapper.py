# 16.11.26

import logging
import threading

from VibraVid.services._base.object import Episode, EpisodeManager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)


class ScrapeSerieAnime:
    def __init__(self, url: str):
        """
        Initialize the media scraper for a specific website.

        Args:
            url (str): Url of the streaming site
        """
        self.is_series = False
        self.headers = get_headers()
        self.url = url.rstrip("/")
        self.episodes_cache = None
        self._fetch_lock = threading.Lock()

    def setup(self, version: str = None, media_id: int = None, series_name: str = None):
        self.version = version
        self.media_id = media_id

        if series_name is not None:
            self.is_series = True
            self.series_name = series_name
            self.obj_episode_manager: EpisodeManager = EpisodeManager()

    def get_external_ids(self) -> tuple[str | None, str | None]:
        """MyAnimeList and AniList ids linked from the detail page (the search API no longer returns them)."""
        from VibraVid.utils.media.anime_naming import external_ids_from_html

        slug = getattr(self, "series_name", None)
        if not self.media_id or not slug:
            return None, None
        try:
            with create_client(headers=self.headers) as client:
                response = client.get(f"{self.url}/anime/{self.media_id}-{slug}")
            response.raise_for_status()
            return external_ids_from_html(response.text)
        except Exception as error:
            logger.warning(f"[animeunity] external ids lookup failed for {self.media_id}-{slug}: {error}")
            return None, None

    def get_count_episodes(self):
        """Total number of episodes available for the selected media."""
        with self._fetch_lock:
            if self.episodes_cache is None:
                self._fetch_all_episodes()

        if self.episodes_cache:
            return len(self.episodes_cache)
        return None

    def _fetch_all_episodes(self):
        """Fetch every episode through the site API, following the ``start`` pagination."""
        try:
            all_episodes = []
            start = 0
            while True:
                params = {"start": start} if start else None
                with create_client(headers=self.headers) as client:
                    response = client.get(f"{self.url}/api/anime/{self.media_id}/episodes", params=params)
                response.raise_for_status()

                payload = response.json()
                chunk = payload.get("episodes") or []
                total = payload.get("total")
                all_episodes.extend(chunk)

                if not chunk:
                    break
                start += len(chunk)
                if total is not None and len(all_episodes) >= int(total):
                    break

            self.episodes_cache = all_episodes
        except Exception as e:
            logger.error(f"Error fetching all episodes: {e}")
            self.episodes_cache = None

    def get_info_episode(self, index_ep: int) -> Episode:
        """Episode metadata from the cached list."""
        with self._fetch_lock:
            if self.episodes_cache is None:
                self._fetch_all_episodes()

        if self.episodes_cache and 0 <= index_ep < len(self.episodes_cache):
            ep = self.episodes_cache[index_ep]
            return Episode(id=ep.get("id"), number=ep.get("number"), name=f"Episode {ep.get('number')}")
        return None

    def get_video_url(self, episode_id: int, prefer_language: str = "ita") -> str | None:
        """Direct MP4 url for an episode, best available quality."""
        try:
            with create_client(headers=self.headers) as client:
                response = client.get(f"{self.url}/api/episodes/{episode_id}/downloads")
            response.raise_for_status()
            entries = response.json() or []
        except Exception as error:
            logger.error(f"[animeunity] downloads list failed for episode {episode_id}: {error}")
            return None

        if not entries:
            logger.error(f"[animeunity] no downloadable video for episode {episode_id}")
            return None

        entry = next(
            (item for item in entries if str(item.get("language") or "").lower() == prefer_language),
            entries[0],
        )
        quality = max((int(r.get("quality") or 0) for r in (entry.get("renditions") or [])), default=1080)

        try:
            with create_client(headers=self.headers) as client:
                redirect = client.get(
                    f"{self.url}/episode-videos/{entry.get('id')}/download",
                    params={"quality": quality},
                    allow_redirects=False,
                )
        except Exception as error:
            logger.error(f"[animeunity] download redirect failed for episode {episode_id}: {error}")
            return None

        location = redirect.headers.get("location") or redirect.headers.get("Location")
        if redirect.status_code in (301, 302, 303, 307, 308) and location:
            return location

        logger.error(f"[animeunity] unexpected download response: HTTP {redirect.status_code}")
        return None

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the anime."""
        return 1
