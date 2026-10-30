# 30.10.26

import logging
import os

from VibraVid.core.downloader import HLS_Downloader, MP4_Downloader
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from .client import LoonexClient, ResolvedStream
from .scrapper import GetSerieInfo

logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def _download_stream(stream: ResolvedStream, output_dir: str, filename: str):
    """Hand a resolved stream to the HLS or progressive-MP4 downloader."""
    output_path = os.path.join(output_dir, filename)
    if stream.type == "hls":
        return HLS_Downloader(m3u8_url=stream.url, headers=stream.headers, output_path=output_path).start()

    return MP4_Downloader(url=stream.url, path=output_path, headers=stream.headers)


def download_film(select_title: Entries):
    """
    Downloads a film using the provided Entries information.

    Parameters:
        select_title (Entries): Class with info about film title.

    Return:
        - output path / downloader result
    """
    start_message()
    print_download_header(select_title)

    scrape_serie = GetSerieInfo(select_title.url, select_title.name, select_title.year)
    if not scrape_serie.is_movie():
        logger.error(f"No playable film found at {select_title.url}")
        return None

    stream = LoonexClient().resolve_stream(scrape_serie.movie_url)
    if not stream:
        logger.error(f"No stream found for film {select_title.name}")
        return None

    path_components, filename = map_movie_path(select_title.name, select_title.year)
    return _download_stream(stream, movie_folder(*path_components), f"{filename}.{extension_output}")


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """Downloads a specific episode of a series."""
    start_message()
    print_episode_header(scrape_serie.series_display_name, obj_episode.name, index_season_selected, index_episode_selected)

    stream = LoonexClient().resolve_stream(obj_episode.url)
    if not stream:
        logger.error(f"No stream found for episode {obj_episode.url}")
        return None

    path_components, filename = map_episode_path(
        scrape_serie.series_display_name,
        scrape_serie.year,
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    return _download_stream(stream, series_folder(*path_components), f"{filename}.{extension_output}")


def download_series(select_title: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_title.url, select_title.name, select_title.year, getattr(select_title, "tmdb_id", None))
        scrape_serie.getNumberSeason()

    # Some catalog entries are single films listed without the FILM badge
    if scrape_serie.is_movie():
        download_film(select_title)
        return

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
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
        seasons_count=len(scrape_serie.seasons_manager),
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
