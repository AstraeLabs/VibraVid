# 3.12.23

import os


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import os_manager
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.message import start_message
from StreamingCommunity.Lib.HLS import HLS_Downloader


# Logic class
from StreamingCommunity.Api.Player.vixcloud import VideoSource
from StreamingCommunity.Api.Template.config_loader import site_constant
from StreamingCommunity.Api.Template.object import MediaItem


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


def download_film(select_title: MediaItem) -> str:
    """
    Downloads a film using the provided film ID, title name, and domain.

    Parameters:
        - domain (str): The domain of the site
        - version (str): Version of site.

    Return:
        - str: output path
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constant.SITE_NAME} → [cyan]{select_title.name} \n")

    # Init class
    video_source = VideoSource(f"{site_constant.FULL_URL}/it", False, select_title.id)

    # Retrieve scws and if available master playlist
    video_source.get_iframe(select_title.id)
    video_source.get_content()
    master_playlist = video_source.get_playlist()

    if master_playlist is None:
        console.print(f"[red]Site: {site_constant.SITE_NAME}, error: No master playlist found")
        return None

    # Define the filename and path for the downloaded film
    title_name = f"{os_manager.get_sanitize_file(select_title.name, select_title.date)}.{extension_output}"
    mp4_path = os.path.join(site_constant.MOVIE_FOLDER, title_name.replace(extension_output, ""))

    # Download the film using the m3u8 playlist, and output filename
    hls_process = HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(mp4_path, title_name)
    ).start()

    if hls_process['error'] is not None:
        try: 
            os.remove(hls_process['path'])
        except Exception: 
            pass

    return hls_process['path']