# 16.12.25

import os
import re
from typing import Tuple


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util import os_manager, config_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
from StreamingCommunity.Lib.HLS import HLS_Downloader


# Logic
from .util.get_license import get_bearer_token, get_playback_url


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


def extract_content_id(url: str) -> str:
    """Extract content ID from Tubi TV URL"""
    # URL format: https://tubitv.com/movies/{content_id}/{slug}
    match = re.search(r'/movies/(\d+)/', url)
    if match:
        return match.group(1)
    return None


def download_film(select_title: MediaItem) -> Tuple[str, bool]:
    """
    Downloads a film using the provided MediaItem information.

    Parameters:
        - select_title (MediaItem): The media item containing film information

    Return:
        - str: Path to downloaded file
        - bool: Whether download was stopped
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Extract content ID from URL
    content_id = extract_content_id(select_title.url)
    if not content_id:
        console.print("[red]Error: Could not extract content ID from URL")
        return None, True

    # Get bearer token
    try:
        bearer_token = get_bearer_token()
    except Exception as e:
        console.print(f"[red]Error getting bearer token: {e}")
        return None, True

    # Get master playlist URL
    try:
        master_playlist, license_url = get_playback_url(content_id, bearer_token)
    except Exception as e:
        console.print(f"[red]Error getting playback URL: {e}")
        return None, True

    # Define the filename and path for the downloaded film
    mp4_name = f"{os_manager.get_sanitize_file(select_title.name, select_title.date)}.{extension_output}"
    mp4_path = os.path.join(site_constants.MOVIE_FOLDER, mp4_name.replace(f".{extension_output}", ""))

    # HLS Download
    r_proc = HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(mp4_path, mp4_name),
        license_url=license_url
    ).start()

    return r_proc['path'], r_proc['stopped']