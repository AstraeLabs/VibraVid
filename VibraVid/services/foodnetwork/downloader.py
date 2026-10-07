# 26.11.25

import os

from rich.console import Console

from VibraVid.core.downloader import HLS_Downloader
from VibraVid.services._base import Entries, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from ..realtime.client import get_bearer_token, get_playback_url
from ..realtime.scrapper import GetSerieInfo

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    print_episode_header(scrape_serie.series_name, obj_episode.name, index_season_selected, index_episode_selected)

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

    # Get m3u8 playlist
    bearer_token = get_bearer_token()
    master_playlist = get_playback_url(obj_episode.id, bearer_token, False, obj_episode.channel)

    # Download the episode
    return HLS_Downloader(m3u8_url=master_playlist, output_path=os.path.join(episode_path, episode_name)).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_season.url)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

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
