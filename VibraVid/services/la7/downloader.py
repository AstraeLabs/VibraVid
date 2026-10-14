# 22.08.26
# By @sync-luca98

import logging
import os
import time
import urllib.parse

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader, MP4_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.services._base import Entries, movie_folder, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message
from VibraVid.utils.http_client import create_client, get_userAgent

from .client import get_playback_info
from .scrapper import GetSerieInfo

logger = logging.getLogger(__name__)

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")

WIDEVINE_LICENSE_URL = "https://la7.prod.conax.cloud/widevine/license"


def _get_widevine_preauth_token(page_url: str) -> str | None:
    """Get Widevine preAuthToken from la7 token endpoint (like Kodi plugin)."""
    try:
        with create_client(headers={"user-agent": get_userAgent(), "referer": page_url}) as client:
            response = client.get(page_url)
            response.raise_for_status()
            html = response.text
        
            # Find preTokenUrl in page
            import re
            pre_token_url_match = re.search(r'preTokenUrl\s*=\s*["\']([^"\']+)["\']', html)
            if not pre_token_url_match:
                return None
            
            pre_token_url = pre_token_url_match.group(1)
            
            # Request token with headers (same client session)
            headers_tok = {
                'host': 'pat.la7.it',
                'user-agent': get_userAgent(),
                'accept': '*/*',
                'accept-language': 'en,en-US;q=0.9,it;q=0.8',
                'dnt': '1',
                'te': 'trailers',
                'origin': 'https://www.la7.it',
                'referer': 'https://www.la7.it/',
            }
            tok_response = client.get(pre_token_url, headers=headers_tok)
            tok_response.raise_for_status()
            tok_data = tok_response.json()
            return tok_data.get('preAuthToken')
    except Exception as exc:
        console.print(f"[yellow]Could not get Widevine preAuthToken: {exc}")
    return None


def _build_widevine_license_info(page_url: str) -> tuple[str, dict]:
    """Build Widevine license URL and headers (like Kodi plugin)."""
    tsatmp = str(int(time.time()))
    preauth_token = _get_widevine_preauth_token(page_url)
    
    headers_lic = {
        'host': 'la7.prod.conax.cloud',
        'user-agent': get_userAgent(),
        'accept': '*/*',
        'accept-language': 'en,en-US;q=0.9,it;q=0.8',
        'origin': 'https://www.la7.it',
        'referer': 'https://www.la7.it/',
    }
    if preauth_token:
        headers_lic['preAuthorization'] = preauth_token
        console.print(f"[cyan]Got Widevine preAuthToken")
    else:
        console.print("[yellow]Could not get Widevine preAuthToken")
    
    license_url = f"{WIDEVINE_LICENSE_URL}?d={tsatmp}"
    return license_url, headers_lic


def _download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """Download a single episode."""
    start_message()
    
    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"
    output_path = os.path.join(episode_path, episode_name)
    
    info = get_playback_info(obj_episode.url)
    
    if info["drm"]:
        raise ValueError("La7 content is DRM-protected and cannot be downloaded by this service")
    
    try:
        if info["dash_url"]:
            return DASH_Downloader(
                mpd_url=info["dash_url"],
                mpd_headers={"referer": obj_episode.url, "user-agent": get_userAgent()},
                output_path=output_path,
            ).start()
        if info["hls_url"]:
            return HLS_Downloader(
                m3u8_url=info["hls_url"],
                headers={"referer": obj_episode.url, "user-agent": get_userAgent()},
                output_path=output_path,
            ).start()
        if info["mp4_url"]:
            return MP4_Downloader(
                url=info["mp4_url"],
                path=output_path,
                referer=obj_episode.url,
            )
    except HTTPError as exc:
        console.print(f"[red]La7 download rejected by the source server: {exc}")
        return None
    raise ValueError("La7 page does not expose a downloadable non-DRM source")


def download_film(select_title: Entries):
    start_message()
    info = get_playback_info(select_title.url)
    
    path_components, filename = map_movie_path(info["title"], getattr(select_title, "year", None))
    output_dir = os_manager.get_sanitize_path(movie_folder(*path_components))
    output_path = os.path.join(output_dir, f"{filename}.{extension_output}")
    
    # Check if it's a cinema film (Widevine DRM)
    is_cinema = "/la7-cinema-tutti-i-film/" in select_title.url
    
    if is_cinema:
        if not info["dash_url"]:
            raise ValueError("La7 Cinema page does not expose a DASH source")
        if not info["drm"]:
            raise ValueError("La7 Cinema content is expected to be DRM protected")
        
        # Build Widevine license URL and headers (Kodi plugin approach)
        license_url, license_headers = _build_widevine_license_info(select_title.url)
        console.print(f"[cyan]Widevine license URL: {license_url}")
        
        console.print(f"[cyan]Attempting download with Widevine...")
        result = DASH_Downloader(
            mpd_url=info["dash_url"],
            mpd_headers={"referer": select_title.url, "user-agent": get_userAgent()},
            license_url=license_url,
            license_headers=license_headers,
            output_path=output_path,
            drm_preference=DRMType.WIDEVINE,
        ).start()
        
        if result and not result.error:
            return result
        
        console.print(f"[yellow]Widevine failed: {result.error if result else 'Unknown error'}")
        return None
    
    # Regular episodes - no DRM
    if info["drm"]:
        raise ValueError("La7 content is DRM-protected and cannot be downloaded by this service")
    
    try:
        if info["dash_url"]:
            return DASH_Downloader(
                mpd_url=info["dash_url"],
                mpd_headers={"referer": select_title.url, "user-agent": get_userAgent()},
                output_path=output_path,
            ).start()
        if info["hls_url"]:
            return HLS_Downloader(
                m3u8_url=info["hls_url"],
                headers={"referer": select_title.url, "user-agent": get_userAgent()},
                output_path=output_path,
            ).start()
        if info["mp4_url"]:
            return MP4_Downloader(
                url=info["mp4_url"],
                path=output_path,
                referer=select_title.url,
            )
    except HTTPError as exc:
        console.print(f"[red]La7 download rejected by the source server: {exc}")
        return None
    raise ValueError("La7 page does not expose a downloadable non-DRM source")


def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None) -> None:
    """Handle downloading a complete series."""
    start_message()
    if scrape_serie is None:
        # Get path_id from the selected entry
        path_id = getattr(select_season, "path_id", None)
        if not path_id:
            # Fallback: try to extract from URL
            url = select_season.url
            if "/rivedila7" in url:
                path_id = url.split("/rivedila7")[0].replace("https://www.la7.it", "")
            else:
                raise ValueError("Cannot determine program path_id for series download")
        scrape_serie = GetSerieInfo(path_id)
        scrape_serie.collect_info_title()
    
    seasons_count = len(scrape_serie.seasons_manager)
    
    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return _download_episode(obj_episode, season_idx, episode_idx, scrape_serie)
        
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