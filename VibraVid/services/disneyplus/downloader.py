# 21.09.26

import logging
import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message

from .client import get_client
from .scrapper import GetContentInfo, GetPageInfo, GetSerieInfo

msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension", default="mkv")


def _get_playback_info(client, media_id: str) -> dict:
    """Get playback info from Disney+ client."""
    try:
        return client.get_playback_info(media_id)
    except Exception as e:
        logger.error(f"Error getting playback info: {e}")
        return {"manifest": None, "license": None, "license_headers": {}}


def _get_drm_preference(playback_info: dict) -> DRMType:
    drm_type = playback_info.get("drm_type", "")
    if drm_type == "playready":
        return DRMType.PLAYREADY
    elif drm_type == "widevine":
        return DRMType.WIDEVINE
    return DRMType.PLAYREADY


def _make_license_request_fn(playback_info: dict, client):
    """Create a license request function for PlayReady SOAP and Widevine."""
    drm_type = playback_info.get("drm_type", "")
    license_url = playback_info.get("license", "")
    license_headers = playback_info.get("license_headers", {})

    if not license_url:
        return None

    def license_request_fn(challenge: bytes, request_headers: dict) -> bytes:
        headers = {
            "Content-Type": "application/octet-stream",
            **license_headers,
        }
        if drm_type == "playready":
            headers.update({
                "Accept": "application/xml, application/vnd.media-service+json; version=2",
                "SOAPAction": "http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense",
            })
            soap_body = f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><AcquireLicense xmlns="http://schemas.microsoft.com/DRM/2007/03/protocols"><Challenge>{challenge.hex()}</Challenge></AcquireLicense></s:Body></s:Envelope>'
            payload = soap_body.encode("utf-8")
        else:
            payload = challenge

        resp = client.session.post(license_url, headers=headers, data=payload)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            logger.error(f"License request failed: {resp.status_code} {license_url}")
            logger.error(f"Request headers: {headers}")
            logger.error(f"Response: {body}")
            raise RuntimeError(f"License request failed ({resp.status_code}): {body}")
        return resp.content

    return license_request_fn


def _download_playback(playback_info: dict, output_path: str):
    manifest = playback_info["manifest"]
    license_url = playback_info.get("license")
    license_headers = playback_info.get("license_headers", {})
    drm_preference = _get_drm_preference(playback_info)

    client = get_client()
    license_fn = _make_license_request_fn(playback_info, client) if license_url else None

    if ".m3u8" in manifest.lower():
        return HLS_Downloader(
            m3u8_url=manifest,
            output_path=output_path,
            license_url=license_url or None,
            license_headers=license_headers,
            has_drm=bool(license_url),
        ).start()

    try:
        return DASH_Downloader(
            mpd_url=manifest,
            license_url=license_url,
            license_headers=license_headers,
            output_path=output_path,
            drm_preference=drm_preference,
            license_request_fn=license_fn,
        ).start()
    except Exception as e:
        console.print(f"[red]DRM {drm_preference} failed: {e}")
        fallback = DRMType.WIDEVINE if drm_preference == DRMType.PLAYREADY else DRMType.PLAYREADY
        console.print(f"[yellow]Retrying with {fallback} DRM...")
        fallback_info = {**playback_info, "drm_type": "widevine" if fallback == DRMType.WIDEVINE else "playready"}
        fallback_fn = _make_license_request_fn(fallback_info, client) if license_url else None
        return DASH_Downloader(
            mpd_url=manifest,
            license_url=license_url,
            license_headers=license_headers,
            output_path=output_path,
            drm_preference=fallback,
            license_request_fn=fallback_fn,
        ).start()


def download_film(select_title: Entries):
    """Download a film from Disney+."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name}\n")

    client = get_client()
    content_info = GetContentInfo(client, select_title.id)

    if content_info.get_type() != "movie":
        console.print("[red]Error: Selected title is not a movie.")
        return False

    page_info = GetPageInfo(client, select_title.id)
    visuals = page_info.get_visuals()
    name = visuals.get("title", select_title.name)
    year = visuals.get("metastringParts", {}).get("releaseYearRange", {}).get("startYear")

    actions = page_info.data.get("actions", []) if page_info.data else []
    playback = next((a for a in actions if a.get("type") == "playback"), None)
    media_id = playback.get("resourceId") if playback else None

    if not media_id:
        console.print("[red]Error: Could not get media ID for playback")
        return False

    playback_info = _get_playback_info(client, media_id)
    if not playback_info.get("manifest"):
        console.print("[red]Error: Could not get manifest URL")
        return False

    path_components, filename = map_movie_path(name, year)
    movie_path = os_manager.get_sanitize_path(movie_folder(*path_components))
    movie_name = f"{filename}.{extension_output}"

    return _download_playback(playback_info, os.path.join(movie_path, movie_name))


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """Download a series from Disney+."""
    start_message()
    if not scrape_serie:
        scrape_serie = GetSerieInfo(get_client(), select_season.id)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

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


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """Download a specific episode from Disney+."""
    start_message()
    client = get_client()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        scrape_serie.year,
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = os_manager.get_sanitize_path(series_folder(*path_components))
    episode_name = f"{filename}.{extension_output}"

    media_id = getattr(obj_episode, "video_id", None) or obj_episode.id

    if not media_id:
        console.print("[red]Error: Could not get media ID for episode")
        return False

    playback_info = _get_playback_info(client, media_id)
    if not playback_info.get("manifest"):
        console.print("[red]Error: Could not get manifest URL for episode")
        return False

    return _download_playback(playback_info, os.path.join(episode_path, episode_name))
