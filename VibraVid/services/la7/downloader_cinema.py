# 11.10.26
# LA7 Cinema downloader - Widevine DRM (Kodi plugin approach)

import os
import time

from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.services._base import Entries, movie_folder
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.utils import config_manager, os_manager, start_message
from VibraVid.utils.http_client import create_client, get_userAgent

from .client import get_playback_info

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
        console.print("[cyan]Got Widevine preAuthToken")
    else:
        console.print("[yellow]Could not get Widevine preAuthToken")
    
    license_url = f"{WIDEVINE_LICENSE_URL}?d={tsatmp}"
    return license_url, headers_lic


def download_film_cinema(select_title: Entries):
    start_message()
    info = get_playback_info(select_title.url)
    
    if not info["dash_url"]:
        raise ValueError("La7 Cinema page does not expose a DASH source")
    
    if not info["drm"]:
        raise ValueError("La7 Cinema content is expected to be DRM protected")
    
    path_components, filename = map_movie_path(info["title"], getattr(select_title, "year", None))
    output_dir = os_manager.get_sanitize_path(movie_folder(*path_components))
    output_path = os.path.join(output_dir, f"{filename}.{extension_output}")
    
    # Build Widevine license URL and headers (Kodi plugin approach)
    license_url, license_headers = _build_widevine_license_info(select_title.url)
    console.print(f"[cyan]Widevine license URL: {license_url}")
    
    console.print("[cyan]Attempting download with Widevine...")
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