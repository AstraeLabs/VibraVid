# 22.12.25

import logging
import os
from contextlib import nullcontext
from datetime import datetime

from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.live_event import LiveEndWatcher, LiveEvent
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message

from .client import get_client
from .scrapper import GetLiveInfo, GetSerieInfo, GetStandaloneInfo

console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def _drm_preference(playback_info: dict) -> str:
    """Get the preferred DRM type based on the playback info."""
    return DRMType.WIDEVINE if playback_info.get("drm_type") == "widevine" else DRMType.PLAYREADY


def _event_window(scrape_content) -> tuple[datetime | None, datetime | None]:
    """The scheduled (start, end) of a live event from its EPG window; None for a bound that is missing or unreadable"""
    attrs = (getattr(scrape_content, "content_info", None) or {}).get("attributes", {})

    def parse(raw):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
        except (AttributeError, ValueError) as exc:
            logger.debug(f"Could not read live event time {raw!r}: {exc}")
            return None

    return parse(attrs.get("scheduleStart")), parse(attrs.get("scheduleEnd"))


def download_live(select_title: Entries):
    """
    Downloads a live event using the provided Entries information.
    """
    start_message()
    print_download_header(select_title, suffix="[magenta](LIVE)")

    scrape_content = GetLiveInfo(select_title.id)
    edit_id = getattr(select_title, "edit_id", None) or scrape_content.get_edit_id()

    if not edit_id:
        console.print(f"[red]Error: Could not get edit ID for live event {select_title.name}")
        return False

    # Reuse the movie path logic — live events are single-file downloads
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    live_path = os_manager.get_sanitize_path(movie_folder(*path_components))
    live_name = f"{filename}.{extension_output}"

    client = get_client()
    playback_info = client.get_playback_info(edit_id)
    starts_at, ends_at = _event_window(scrape_content)
    elapsed = None if context_tracker.max_time else LiveEvent(starts_at).elapsed_seconds()
    
    if elapsed:
        h, m = divmod(int(elapsed) // 60, 60)
        console.print(f"[cyan]The event started [green]{h:02d}:{m:02d}[/green] ago: recording from its start until the live ends")
    
    if ends_at:
        console.print(f"[cyan]Scheduled end: [green]{ends_at.astimezone():%H:%M}[/green] — the recording follows the stream, not the schedule")

    watcher = LiveEndWatcher(scrape_content.is_live_now)
    watch_end = watcher if scrape_content.is_live() and not context_tracker.max_time else nullcontext()
    with watch_end:
        return DASH_Downloader(
            mpd_url=playback_info["manifest"],
            license_url=playback_info["license"],
            license_headers=playback_info.get("license_headers", {}),
            output_path=os.path.join(live_path, live_name),
            drm_preference=_drm_preference(playback_info),
            stop_when=watcher.is_ended,
            live_tail_seconds=elapsed,
        ).start()


def download_film(select_title: Entries):
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    print_download_header(select_title)

    edit_id = getattr(select_title, "edit_id", None)
    if not edit_id:
        scrape_content = GetStandaloneInfo(select_title.id)
        edit_id = scrape_content.get_edit_id()

    if not edit_id:
        console.print(f"[red]Error: Could not get edit ID for {select_title.name}")
        return False

    # Define output path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = os_manager.get_sanitize_path(movie_folder(*path_components))
    movie_name = f"{filename}.{extension_output}"

    # Get playback info
    client = get_client()
    playback_info = client.get_playback_info(edit_id)

    return DASH_Downloader(
        mpd_url=playback_info["manifest"],
        license_url=playback_info["license"],
        license_headers=playback_info.get("license_headers", {}),
        output_path=os.path.join(movie_path, movie_name),
        drm_preference=_drm_preference(playback_info),
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode using the authenticated playback info.
    """
    start_message()
    client = get_client()
    print_episode_header(scrape_serie.series_name, obj_episode.name, index_season_selected, index_episode_selected)

    # Define output path
    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = os_manager.get_sanitize_path(series_folder(*path_components))
    episode_name = f"{filename}.{extension_output}"

    # Get playback info
    playback_info = client.get_playback_info(obj_episode.id)

    return DASH_Downloader(
        mpd_url=playback_info["manifest"],
        license_url=playback_info["license"],
        license_headers=playback_info.get("license_headers", {}),
        output_path=os.path.join(episode_path, episode_name),
        drm_preference=_drm_preference(playback_info),
    ).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series
    """
    start_message()
    if not scrape_serie:
        scrape_serie = GetSerieInfo(select_season.id)
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
