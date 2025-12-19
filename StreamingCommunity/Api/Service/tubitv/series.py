# 16.12.25

import os
from typing import Tuple


# External library
from rich.console import Console
from rich.prompt import Prompt


# Internal utilities
from StreamingCommunity.Util import config_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
from StreamingCommunity.Api.Template.episode_manager import (
    manage_selection, 
    map_episode_title, 
    validate_selection, 
    validate_episode_selection, 
    display_episodes_list,
    display_seasons_list
)
from StreamingCommunity.Lib.HLS import HLS_Downloader


# Logic
from .util.ScrapeSerie import GetSerieInfo
from .util.get_license import get_bearer_token, get_playback_url


# Variable
msg = Prompt()
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


def download_video(index_season_selected: int, index_episode_selected: int, scrape_serie: GetSerieInfo, bearer_token: str) -> Tuple[str, bool]:
    """
    Downloads a specific episode from the specified season.

    Parameters:
        - index_season_selected (int): Season number
        - index_episode_selected (int): Episode index
        - scrape_serie (GetSerieInfo): Scraper object with series information
        - bearer_token (str): Bearer token for authentication

    Returns:
        - str: Path to downloaded file
        - bool: Whether download was stopped
    """
    start_message()

    # Get episode information
    obj_episode = scrape_serie.selectEpisode(index_season_selected, index_episode_selected-1)
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} \\ [magenta]{obj_episode.name}[/magenta] ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    mp4_name = f"{map_episode_title(scrape_serie.series_name, index_season_selected, index_episode_selected, obj_episode.name)}.{extension_output}"
    mp4_path = os.path.join(site_constants.SERIES_FOLDER, scrape_serie.series_name, f"S{index_season_selected}")

    # Get master playlist URL
    try:
        master_playlist, license_url = get_playback_url(obj_episode.id, bearer_token)
    except Exception as e:
        console.print(f"[red]Error getting playback URL: {e}")
        return None, True

    # Download the episode
    hls_process = HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(mp4_path, mp4_name),
        license_url=license_url
    ).start()

    if hls_process['error'] is not None:
        try: 
            os.remove(hls_process['path'])
        except Exception: 
            pass

    return hls_process['path'], hls_process['stopped']


def download_episode(index_season_selected: int, scrape_serie: GetSerieInfo, bearer_token: str, download_all: bool = False, episode_selection: str = None) -> None:
    """
    Handle downloading episodes for a specific season.

    Parameters:
        - index_season_selected (int): Season number
        - scrape_serie (GetSerieInfo): Scraper object with series information
        - bearer_token (str): Bearer token for authentication
        - download_all (bool): Whether to download all episodes
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
    """
    # Get episodes for the selected season
    episodes = scrape_serie.getEpisodeSeasons(index_season_selected)
    episodes_count = len(episodes)

    if episodes_count == 0:
        console.print(f"[red]No episodes found for season {index_season_selected}")
        return

    if download_all:
        # Download all episodes in the season
        for i_episode in range(1, episodes_count + 1):
            path, stopped = download_video(index_season_selected, i_episode, scrape_serie, bearer_token)

            if stopped:
                break

        console.print(f"\n[red]End downloaded [yellow]season: [red]{index_season_selected}.")

    else:
        # Display episodes list and manage user selection
        if episode_selection is None:
            last_command = display_episodes_list(episodes)
        else:
            last_command = episode_selection
            console.print(f"\n[cyan]Using provided episode selection: [yellow]{episode_selection}")
        
        # Validate the selection
        list_episode_select = manage_selection(last_command, episodes_count)
        list_episode_select = validate_episode_selection(list_episode_select, episodes_count)

        # Download selected episodes if not stopped
        for i_episode in list_episode_select:
            path, stopped = download_video(index_season_selected, i_episode, scrape_serie, bearer_token)

            if stopped:
                break


def download_series(select_season: MediaItem, season_selection: str = None, episode_selection: str = None) -> None:
    """
    Handle downloading a complete series.

    Parameters:
        - select_season (MediaItem): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection that bypasses manual input
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
    """
    start_message()
    bearer_token = get_bearer_token()

    # Init class
    scrape_serie = GetSerieInfo(select_season.url, bearer_token, select_season.name)

    # Collect information about season
    scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    # If season_selection is provided, use it instead of asking for input
    if season_selection is None:
        index_season_selected = display_seasons_list(scrape_serie.seasons_manager)
    else:
        index_season_selected = season_selection
        console.print(f"\n[cyan]Using provided season selection: [yellow]{season_selection}")

    # Validate the selection
    list_season_select = manage_selection(index_season_selected, seasons_count)
    list_season_select = validate_selection(list_season_select, seasons_count)

    # Loop through the selected seasons and download episodes
    for i_season in list_season_select:
        try:
            season = scrape_serie.seasons_manager.seasons[i_season - 1]
        except IndexError:
            console.print(f"[red]Season index {i_season} not found! Available seasons: {[s.number for s in scrape_serie.seasons_manager.seasons]}")
            continue
        
        season_number = season.number

        if len(list_season_select) > 1 or index_season_selected == "*":
            download_episode(season_number, scrape_serie, bearer_token, download_all=True)
        else:
            download_episode(season_number, scrape_serie, bearer_token, download_all=False, episode_selection=episode_selection)