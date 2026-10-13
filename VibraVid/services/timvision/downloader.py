# 13.10.26
# timvision.downloader - Download via DASH (hapi playset/view)

import logging
import os
from typing import Any

from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.services._base import Entries, print_download_header
from VibraVid.services._base.output_path import movie_folder, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from .client import TimvisionClient
from .scrapper import GetSerieInfo

console = Console()
logger = logging.getLogger(__name__)

extension_output = config_manager.config.get("PROCESS", "extension")

_MPD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.timvision.it/",
    "Origin": "https://www.timvision.it",
}


def _dash_download(playback_info: dict, output_path: str):
    return DASH_Downloader(
        mpd_url=playback_info["mpd_url"],
        mpd_headers=_MPD_HEADERS,
        output_path=output_path,
        license_url=playback_info.get("license_url"),
        license_request_fn=playback_info.get("license_request_fn"),
        license_headers=playback_info.get("license_headers"),
        license_data=playback_info.get("license_data"),
        drm_preference=DRMType.PLAYREADY,
    ).start()


def download_film(select_title: Entries) -> Any:
    """Download a film from TIMvision."""
    start_message()
    print_download_header(select_title)

    client = TimvisionClient()
    content_id = getattr(select_title, "id", "") or getattr(select_title, "slug", "")
    if not content_id:
        console.print("[red]TIMvision: missing content id")
        return (None, True, "Missing content id")

    playback_info = client.get_playback_info(content_id)
    if not playback_info:
        console.print("[red]TIMvision: content not available for download (likely TVOD - pay-per-view)")
        return (None, True, "Content not available (TVOD)")

    path_components, filename = map_movie_path(select_title.name, getattr(select_title, "year", None))
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"
    output_path = os.path.join(movie_path, movie_name)

    return _dash_download(playback_info, output_path)


def download_series(
    select_title: Entries,
    season_selection: str = None,
    episode_selection: str = None,
    scrape_serie=None,
) -> None:
    """Download a series from TIMvision."""
    start_message()

    client = TimvisionClient()

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(
            getattr(select_title, "id", "") or getattr(select_title, "slug", ""),
            select_title.name,
            getattr(select_title, "year", None),
        )
    seasons_count = scrape_serie.getNumberSeason()

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        def download_video_callback(obj_episode, season_idx, episode_idx):
            content_id = getattr(obj_episode, "id", None)
            if not content_id:
                console.print("[red]TIMvision: missing episode content id")
                return (None, True, "Missing episode id")

            playback_info = client.get_playback_info(content_id)
            if not playback_info:
                console.print(
                    f"[red]TIMvision: S{season_idx}E{episode_idx} not available for download (likely TVOD)"
                )
                return (None, True, "Content not available (TVOD)")

            path_components, filename = map_episode_path(
                scrape_serie.series_name,
                getattr(scrape_serie, "year", None),
                season_idx,
                episode_idx,
                obj_episode.name,
            )
            episode_path = series_folder(*path_components)
            episode_name = f"{filename}.{extension_output}"
            output_path = os.path.join(episode_path, episode_name)

            return _dash_download(playback_info, output_path)

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
