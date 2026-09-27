# 16.12.25

import os
import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.downloader.base import DownloadResult
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from .client import get_bearer_token, get_playback_url
from .scrapper import GetSerieInfo

console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def extract_content_id(url: str) -> str:
    """Extract content ID from Tubi TV URL"""
    # URL format: https://tubitv.com/movies/{content_id}/{slug}
    match = re.search(r"/movies/(\d+)/", url)
    if match:
        return match.group(1)
    return None


def download_film(select_title: Entries) -> DownloadResult:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name} \n")

    # Extract content ID from URL
    content_id = extract_content_id(select_title.url)
    if not content_id:
        console.print("[red]Error: Could not extract content ID from URL")
        return DownloadResult(None, True)

    # Get bearer token
    try:
        bearer_token = get_bearer_token()
    except Exception as e:
        console.print(f"[red]Error getting bearer token: {e}")
        return DownloadResult(None, True)

    # Get master playlist URL
    try:
        master_playlist, license_url, custom_headers = get_playback_url(content_id, bearer_token)
    except Exception as e:
        console.print(f"[red]Error getting playback URL: {e}")
        return DownloadResult(None, True)

    # Define the filename and path for the downloaded film
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    return DASH_Downloader(
        mpd_url=master_playlist,
        mpd_headers=custom_headers,
        output_path=os.path.join(movie_path, movie_name),
        license_url=license_url,
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, bearer_token):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"

    # Get master playlist URL
    try:
        master_playlist, license_url, custom_headers = get_playback_url(obj_episode.id, bearer_token)
    except Exception as e:
        console.print(f"[red]Error getting playback URL: {e}")
        return DownloadResult(None, True)

    # Download the episode
    return DASH_Downloader(
        mpd_url=master_playlist,
        mpd_headers=custom_headers,
        output_path=os.path.join(episode_path, episode_name),
        license_url=license_url,
    ).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    bearer_token = get_bearer_token()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_season.url, bearer_token, select_season.name)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie, bearer_token)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
