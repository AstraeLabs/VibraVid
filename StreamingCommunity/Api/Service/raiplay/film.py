# 21.05.24

import os
from typing import Tuple


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import os_manager
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.headers import get_headers
from StreamingCommunity.Util.http_client import create_client
from StreamingCommunity.Util.message import start_message
from StreamingCommunity.Lib.DASH.downloader import DASH_Downloader
from StreamingCommunity.Lib.HLS import HLS_Downloader


# Logic class
from .util.get_license import generate_license_url
from StreamingCommunity.Api.Player.mediapolisvod import VideoSource
from StreamingCommunity.Api.Template.config_loader import site_constant
from StreamingCommunity.Api.Template.object import MediaItem


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


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
    console.print(f"\n[yellow]Download: [red]{site_constant.SITE_NAME} → [cyan]{select_title.name} \n")

    # Extract m3u8 URL from the film's URL
    response = create_client(headers=get_headers()).get(select_title.url + ".json")
    first_item_path =  "https://www.raiplay.it" + response.json().get("first_item_path")
    master_playlist = VideoSource.extract_m3u8_url(first_item_path)

    # Define the filename and path for the downloaded film
    mp4_name = os_manager.get_sanitize_file(select_title.name, select_title.date) + extension_output
    mp4_path = os.path.join(site_constant.MOVIE_FOLDER, mp4_name.replace(extension_output, ""))

    # HLS
    if ".mpd" not in master_playlist:
        r_proc = HLS_Downloader(
            m3u8_url=master_playlist,
            output_path=os.path.join(mp4_path, mp4_name)
        ).start()

    # MPD
    else:
        license_url = generate_license_url(select_title.mpd_id)

        dash_process = DASH_Downloader(
            license_url=license_url,
            mpd_url=master_playlist,
            output_path=os.path.join(mp4_path, mp4_name),
        )
        dash_process.parse_manifest(custom_headers=get_headers())
        
        if dash_process.download_and_decrypt():
            dash_process.finalize_output()

        # Get final output path and status
        r_proc = dash_process.get_status()

    if r_proc['error'] is not None:
        try: 
            os.remove(r_proc['path'])
        except Exception: 
            pass

    return r_proc['path'], r_proc['stopped']