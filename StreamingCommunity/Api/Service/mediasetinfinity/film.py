# 21.05.24

import os
from typing import Tuple


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import os_manager
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.message import start_message
from StreamingCommunity.Util.headers import get_headers
from StreamingCommunity.Lib.DASH.downloader import DASH_Downloader


# Logic class
from .util.fix_mpd import get_manifest
from .util.get_license import get_playback_url, get_tracking_info, generate_license_url
from StreamingCommunity.Api.Template.config_loader import site_constant
from StreamingCommunity.Api.Template.object import MediaItem


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


def download_film(select_title: MediaItem) -> Tuple[str, bool]:
    """
    Downloads a film using the provided film ID, title name, and domain.

    Parameters:
        - select_title (MediaItem): The selected media item.

    Return:
        - str: output path if successful, otherwise None
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constant.SITE_NAME} → [cyan]{select_title.name} \n")

    # Define the filename and path for the downloaded film
    title_name = os_manager.get_sanitize_file(select_title.name, select_title.date) + extension_output
    mp4_path = os.path.join(site_constant.MOVIE_FOLDER, title_name.replace(extension_output, ""))

    # Get playback URL and tracking info
    playback_json = get_playback_url(select_title.id)
    tracking_info = get_tracking_info(playback_json)['videos'][0]

    license_url, license_params = generate_license_url(tracking_info)
    mpd_url = get_manifest(tracking_info['url'])

    # Download the episode
    dash_process =  DASH_Downloader(
        license_url=license_url,
        mpd_url=mpd_url,
        output_path=os.path.join(mp4_path, title_name),
    )
    dash_process.parse_manifest(custom_headers=get_headers())

    if dash_process.download_and_decrypt(query_params=license_params):
        dash_process.finalize_output()

    # Get final output path and status
    status = dash_process.get_status()

    if status['error'] is not None and status['path']:
        try: 
            os.remove(status['path'])
        except Exception: 
            pass

    return status['path'], status['stopped']