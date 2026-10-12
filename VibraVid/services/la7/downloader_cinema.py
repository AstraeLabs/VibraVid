# 11.10.26
# LA7 Cinema downloader - PlayReady DRM

import base64
import os
import xml.etree.ElementTree as ET

from curl_cffi.requests.exceptions import HTTPError
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


def _extract_playready_license_url(mpd_url: str, referer: str) -> str | None:
    """Extract PlayReady license URL from DASH manifest mspr:pro element."""
    try:
        with create_client(headers={"user-agent": get_userAgent(), "referer": referer}) as client:
            response = client.get(mpd_url)
            response.raise_for_status()
        
        root = ET.fromstring(response.text)
        ns = {"mspr": "urn:microsoft:playready"}
        
        # Find mspr:pro element
        pro_elem = root.find(".//mspr:pro", ns)
        if pro_elem is not None and pro_elem.text:
            pro_b64 = pro_elem.text.strip()
            # Add padding if needed
            pro_b64 = pro_b64 + "=" * (-len(pro_b64) % 4)
            decoded = base64.b64decode(pro_b64)
            
            # Find XML part (starts with '<')
            xml_start = decoded.find(b"<")
            if xml_start >= 0:
                xml_bytes = decoded[xml_start:]
                pro_xml = xml_bytes.decode("utf-16le")
                
                # Parse XML to find LA_URL (with namespace)
                pro_root = ET.fromstring(pro_xml)
                pr_ns = {"pr": "http://schemas.microsoft.com/DRM/2007/03/PlayReadyHeader"}
                la_url_elem = pro_root.find(".//pr:LA_URL", pr_ns)
                if la_url_elem is not None and la_url_elem.text:
                    return la_url_elem.text.strip()
                
                lui_url_elem = pro_root.find(".//pr:LUI_URL", pr_ns)
                if lui_url_elem is not None and lui_url_elem.text:
                    return lui_url_elem.text.strip()
    except Exception as exc:
        console.print(f"[yellow]Could not extract PlayReady license URL: {exc}")
    return None


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
    
    # Extract license URL from manifest
    license_url = _extract_playready_license_url(info["dash_url"], select_title.url)
    if not license_url:
        raise ValueError("Could not extract PlayReady license URL from manifest")
    
    console.print(f"[cyan]PlayReady license URL: {license_url}")
    
    try:
        return DASH_Downloader(
            mpd_url=info["dash_url"],
            mpd_headers={"referer": select_title.url, "user-agent": get_userAgent()},
            license_url=license_url,
            output_path=output_path,
            drm_preference=DRMType.PLAYREADY,
        ).start()
    except HTTPError as exc:
        console.print(f"[red]La7 Cinema download rejected by the source server: {exc}")
        return None