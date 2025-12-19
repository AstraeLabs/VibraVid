# 3.12.23

import os


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util import os_manager, config_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
from StreamingCommunity.Lib.HLS import HLS_Downloader


# Logic
from StreamingCommunity.Api.Player.vixcloud import VideoSource


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
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Init class
    video_source = VideoSource(f"{site_constants.FULL_URL}/it", False, select_title.id)

    # Retrieve scws and if available master playlist
    video_source.get_iframe(select_title.id)
    video_source.get_content()
    master_playlist = video_source.get_playlist()

    if master_playlist is None:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, error: No master playlist found")
        return None

    # Define the filename and path for the downloaded film
    mp4_name = f"{os_manager.get_sanitize_file(select_title.name, select_title.date)}.{extension_output}"
    mp4_path = os.path.join(site_constants.MOVIE_FOLDER, mp4_name.replace(f".{extension_output}", ""))

    # Download the film using the m3u8 playlist, and output filename
    hls_process = HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(mp4_path, mp4_name)
    ).start()

    if hls_process['error'] is not None:
        try: 
            os.remove(hls_process['path'])
        except Exception: 
            pass

    return hls_process['path']