# 21.05.24

import os
import urllib.parse
from urllib.parse import urlparse, urlunparse

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.downloader.base import DownloadResult
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message
from VibraVid.utils.http_client import create_client

from .client import generate_license_url, get_playback_url, get_playback_url_direct, get_tracking_info
from .regions import get_region
from .scrapper import GetSerieInfo

console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def _subtitles_to_other_tracks(subtitles: list) -> list:
    tracks = []
    for sub in subtitles or []:
        if not isinstance(sub, dict):
            continue

        sub_url = sub.get("url")
        if not sub_url:
            continue

        track = {
            "type": "subtitle",
            "url": sub_url,
            "language": sub.get("language") or "und",
            "name": sub.get("language") or "Subtitle",
        }

        fmt = str(sub.get("format") or "").strip().lower().lstrip(".")
        if fmt:
            track["extension"] = fmt
            track["format"] = fmt
        tracks.append(track)
    return tracks


def try_mpd(url, qualities):
    """Try to find an existing manifest by swapping the quality token in the filename."""
    parsed = urlparse(url)
    path_parts = parsed.path.rsplit("/", 1)
    if len(path_parts) != 2:
        return None
    dir_path, filename = path_parts

    def replace_quality(filename, old_q, new_q):
        if f"{old_q}_" in filename:
            return filename.replace(f"{old_q}_", f"{new_q}_", 1)
        elif filename.startswith(f"{old_q}_"):
            return f"{new_q}_" + filename[len(f"{old_q}_") :]
        return filename

    for q in qualities:
        for old_q in qualities:
            if f"{old_q}_" in filename or filename.startswith(f"{old_q}_"):
                new_filename = replace_quality(filename, old_q, q)
                break
        else:
            new_filename = filename

        new_path = f"{dir_path}/{new_filename}"
        mpd_url = urlunparse(parsed._replace(path=new_path)).strip()
        try:
            with create_client() as client:
                r = client.head(mpd_url)
            if r.status_code == 200:
                return mpd_url
        except Exception:
            pass
    return None


def resolve_manifest(base):
    """Resolve the .mpd manifest URL."""
    if get_region() == "es":
        return base
    mpd_url = try_mpd(base, ["hd", "hr", "sd"])
    if not mpd_url:
        console.print("[red]Could not resolve a valid manifest URL")
        return base
    return mpd_url


def _resolve_and_download(content_id: str, output_path: str) -> DownloadResult:
    """Shared pipeline: content id -> playback -> SMIL -> MPD -> DASH download"""
    try:
        playback_json = get_playback_url(content_id)
    except RuntimeError as e:
        if not any(code in str(e) for code in ("PL043", "AG006")):
            raise
        console.print("[yellow]Playback gateway blocked this content, falling back to direct stream...")
        playback_json = get_playback_url_direct(content_id)

    tracking = get_tracking_info(playback_json)
    if not tracking or not tracking.get("videos"):
        if tracking is not None:
            console.print("[red]No playable video stream returned (geo/DRM/anonymous-proxy block?)")
        return DownloadResult(output_path, False)

    video = tracking["videos"][0]
    mpd_url = resolve_manifest(video["url"])
    license_url, license_params, license_headers = generate_license_url(video)
    if license_params:
        license_url = f"{license_url}?{urllib.parse.urlencode(license_params)}"

    return DASH_Downloader(
        mpd_url=mpd_url,
        license_url=license_url,
        license_headers=license_headers,
        other_tracks=_subtitles_to_other_tracks(tracking.get("subtitles")) or None,
        output_path=output_path,
    ).start()


def download_film(select_title: Entries) -> DownloadResult:
    """Download a single film / episode (used for ES /player/ URLs too)."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name} \n")

    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    return _resolve_and_download(select_title.id, os.path.join(movie_path, movie_name))


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """Download a specific episode from a season."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = os_manager.get_sanitize_path(series_folder(*path_components))
    episode_name = f"{filename}.{extension_output}"

    return _resolve_and_download(obj_episode.id, os.path.join(episode_path, episode_name))


def download_series(
    dict_serie: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(dict_serie.url)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

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
