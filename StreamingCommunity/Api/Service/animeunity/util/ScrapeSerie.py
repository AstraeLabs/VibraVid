# 01.03.24

import logging


# Internal utilities
from StreamingCommunity.Util.http_client import create_client_curl, get_headers
from StreamingCommunity.Api.Template.object import EpisodeManager, Episode


class ScrapeSerieAnime:
    def __init__(self, url: str):
        """
        Initialize the media scraper for a specific website.
        
        Args:
            url (str): Url of the streaming site
        """
        self.is_series = False
        self.headers = get_headers()
        self.url = url
        self.episodes_cache = None

    def setup(self, version: str = None, media_id: int = None, series_name: str = None):
        self.version = version
        self.media_id = media_id

        if series_name is not None:
            self.is_series = True
            self.series_name = series_name
            self.obj_episode_manager: EpisodeManager = EpisodeManager()
            
    def get_count_episodes(self):
        """
        Retrieve total number of episodes for the selected media.
        This includes partial episodes (like episode 6.5).
        
        Returns:
            int: Total episode count including partial episodes
        """
        if self.episodes_cache is None:
            self._fetch_all_episodes()
            
        if self.episodes_cache:
            return len(self.episodes_cache)
        return None
    
    def _fetch_all_episodes(self):
        """
        Fetch all episodes data at once and cache it
        """
        try:
            # Get initial episode count
            response = create_client_curl(headers=self.headers).get(f"{self.url}/info_api/{self.media_id}/")
            response.raise_for_status()
            initial_count = response.json()["episodes_count"]
            
            all_episodes = []
            start_range = 1
            
            # Fetch episodes in chunks
            while start_range <= initial_count:
                end_range = min(start_range + 119, initial_count)

                params={
                    "start_range": start_range,
                    "end_range": end_range
                }
                
                response = create_client_curl(headers=self.headers).get(f"{self.url}/info_api/{self.media_id}/1", params=params)
                response.raise_for_status()

                chunk_episodes = response.json().get("episodes", [])
                all_episodes.extend(chunk_episodes)
                start_range = end_range + 1
            
            self.episodes_cache = all_episodes
        except Exception as e:
            logging.error(f"Error fetching all episodes: {e}")
            self.episodes_cache = None

    def get_info_episode(self, index_ep: int) -> Episode:
        """
        Get episode info from cache
        """
        if self.episodes_cache is None:
            self._fetch_all_episodes()
            
        if self.episodes_cache and 0 <= index_ep < len(self.episodes_cache):
            return Episode(self.episodes_cache[index_ep])
        return None


    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the anime.
        Note: AnimeUnity typically doesn't have seasons, so returns 1.
        """
        return 1
        
    def selectEpisode(self, season_number: int = 1, episode_index: int = 0) -> Episode:
        """
        Get information for a specific episode.
        """
        return self.get_info_episode(episode_index)
