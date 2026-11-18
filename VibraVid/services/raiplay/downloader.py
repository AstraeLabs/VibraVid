# 21.05.24

import logging
import os
import re

from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader
from VibraVid.player.mediapolisvod import VideoSource
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

from .client import generate_license_url
from .scrapper import GetSerieInfo

console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def _subtitle_other_tracks(subtitle_url: str | None, lang: str = "it") -> list:
    """Wrap the RaiPlay .srt sidecar (if any) as an other_tracks entry the downloaders already know how to fetch/merge."""
    if not subtitle_url:
        return []
    return [{"type": "subtitle", "url": subtitle_url, "language": lang}]


def fix_manifest_url(manifest_url: str) -> str:
    """
    Fixes RaiPlay manifest URLs to include all available quality levels.

    Args:
        manifest_url (str): Original manifest URL from RaiPlay
    """
    STANDARD_QUALITIES = "1200,1800,2400,3600,5000"
    pattern = r"(_,[\d,]+)(/playlist\.m3u8)"

    # Check if URL contains quality specification
    match = re.search(pattern, manifest_url)

    if match:
        fixed_url = re.sub(pattern, f"_,{STANDARD_QUALITIES}\\2", manifest_url)
        return fixed_url

    return manifest_url


def _extract_film_content_id(first_item_url: str) -> str:
    """Extract the relinker content id (used for the Widevine license) from a film ContentItem JSON."""
    with create_client(headers=get_headers()) as client:
        response = client.get(first_item_url)
    response.raise_for_status()

    content_url = (response.json().get("video", {}) or {}).get("content_url", "") or ""
    return content_url.split("=")[1] if "=" in content_url else ""


def download_film(select_title: Entries) -> tuple[str, bool]:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    print_download_header(select_title)

    # Resolve the film's video ContentItem and extract the master playlist
    with create_client(headers=get_headers()) as client:
        response = client.get(select_title.url + ".json")
    first_item_path = "https://www.raiplay.it" + response.json().get("first_item_path")
    master_playlist, subtitle_url = VideoSource.extract_m3u8_url(first_item_path)

    # Define the filename and path for the downloaded film
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    # Extract the content ID for license generation
    content_id = _extract_film_content_id(first_item_path)
    full_license_url = generate_license_url(content_id)

    other_tracks = _subtitle_other_tracks(subtitle_url)

    # HLS
    if ".mpd" not in master_playlist:
        return HLS_Downloader(
            m3u8_url=fix_manifest_url(master_playlist),
            license_url=full_license_url,
            output_path=os.path.join(movie_path, movie_name),
            other_tracks=other_tracks,
            has_drm=True,
        ).start()

    # MPD
    else:
        license_headers = (
            {
                "nv-authorizations": full_license_url.split("?")[1].split("=")[1],
                "user-agent": get_userAgent(),
            }
            if full_license_url
            else {}
        )

        return DASH_Downloader(
            mpd_url=master_playlist,
            license_url=full_license_url.split("?")[0] if full_license_url else None,
            license_headers=license_headers,
            output_path=os.path.join(movie_path, movie_name),
            other_tracks=other_tracks,
        ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    print_episode_header(scrape_serie.series_name, obj_episode.name, index_season_selected, index_episode_selected)

    # Define filename and path
    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"

    # Get streaming URL
    master_playlist, subtitle_url = VideoSource.extract_m3u8_url(obj_episode.url)

    if not master_playlist:
        logger.error(f"Error: Could not extract streaming URL for {obj_episode.name}")
        return False

    other_tracks = _subtitle_other_tracks(subtitle_url)

    # HLS
    if ".mpd" not in master_playlist:
        return HLS_Downloader(
            m3u8_url=fix_manifest_url(master_playlist),
            output_path=os.path.join(episode_path, episode_name),
            other_tracks=other_tracks,
        ).start()

    # MPD
    else:
        full_license_url = generate_license_url(obj_episode.mpd_id)
        license_headers = (
            {
                "nv-authorizations": full_license_url.split("?")[1].split("=")[1],
                "user-agent": get_userAgent(),
            }
            if full_license_url
            else {}
        )

        return DASH_Downloader(
            mpd_url=master_playlist,
            license_url=full_license_url.split("?")[0] if full_license_url else None,
            license_headers=license_headers,
            output_path=os.path.join(episode_path, episode_name),
            other_tracks=other_tracks,
        ).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_season.path_id)
        scrape_serie.collect_info_title()
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
