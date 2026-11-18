# 21.03.25

from bs4 import BeautifulSoup

from VibraVid.services._base.object import Episode
from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.utils.os import os_manager

from .client import get_session_and_csrf


class ScrapSerie:
    def __init__(self, url, site_url):
        """Initialize the ScrapSerie object with the provided URL and setup the HTTP client."""
        self.url = url
        self.session_id, self.csrf_token = get_session_and_csrf()
        self.client = create_client(
            cookies={"sessionId": self.session_id},
            headers={"User-Agent": get_userAgent(), "csrf-token": self.csrf_token},
        )

        try:
            self.response = self.client.get(self.url)
            self.response.raise_for_status()

        except Exception as e:
            raise Exception(f"Failed to retrieve anime page: {str(e)}") from e

    def close(self):
        """Close the HTTP client session."""
        if self.client:
            self.client.close()

    def get_name(self):
        """Extract and return the name of the anime series."""
        soup = BeautifulSoup(self.response.content, "html.parser")
        return os_manager.get_sanitize_file(soup.find("h1", {"id": "anime-title"}).get_text(strip=True))

    def get_external_ids(self):
        """MyAnimeList and AniList ids linked from the detail page (None when absent)."""
        from VibraVid.utils.media.anime_naming import external_ids_from_html
        return external_ids_from_html(self.response.text)

    def get_episodes(self, nums=None):
        """Fetch and return the list of episodes, optionally filtering by specific episode numbers."""
        soup = BeautifulSoup(self.response.content, "html.parser")

        raw_eps = {}
        for data in soup.select("li.episode > a"):
            epNum = data.get("data-episode-num")
            epID = data.get("data-episode-id")
            play_href = data.get("href", "")

            if nums and epNum not in nums:
                continue

            ep_token = data.get("data-id", "")
            if epID not in raw_eps:
                raw_eps[epID] = Episode(
                    number=epNum, url="/api/episode/info", id=epID, play_url=play_href, token=ep_token
                )

        episodes = [episode_data for episode_data in raw_eps.values()]
        return episodes

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the anime."""
        return 1

    def getEpisodeSeasons(self, season_number: int = 1) -> list:
        """Get all episodes for a specific season."""
        return self.get_episodes()
