# 02.11.26

import os
import time

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader, MP4_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message
from VibraVid.utils.http_client import create_client, get_userAgent

from ._helpers import BASE_URL
from .client import get_playback_info
from .scrapper import GetSerieInfo

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")

WIDEVINE_LICENSE_URL = "https://la7.prod.conax.cloud/widevine/license"


def _download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """Download a single episode."""
    start_message()
    print_episode_header(scrape_serie.series_name, obj_episode.name, index_season_selected, index_episode_selected)

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    season = scrape_serie.seasons_manager.get_season_by_number(index_season_selected)
    category_name = (
        os_manager.get_sanitize_file(season.name)
        if season and season.name
        else f"S{index_season_selected:02d}"
    )
    season_folder = f"S{index_season_selected:02d}"
    path_components = list(path_components)
    for index, component in enumerate(path_components):
        if component.casefold() == season_folder.casefold():
            path_components[index] = category_name
            break
    else:
        path_components.insert(min(1, len(path_components)), category_name)

    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"
    output_path = os.path.join(episode_path, episode_name)

    info = get_playback_info(obj_episode.url)

    if info["drm"]:
        if not info["dash_url"]:
            raise ValueError("La7 DRM content does not expose a DASH source")

        drm_content_id = info.get("drm_content_id", "")
        vod_token = None
        if drm_content_id:
            token_url = f"https://svc-01.la7play.it/DRM/tk.php?id={drm_content_id}&contentType=VOD&v={int(time.time() * 1000)}"
            try:
                with create_client(
                    headers={
                        "user-agent": get_userAgent(),
                        "referer": obj_episode.url,
                        "accept": "application/json",
                    }
                ) as client:
                    response = client.get(token_url, timeout=30)
                    response.raise_for_status()
                    tok_data = response.json()
                    vod_token = tok_data.get("preAuthToken")
            except Exception as exc:
                console.print(f"[yellow]Could not get VOD preAuthToken: {exc}")

        license_url = f"{WIDEVINE_LICENSE_URL}?d={int(time.time())}"
        headers_lic = {
            "host": "la7.prod.conax.cloud",
            "user-agent": get_userAgent(),
            "accept": "*/*",
            "accept-language": "en,en-US;q=0.9,it;q=0.8",
            "origin": "https://www.la7.it",
            "referer": "https://www.la7.it/",
        }
        if vod_token:
            headers_lic["preAuthorization"] = vod_token

        result = DASH_Downloader(
            mpd_url=info["dash_url"],
            mpd_headers={"referer": obj_episode.url, "user-agent": get_userAgent()},
            license_url=license_url,
            license_headers=headers_lic,
            output_path=output_path,
            drm_preference=DRMType.WIDEVINE,
        ).start()

        if result and not result.error:
            return result
        return None

    return _download_clear_stream(obj_episode.url, info, output_path)


def _download_clear_stream(page_url: str, info: dict, output_path: str):
    headers = {"referer": page_url, "user-agent": get_userAgent()}
    try:
        if info["dash_url"]:
            return DASH_Downloader(
                mpd_url=info["dash_url"],
                mpd_headers=headers,
                output_path=output_path,
            ).start()
        if info["hls_url"]:
            return HLS_Downloader(
                m3u8_url=info["hls_url"],
                headers=headers,
                output_path=output_path,
            ).start()
        if info["mp4_url"]:
            return MP4_Downloader(
                url=info["mp4_url"],
                path=output_path,
                referer=page_url,
            )
    except HTTPError:
        return None
    return None


def download_film(select_title: Entries):
    start_message()
    print_download_header(select_title)
    info = get_playback_info(select_title.url)

    path_components, filename = map_movie_path(info["title"], getattr(select_title, "year", None))
    output_dir = os_manager.get_sanitize_path(movie_folder(*path_components))
    output_path = os.path.join(output_dir, f"{filename}.{extension_output}")

    if info["drm"]:
        if not info["dash_url"]:
            raise ValueError("La7 DRM content does not expose a DASH source")

        # Get VOD-specific preAuthorization token
        drm_content_id = info.get("drm_content_id", "")
        vod_token = None
        if drm_content_id:
            token_url = f"https://svc-01.la7play.it/DRM/tk.php?id={drm_content_id}&contentType=VOD&v={int(time.time() * 1000)}"
            try:
                with create_client(
                    headers={
                        "user-agent": get_userAgent(),
                        "referer": select_title.url,
                        "accept": "application/json",
                    }
                ) as client:
                    response = client.get(token_url, timeout=30)
                    response.raise_for_status()
                    tok_data = response.json()
                    vod_token = tok_data.get("preAuthToken")
            except Exception as exc:
                console.print(f"[yellow]Could not get VOD preAuthToken: {exc}")

        license_url = f"{WIDEVINE_LICENSE_URL}?d={int(time.time())}"
        headers_lic = {
            "host": "la7.prod.conax.cloud",
            "user-agent": get_userAgent(),
            "accept": "*/*",
            "accept-language": "en,en-US;q=0.9,it;q=0.8",
            "origin": "https://www.la7.it",
            "referer": "https://www.la7.it/",
        }
        if vod_token:
            headers_lic["preAuthorization"] = vod_token

        result = DASH_Downloader(
            mpd_url=info["dash_url"],
            mpd_headers={"referer": select_title.url, "user-agent": get_userAgent()},
            license_url=license_url,
            license_headers=headers_lic,
            output_path=output_path,
            drm_preference=DRMType.WIDEVINE,
        ).start()

        if result and not result.error:
            return result

        return None

    return _download_clear_stream(select_title.url, info, output_path)


def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None) -> None:
    """Handle downloading a complete series."""
    start_message()
    if scrape_serie is None:
        path_id = getattr(select_season, "path_id", None)
        if not path_id:
            url = select_season.url
            path_id = url.replace(BASE_URL, "").rstrip("/")
        # Normalize: remove trailing /video if present
        if path_id.endswith("/video"):
            path_id = path_id[:-len("/video")]
        scrape_serie = GetSerieInfo(path_id)
        scrape_serie.collect_info_title()

    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
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
