# 26.11.2025

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from .client import (
    get_playback_dash_episode,
    get_playback_url_episode,
    get_playready_license,
)
from .scrapper import GetSerieInfo, GetSerieInfoBySlug

msg = Prompt()
console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries):
    """Download a Pluto TV movie."""
    start_message()
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name}\n"
    )

    path_components, filename = map_movie_path(select_title.name, getattr(select_title, "year", None))
    movie_path = movie_folder(*path_components)
    output_path = os.path.join(movie_path, f"{filename}.{extension_output}")

    protocol = (context_tracker.site_options or {}).get("protocol") or "hls"
    if protocol == "dash":
        content_ids = {"movie_id": select_title.id, "regione": "IT"}
        mpd_url, mpd_headers = get_playback_dash_episode(select_title.id, content_ids)
        license_url, license_headers = get_playready_license(mpd_headers)
        return DASH_Downloader(
            mpd_url=mpd_url,
            mpd_headers=mpd_headers,
            license_url=license_url,
            license_headers=license_headers,
            output_path=output_path,
            drm_preference=DRMType.PLAYREADY,
        ).start()

    content_ids = {"movie_id": select_title.id, "regione": "IT"}
    m3u8_url = get_playback_url_episode(select_title.id, content_ids)
    return HLS_Downloader(m3u8_url=m3u8_url, output_path=output_path).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define output path
    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"

    # Get playback information
    content_ids = {"episode_id": obj_episode.id, "regione": "IT"}
    output_path = os.path.join(episode_path, episode_name)

    protocol = (context_tracker.site_options or {}).get("protocol") or "hls"
    if protocol == "dash":
        mpd_url, mpd_headers = get_playback_dash_episode(obj_episode.id, content_ids)
        license_url, license_headers = get_playready_license(mpd_headers)
        return DASH_Downloader(
            mpd_url=mpd_url,
            mpd_headers=mpd_headers,
            license_url=license_url,
            license_headers=license_headers,
            output_path=output_path,
            drm_preference=DRMType.PLAYREADY,
        ).start()

    m3u8_url = get_playback_url_episode(obj_episode.id, content_ids)
    return HLS_Downloader(m3u8_url=m3u8_url, output_path=output_path).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    if not scrape_serie:
        if getattr(select_season, "slug", None):
            scrape_serie = GetSerieInfoBySlug(select_season.slug)
        else:
            url = f"https://service-vod.clusters.pluto.tv/v4/vod/series/{select_season.id}/seasons"
            scrape_serie = GetSerieInfo(url)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        # Create callback for downloading individual videos
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

        # Use the process_episode_download function
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    # Use the process_season_selection function
    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
