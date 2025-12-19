# 11.03.24

import os
from typing import Tuple


# External library
from rich.console import Console
from rich.prompt import Prompt


# Internal utilities
from StreamingCommunity.Util import os_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
from StreamingCommunity.Api.Template.episode_manager import manage_selection, dynamic_format_number
from StreamingCommunity.Lib.MP4 import MP4_Downloader


# Logic
from .util.ScrapeSerie import ScrapSerie
from StreamingCommunity.Api.Player.sweetpixel import VideoSource


# Variable
console = Console()
msg = Prompt()
KILL_HANDLER = bool(False)


def download_episode(index_select: int, scrape_serie: ScrapSerie) -> Tuple[str,bool]:
    """
    Downloads the selected episode.

    Parameters:
        - index_select (int): Index of the episode to download.

    Return:
        - str: output path
        - bool: kill handler status
    """
    start_message()

    # Get episode information
    episode_data = scrape_serie.selectEpisode(1, index_select)
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.get_name()} ([cyan]E{str(index_select+1)}) \n")

    # Define filename and path for the downloaded video
    mp4_name = f"{scrape_serie.get_name()}_EP_{dynamic_format_number(str(index_select+1))}.mp4"
    mp4_path = os.path.join(site_constants.ANIME_FOLDER, scrape_serie.get_name())

    # Create output folder
    os_manager.create_path(mp4_path)

    # Get video source for the episode
    video_source = VideoSource(site_constants.FULL_URL, episode_data, scrape_serie.session_id, scrape_serie.csrf_token)
    mp4_link = video_source.get_playlist()

    # Start downloading
    path, kill_handler = MP4_Downloader(
        url=str(mp4_link).strip(),
        path=os.path.join(mp4_path, mp4_name)
    )

    return path, kill_handler


def download_series(select_title: MediaItem, episode_selection: str = None):
    """
    Function to download episodes of a TV series.

    Parameters:
        - select_title (MediaItem): The selected media item
        - episode_selection (str, optional): Episode selection input that bypasses manual input
    """
    start_message()

    # Create scrap instance
    scrape_serie = ScrapSerie(select_title.url, site_constants.FULL_URL)
    episodes = scrape_serie.get_episodes() 

    # Get episode count
    console.print(f"\n[green]Episodes count: [red]{len(episodes)}")

    # Display episodes list and get user selection
    if episode_selection is None:
        last_command = msg.ask("\n[cyan]Insert media [red]index [yellow]or [red]* [cyan]to download all media [yellow]or [red]1-2 [cyan]or [red]3-* [cyan]for a range of media")
    else:
        last_command = episode_selection
        console.print(f"\n[cyan]Using provided episode selection: [yellow]{episode_selection}")

    list_episode_select = manage_selection(last_command, len(episodes))

    # Download selected episodes
    if len(list_episode_select) == 1 and last_command != "*":
        path, _ = download_episode(list_episode_select[0]-1, scrape_serie)
        return path

    # Download all selected episodes
    else:
        kill_handler = False
        for i_episode in list_episode_select:
            if kill_handler:
                break
            _, kill_handler = download_episode(i_episode-1, scrape_serie)