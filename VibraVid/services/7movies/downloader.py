# 07.10.26

import logging
import os

from VibraVid.core.downloader import Generic_Downloader, HLS_Downloader, MP4_Downloader
from VibraVid.services._base import (
    Entries,
    movie_folder,
    print_download_header,
    print_episode_header,
    series_folder,
)
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from .client import get_stream
from .scrapper import GetSerieInfo

logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def _start_download(stream_url: str, stream_headers: dict, stream_type: str, extra_tracks: list, output_path: str):
    """Start the download process based on the stream type and extra tracks."""
    if not extra_tracks:
        if stream_type == "mp4":
            return MP4_Downloader(url=stream_url, path=output_path, headers=stream_headers)
        return HLS_Downloader(m3u8_url=stream_url, headers=stream_headers or None, output_path=output_path).start()

    sources = [{"url": stream_url, "type": "video", "headers": stream_headers}]
    for track in extra_tracks:
        sources.append(
            {
                "url": track["url"],
                "type": track.get("type", "audio"),
                "language": track.get("language"),
                "name": track.get("name"),
            }
        )
    return Generic_Downloader(sources=sources, output_path=output_path).start()


def download_film(select_title: Entries):
    """Download a movie from 7Movies."""
    start_message()
    print_download_header(select_title, year=select_title.year)

    tmdb_id = getattr(select_title, "id", None) or getattr(select_title, "tmdb_id", None)
    if not tmdb_id:
        raise ValueError(f"[7Movies] No TMDB ID for '{select_title.name}'")

    stream_url, stream_headers, stream_type, extra_tracks = get_stream(int(tmdb_id), "movie")
    logger.info(f"[7Movies] Stream ({stream_type}): {stream_url}")

    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"
    output_path = os.path.join(movie_path, movie_name)

    return _start_download(stream_url, stream_headers, stream_type, extra_tracks, output_path)


def download_episode(obj_episode, index: int, scrape_serie: GetSerieInfo, season_number: int):
    """Download a single episode from 7Movies."""
    start_message()
    print_episode_header(
        scrape_serie.series_name,
        obj_episode.name,
        season_number,
        int(obj_episode.number),
        year=scrape_serie.series_year,
    )

    stream_url, stream_headers, stream_type, extra_tracks = get_stream(
        scrape_serie.tmdb_id, "tv", season=season_number, episode=int(obj_episode.number)
    )
    logger.info(f"[7Movies] Stream ({stream_type}): {stream_url}")

    path_components, filename = map_episode_path(
        series_name=scrape_serie.series_name,
        series_year=scrape_serie.series_year,
        season_number=season_number,
        episode_number=int(obj_episode.number),
        episode_name=obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"
    output_path = os.path.join(episode_path, episode_name)

    return _start_download(stream_url, stream_headers, stream_type, extra_tracks, output_path)


def download_series(
    select_title: Entries,
    season_selection: str = None,
    episode_selection: str = None,
    scrape_serie: GetSerieInfo = None,
):
    """Download selected episodes from 7Movies."""
    start_message()

    tmdb_id = getattr(select_title, "id", None) or getattr(select_title, "tmdb_id", None)
    if not tmdb_id:
        raise ValueError(f"[7Movies] No TMDB ID for '{select_title.name}'")

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(int(tmdb_id), select_title.name)
    seasons_count = scrape_serie.getNumberSeason()

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, episode_idx, scrape_serie, season_idx)

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
