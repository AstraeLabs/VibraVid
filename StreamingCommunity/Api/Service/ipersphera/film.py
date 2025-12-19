# 16.12.25

import os

# External library
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_headers
from StreamingCommunity.Util import config_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
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
    
    # Extract proton url
    proton_url = None
    try:
        response = create_client(headers=get_headers()).get(select_title.url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'uprot' in href:
                proton_url = href
                break
    
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request error: {e}, get proton URL")
        return None
    
    # Extract mega link
    mega_link = None
    try:
        response = create_client(headers=get_headers()).get(proton_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'mega' in href:
                mega_link = href
                break
    
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request error: {e}, get mega link")
        return None
    
    # Define the filename and path for the downloaded film
    if select_title.type == "film":
        mp4_path = os.path.join(site_constants.MOVIE_FOLDER, str(select_title.name).replace(extension_output, ""))
    else:
        mp4_path = os.path.join(site_constants.SERIES_FOLDER, str(select_title.name).replace(extension_output, ""))

    # Download from MEGA
    mega = MEGA_Downloader(
        choose_files=True
    )
    output_path = mega.download_url(
        url=mega_link,
        dest_path=mp4_path
    )
    return output_path