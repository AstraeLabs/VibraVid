# 13.06.24

import logging
from typing import List, Dict


# External libraries
from bs4 import BeautifulSoup


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent
from StreamingCommunity.Api.Template.object import SeasonManager, MediaItem



class GetSerieInfo:
    def __init__(self, dict_serie: MediaItem) -> None:
        """
        Initializes the GetSerieInfo object with default values.

        Parameters:
            dict_serie (MediaItem): Dictionary containing series information.
        """
        self.headers = {'user-agent': get_userAgent()}
        self.url = dict_serie.url
        self.tv_name = None
        self.seasons_manager = SeasonManager()

    def get_seasons_number(self) -> int:
        """
        Retrieves the number of seasons of a TV series and populates the seasons_manager.

        Returns:
            int: Number of seasons of the TV series. Returns -1 if parsing fails.
        """
        try:
            response = create_client(headers=self.headers).get(self.url)
            response.raise_for_status()

            # Find the seasons container
            soup = BeautifulSoup(response.text, "html.parser")
            table_content = soup.find('div', class_="tt_season")
            season_elements = table_content.find_all("li")
            
            # Try to get the title, with fallback
            self.tv_name = soup.find('h1', class_="front_title").get_text(strip=True) if soup.find('h1', class_="front_title") else "Unknown Series"

            # Clear existing seasons and add new ones to SeasonManager
            self.seasons_manager.seasons = []
            for idx, season_element in enumerate(season_elements, start=1):
                self.seasons_manager.add_season({
                    'id': idx,
                    'number': idx,
                    'name': f"Season {idx}",
                    'slug': f"season-{idx}",
                })

            return len(season_elements)

        except Exception as e:
            logging.error(f"Error parsing HTML page: {str(e)}")
            return -1

    def get_episode_number(self, n_season: int) -> List[Dict[str, str]]:
        """
        Retrieves the episodes for a specific season.

        Parameters:
            n_season (int): The season number.

        Returns:
            List[Dict[str, str]]: List of dictionaries containing episode information.
        """
        try:
            response = create_client(headers=self.headers).get(self.url)
            response.raise_for_status()

            # Parse HTML content of the page
            soup = BeautifulSoup(response.text, "html.parser")

            # Find the container of episodes for the specified season
            table_content = soup.find('div', class_="tab-pane", id=f"season-{n_season}")
            episode_content = table_content.find_all("li")
            list_dict_episode = []

            for episode_div in episode_content:
                episode_link = episode_div.find("a")
                if not episode_link:
                    continue
                
                # Extract episode information from data attributes
                data_num = episode_link.get("data-num", "")
                data_link = episode_link.get("data-link", "")
                data_title = episode_link.get("data-title", "")
                
                # Parse episode number from data-num
                episode_number = data_num.split('x')[-1] if 'x' in data_num else data_num
                
                # Use data-title if available
                episode_name = data_title if data_title else f"Episodio {episode_number}"

                obj_episode = {
                    'number': episode_number,
                    'name': episode_name,
                    'url': data_link,
                    'id': episode_number
                }
                list_dict_episode.append(obj_episode)

            return list_dict_episode
        
        except Exception as e:
            logging.error(f"Error parsing HTML page: {e}")

        return []

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the series.
        """
        if not self.seasons_manager.seasons:
            return self.get_seasons_number()
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """
        Get all episodes for a specific season.
        """
        episodes = self.get_episode_number(season_number)
        
        if not episodes:
            logging.error(f"No episodes found for season {season_number}")
            return []
        
        return episodes
        
    def selectEpisode(self, season_number: int, episode_index: int) -> dict:
        """
        Get information for a specific episode in a specific season.
        """
        episodes = self.get_episode_number(season_number)
        
        if not episodes or episode_index < 0 or episode_index >= len(episodes):
            logging.error(f"Episode index {episode_index} is out of range for season {season_number}")
            return None
            
        return episodes[episode_index]
