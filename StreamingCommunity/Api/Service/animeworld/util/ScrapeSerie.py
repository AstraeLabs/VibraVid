# 21.03.25

import logging


# External libraries
from bs4 import BeautifulSoup


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent
from StreamingCommunity.Util.os import os_manager


# Player
from ..site import get_session_and_csrf




class ScrapSerie:
    def __init__(self, url, site_url):
        """Initialize the ScrapSerie object with the provided URL and setup the HTTP client."""
        self.url = url
        self.session_id, self.csrf_token = get_session_and_csrf()
        self.client = create_client(
            cookies={"sessionId": self.session_id},
            headers={"User-Agent": get_userAgent(), "csrf-token": self.csrf_token}
        )

        try:
            self.response = self.client.get(self.url)
            self.response.raise_for_status()

        except Exception as e:
            raise Exception(f"Failed to retrieve anime page: {str(e)}")

    def get_name(self):
        """Extract and return the name of the anime series."""
        soup = BeautifulSoup(self.response.content, "html.parser")
        return os_manager.get_sanitize_file(soup.find("h1", {"id": "anime-title"}).get_text(strip=True))
    
    def get_episodes(self, nums=None):
        """Fetch and return the list of episodes, optionally filtering by specific episode numbers."""
        soup = BeautifulSoup(self.response.content, "html.parser")

        raw_eps = {}
        for data in soup.select('li.episode > a'):
            epNum = data.get('data-episode-num')
            epID = data.get('data-episode-id')

            if nums and epNum not in nums:
                continue

            if epID not in raw_eps:
                raw_eps[epID] = {
                    'number': epNum,
                    'link': f"/api/download/{epID}"
                }

        episodes = [episode_data for episode_data in raw_eps.values()]
        return episodes
    
    
    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the anime.
        Note: AnimeWorld typically doesn't have seasons, so returns 1.
        """
        return 1
    
    def getEpisodeSeasons(self, season_number: int = 1) -> list:
        """
        Get all episodes for a specific season.
        Note: For AnimeWorld, this returns all episodes as they're typically in one season.
        """
        return self.get_episodes()
        
    def selectEpisode(self, season_number: int = 1, episode_index: int = 0) -> dict:
        """
        Get information for a specific episode.
        """
        episodes = self.get_episodes()
        if not episodes or episode_index < 0 or episode_index >= len(episodes):
            logging.error(f"Episode index {episode_index} is out of range")
            return None
            
        return episodes[episode_index]