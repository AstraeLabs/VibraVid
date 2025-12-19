# 16.03.25

import os


# External library
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client_curl, get_headers
from StreamingCommunity.Util import config_manager, os_manager, start_message
from StreamingCommunity.Api.Template import site_constants
from StreamingCommunity.Api.Template.object import MediaItem
from StreamingCommunity.Lib.MEGA import MEGA_Downloader


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


def download_film(select_title: MediaItem) -> str:
    """
    Downloads a film using the provided film ID, title name, and domain.

    Parameters:
        - select_title (MediaItem): The selected media item.

    Return:
        - str: output path if successful, otherwise None
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")
    
    mega_link = None
    try:
        response = create_client_curl(headers=get_headers()).get(select_title.url)
        response.raise_for_status()

        # Parse HTML to find mega link
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all("a", href=True):

            if "?!" in a["href"].lower().strip():
                mega_link = "https://mega.nz/#!" + a["href"].split("/")[-1].replace('?!', '')
                break

            if "/?file/" in a["href"].lower().strip():
                mega_link = "https://mega.nz/#!" + a["href"].split("/")[-1].replace('/?file/', '')
                break

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request error: {e}, get mostraguarda")
        return None

    # Define the filename and path for the downloaded film
    mp4_name = f"{os_manager.get_sanitize_file(select_title.name, select_title.date)}.{extension_output}"
    mp4_path = os.path.join(site_constants.MOVIE_FOLDER, mp4_name.replace(f".{extension_output}", ""))

    # Download the film using the mega downloader
    mega = MEGA_Downloader(choose_files=True)

    if mega_link is None:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, error: Mega link not found for url: {select_title.url}")
        return None

    output_path = mega.download_url(
        url=mega_link,
        dest_path=os.path.join(mp4_path, mp4_name)
    )
    return output_path