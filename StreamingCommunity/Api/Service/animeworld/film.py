# 11.03.24

import os


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import os_manager
from StreamingCommunity.Util.message import start_message
from StreamingCommunity.Lib.MP4 import MP4_Downloader


# Logic class
from .util.ScrapeSerie import ScrapSerie
from StreamingCommunity.Api.Player.sweetpixel import VideoSource
from StreamingCommunity.Api.Template.config_loader import site_constant
from StreamingCommunity.Api.Template.object import MediaItem


# Variable
console = Console()


def download_film(select_title: MediaItem):
    """
    Function to download a film.

    Parameters:
        - id_film (int): The ID of the film.
        - title_name (str): The title of the film.
    """
    start_message()
    
    scrape_serie = ScrapSerie(select_title.url, site_constant.FULL_URL)
    episodes = scrape_serie.get_episodes() 

    # Get episode information
    episode_data = episodes[0]
    console.print(f"\n[yellow]Download: [red]{site_constant.SITE_NAME} ([cyan]{scrape_serie.get_name()}) \n")

    # Define filename and path for the downloaded video
    serie_name_with_year = os_manager.get_sanitize_file(scrape_serie.get_name(), select_title.date)
    mp4_name = f"{serie_name_with_year}.mp4"
    mp4_path = os.path.join(site_constant.ANIME_FOLDER, serie_name_with_year.replace('.mp4', ''))

    # Create output folder
    os_manager.create_path(mp4_path)

    # Get video source for the episode
    video_source = VideoSource(site_constant.FULL_URL, episode_data, scrape_serie.session_id, scrape_serie.csrf_token)
    mp4_link = video_source.get_playlist()

    # Start downloading
    path, kill_handler = MP4_Downloader(
        url=str(mp4_link).strip(),
        path=os.path.join(mp4_path, mp4_name)
    )

    return path, kill_handler