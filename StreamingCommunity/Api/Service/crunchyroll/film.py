# 16.03.25

import os
from urllib.parse import urlparse, parse_qs


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util import config_manager, os_manager, start_message
from StreamingCommunity.Api.Template import site_constants, MediaItem
from StreamingCommunity.Lib.DASH.downloader import DASH_Downloader


# Logi
from .util.get_license import get_playback_session, CrunchyrollClient


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

    # Initialize Crunchyroll client
    client = CrunchyrollClient()
    if not client.start():
        console.print("[red]Failed to authenticate with Crunchyroll.")
        return None, True

    # Define filename and path for the downloaded video
    mp4_name = f"{os_manager.get_sanitize_file(select_title.name, select_title.date)}.{extension_output}"
    mp4_path = os.path.join(site_constants.MOVIE_FOLDER, mp4_name.replace(f".{extension_output}", ""))

    # Generate mpd and license URLs
    url_id = select_title.get('url').split('/')[-1]
    
    # Get playback session
    try:
        playback_result = get_playback_session(client, url_id)
        
        # Check if access was denied (403)
        if playback_result is None:
            console.print("[red]✗ Access denied: This content requires a premium subscription")
            return None, False
        
        mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = playback_result
        
    except Exception as e:
        console.print(f"[red]✗ Error getting playback session: {str(e)}")
        return None, False
    
    # Parse playback token from mpd_url
    parsed_url = urlparse(mpd_url)
    query_params = parse_qs(parsed_url.query)

    # Download the film
    dash_process = DASH_Downloader(
        license_url='https://www.crunchyroll.com/license/v1/license/widevine',
        mpd_url=mpd_url,
        mpd_sub_list=mpd_list_sub,
        output_path=os.path.join(mp4_path, mp4_name),
    )
    dash_process.parse_manifest(custom_headers=mpd_headers)

    # Create headers for license request
    license_headers = mpd_headers.copy()
    license_headers.update({
        "x-cr-content-id": url_id,
        "x-cr-video-token": query_params['playbackGuid'][0],
    })

    if dash_process.download_and_decrypt(custom_headers=license_headers):
        dash_process.finalize_output()

    # Get final output path and status
    status = dash_process.get_status()

    if status['error'] is not None and status['path']:
        try: 
            os.remove(status['path'])
        except Exception: 
            pass

    # Delete stream after download to avoid TOO_MANY_ACTIVE_STREAMS
    playback_token = token or query_params.get('playbackGuid', [None])[0]
    if playback_token:
        client.delete_active_stream(url_id, playback_token)
        console.print("[dim]✓ Playback session closed[/dim]")

    return status['path'], status['stopped']