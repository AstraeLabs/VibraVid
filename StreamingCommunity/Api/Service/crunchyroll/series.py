# 16.03.25

import os
from urllib.parse import urlparse, parse_qs
from typing import Tuple


# External library
from rich.console import Console
from rich.prompt import Prompt


# Internal utilities
from StreamingCommunity.Util import os_manager, config_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
from StreamingCommunity.Api.Template.episode_manager import (
    manage_selection, 
    map_episode_title,
    validate_selection, 
    validate_episode_selection, 
    display_episodes_list,
    display_seasons_list
)
from StreamingCommunity.Lib.DASH.downloader import DASH_Downloader


# Logic
from .util.ScrapeSerie import GetSerieInfo
from .util.get_license import get_playback_session


# Variable
msg = Prompt()
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


def download_video(index_season_selected: int, index_episode_selected: int, scrape_serie: GetSerieInfo) -> Tuple[str, bool]:
    """
    Downloads a specific episode from a specified season.

    Parameters:
        - index_season_selected (int): Season number
        - index_episode_selected (int): Episode index
        - scrape_serie (GetSerieInfo): Scraper object with series information

    Returns:
        - str: Path to downloaded file
        - bool: Whether download was stopped
    """
    start_message()
    client = scrape_serie.client

    # Get episode information
    obj_episode = scrape_serie.selectEpisode(index_season_selected, index_episode_selected-1)
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} \\ [magenta]{obj_episode.get('name')}[/magenta] ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    mp4_name = f"{map_episode_title(scrape_serie.series_name, index_season_selected, index_episode_selected, obj_episode.get('name'))}.{extension_output}"
    mp4_path = os_manager.get_sanitize_path(os.path.join(site_constants.SERIES_FOLDER, scrape_serie.series_name, f"S{index_season_selected}"))

    # Generate mpd and license URLs
    url_id = obj_episode.get('url').split('/')[-1]

    # Get playback session
    try:
        # Get playback session with token for cleanup
        playback_result = get_playback_session(client, url_id)
        
        # Check if access was denied (403)
        if playback_result is None:
            console.print("[red]✗ Access denied: This episode requires a premium subscription")
            return None, False
        
        mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = playback_result
        
    except Exception as e:
        console.print(f"[red]✗ Error getting playback session: {str(e)}")
        return None, False
    
    parsed_url = urlparse(mpd_url)
    query_params = parse_qs(parsed_url.query)

    # Download the episode
    dash_process = DASH_Downloader(
        license_url='https://www.crunchyroll.com/license/v1/license/widevine',
        mpd_url=mpd_url,
        mpd_sub_list=mpd_list_sub,
        output_path=os.path.join(mp4_path, mp4_name),
    )
    dash_process.parse_manifest(custom_headers=mpd_headers)

    # Create headers for license request
    license_headers = mpd_headers.copy()
    license_headers.update({
        "x-cr-content-id": url_id,
        "x-cr-video-token": query_params['playbackGuid'][0],
    })

    if dash_process.download_and_decrypt(custom_headers=license_headers):
        dash_process.finalize_output()

    # Get final output path and status
    status = dash_process.get_status()

    if status['error'] is not None and status['path']:
        try: 
            os.remove(status['path'])
        except Exception: 
            pass

    # Delete episode stream to avoid TOO_MANY_ACTIVE_STREAMS
    playback_token = token or query_params.get('playbackGuid', [None])[0]
    if playback_token:
        client.delete_active_stream(url_id, playback_token)
        console.print("[dim]✓ Playback session closed[/dim]")

    return status['path'], status['stopped']

def download_episode(index_season_selected: int, scrape_serie: GetSerieInfo, download_all: bool = False, episode_selection: str = None) -> None:
    """
    Handle downloading episodes for a specific season.

    Parameters:
        - index_season_selected (int): Season number
        - scrape_serie (GetSerieInfo): Scraper object with series information
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
        for i_episode in range(1, episodes_count + 1):
            path, stopped = download_video(index_season_selected, i_episode, scrape_serie)

            if stopped:
                break

        console.print(f"\n[red]End downloaded [yellow]season: [red]{index_season_selected}.")

    else:
        if episode_selection is not None:
            last_command = episode_selection
            console.print(f"\n[cyan]Using provided episode selection: [yellow]{episode_selection}")

        else:
            last_command = display_episodes_list(episodes)
        
        # Prompt user for episode selection
        list_episode_select = manage_selection(last_command, episodes_count)
        list_episode_select = validate_episode_selection(list_episode_select, episodes_count)

        # Download selected episodes if not stopped
        for i_episode in list_episode_select:
            path, stopped = download_video(index_season_selected, i_episode, scrape_serie)

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
    scrape_serie = GetSerieInfo(select_season.url.split("/")[-1])
    seasons_count = scrape_serie.getNumberSeason()
    
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
        if len(list_season_select) > 1 or index_season_selected == "*":
            download_episode(i_season, scrape_serie, download_all=True)
        else:
            download_episode(i_season, scrape_serie, download_all=False, episode_selection=episode_selection)