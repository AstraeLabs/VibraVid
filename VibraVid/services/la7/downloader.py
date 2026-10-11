# 22.08.26
# By @sync-luca98

import logging
import os
import base64
import xml.etree.ElementTree as ET

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


def _extract_playready_license_url(mpd_url: str, referer: str) -> str | None:
    """Extract PlayReady license URL from DASH manifest mspr:pro element."""
    try:
        with create_client(headers={"user-agent": get_userAgent(), "referer": referer}) as client:
            response = client.get(mpd_url)
            response.raise_for_status()
        
        root = ET.fromstring(response.text)
        ns = {"mspr": "urn:microsoft:playready"}
        
        pro_elem = root.find(".//mspr:pro", ns)
        if pro_elem is not None and pro_elem.text:
            pro_b64 = pro_elem.text.strip()
            pro_b64 = pro_b64 + "=" * (-len(pro_b64) % 4)
            decoded = base64.b64decode(pro_b64)
            
            xml_start = decoded.find(b"<")
            if xml_start >= 0:
                xml_bytes = decoded[xml_start:]
                pro_xml = xml_bytes.decode("utf-16le")
                
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
    
    # Check if it's a cinema film (PlayReady DRM)
    is_cinema = "/la7-cinema-tutti-i-film/" in select_title.url
    
    if is_cinema:
        if not info["dash_url"]:
            raise ValueError("La7 Cinema page does not expose a DASH source")
        if not info["drm"]:
            raise ValueError("La7 Cinema content is expected to be DRM protected")
        
        # Extract PlayReady license URL from manifest
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