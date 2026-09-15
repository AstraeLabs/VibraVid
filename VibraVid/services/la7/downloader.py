import os

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console

from VibraVid.core.downloader import HLS_Downloader, MP4_Downloader
from VibraVid.services._base import Entries, movie_folder
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.utils import config_manager, os_manager, start_message

from .client import get_playback_info

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries):
    start_message()
    info = get_playback_info(select_title.url)
    if info["drm"]:
        raise ValueError("La7 content is DRM-protected and cannot be downloaded by this service")
    path_components, filename = map_movie_path(info["title"], getattr(select_title, "year", None))
    output_dir = os_manager.get_sanitize_path(movie_folder(*path_components))
    output_path = os.path.join(output_dir, f"{filename}.{extension_output}")
    try:
        if info["mp4_url"]:
            return MP4_Downloader(
                url=info["mp4_url"],
                path=output_path,
                referer=select_title.url,
            )
        if info["hls_url"]:
            return HLS_Downloader(m3u8_url=info["hls_url"], output_path=output_path).start()
    except HTTPError as exc:
        console.print(f"[red]La7 download rejected by the source server: {exc}")
        return None
    raise ValueError("La7 page does not expose a downloadable non-DRM source")
