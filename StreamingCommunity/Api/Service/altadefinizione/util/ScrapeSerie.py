# 16.03.25

import logging


# External libraries
from bs4 import BeautifulSoup


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent
from StreamingCommunity.Api.Template.object import SeasonManager


class GetSerieInfo:
    def __init__(self, url):
        """
        Initialize the GetSerieInfo class for scraping TV series information.
        
        Args:
            - url (str): The URL of the streaming site.
        """
        self.headers = {'user-agent': get_userAgent()}
        self.url = url
        self.seasons_manager = SeasonManager()

    def collect_season(self) -> None:
        """
        Retrieve all episodes for all seasons.
        """
        response = create_client(headers=self.headers).get(self.url)
        soup = BeautifulSoup(response.text, "html.parser")
        self.series_name = soup.find("title").get_text(strip=True).split(" - ")[0]

        tt_holder = soup.find('div', id='tt_holder')

        # Find all seasons
        seasons_div = tt_holder.find('div', class_='tt_season')
        if not seasons_div:
            return

        season_list_items = seasons_div.find_all('li')
        for season_li in season_list_items:
            season_anchor = season_li.find('a')
            if not season_anchor:
                continue

            season_num = int(season_anchor.get_text(strip=True))
            season_name = f"Stagione {season_num}"

            # Create a new season
            current_season = self.seasons_manager.add_season({
                'number': season_num,
                'name': season_name
            })

            # Find episodes for this season
            tt_series_div = tt_holder.find('div', class_='tt_series')
            tab_content = tt_series_div.find('div', class_='tab-content')
            tab_pane = tab_content.find('div', id=f'season-{season_num}')

            episode_list_items = tab_pane.find_all('li')
            for ep_li in episode_list_items:
                ep_anchor = ep_li.find('a', id=lambda x: x and x.startswith(f'serie-{season_num}_'))
                if not ep_anchor:
                    continue

                ep_num_str = ep_anchor.get('data-num', '')
                try:
                    ep_num = int(ep_num_str.split('x')[1])
                except (IndexError, ValueError):
                    ep_num = int(ep_anchor.get_text(strip=True))

                ep_title = ep_anchor.get('data-title', '').strip()
                ep_url = ep_anchor.get('data-link', '').strip()

                # Prefer supervideo link from mirrors if available
                mirrors_div = ep_li.find('div', class_='mirrors')
                supervideo_url = None
                if mirrors_div:
                    supervideo_a = mirrors_div.find('a', class_='mr', text=lambda t: t and 'Supervideo' in t)
                    if supervideo_a:
                        supervideo_url = supervideo_a.get('data-link', '').strip()
                        
                if supervideo_url:
                    ep_url = supervideo_url

                if current_season:
                    current_season.episodes.add({
                        'number': ep_num,
                        'name': ep_title if ep_title else f"Episodio {ep_num}",
                        'url': ep_url
                    })


    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the series.
        """
        if not self.seasons_manager.seasons:
            self.collect_season()
            
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """
        Get all episodes for a specific season.
        """
        if not self.seasons_manager.seasons:
            self.collect_season()
            
        # Get season directly by its number
        season = self.seasons_manager.get_season_by_number(season_number)
        return season.episodes.episodes if season else []
        
    def selectEpisode(self, season_number: int, episode_index: int) -> dict:
        """
        Get information for a specific episode in a specific season.
        """
        episodes = self.getEpisodeSeasons(season_number)
        if not episodes or episode_index < 0 or episode_index >= len(episodes):
            logging.error(f"Episode index {episode_index} is out of range for season {season_number}")
            return None
            
        return episodes[episode_index]