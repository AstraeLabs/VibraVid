# 19.06.24

import logging
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader.base import DownloadResult
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.services._base import tmdb_artwork
from VibraVid.services._base.tv_display_manager import (
    display_episodes_list,
    display_seasons_list,
    manage_selection,
    validate_selection,
)
from VibraVid.utils.storage_upload.hook import is_cached

console = Console()
logger = logging.getLogger(__name__)


def _is_user_stop_requested() -> bool:
    """
    Return True only when an explicit stop has been requested for the active tracked download.
    """
    download_id = context_tracker.download_id
    if not download_id:
        return False

    # In GUI context, "cancelled_scheduled_downloads" tracks cancelled queues
    if getattr(context_tracker, "is_gui", False):
        try:
            callback = getattr(context_tracker, "is_cancelled_callback", None)
            if callback and callback(download_id):
                return True
        except Exception as e:
            logger.error(f"Error checking schedule cancellation: {e}")
            pass

    return download_tracker.is_stopped(download_id)


def process_season_selection(
    scrape_serie: Any,
    seasons_count: int,
    season_selection: str | None,
    episode_selection: str | None,
    download_episode_callback: Callable,
) -> None:
    """
    Process season selection and trigger episode downloads.

    Parameters:
        - scrape_serie: Scraper object with series information
        seasons_count (int): Total number of seasons
        season_selection (str, optional): Pre-defined season selection
        episode_selection (str, optional): Pre-defined episode selection
        download_episode_callback (Callable): Function to call for downloading episodes
    """
    if seasons_count == 0:
        console.print("[red]No seasons found for this series")
        return

    # If season_selection is provided, use it instead of asking for input
    if season_selection is None:
        index_season_selected = display_seasons_list(scrape_serie.seasons_manager)
        is_manual_input = True
    else:
        index_season_selected = season_selection
        is_manual_input = False
        logger.info(f"Using provided season selection: {season_selection}")
    context_tracker.cli_season_selection = index_season_selected

    # Get available season numbers
    seasons_list = scrape_serie.seasons_manager.seasons
    available_numbers = [s.number for s in seasons_list]

    # Map the selection to actual season numbers
    list_season_select = []

    if season_selection is None:
        # Interactive mode - use manage_selection with indices (1-based)
        max_count = len(seasons_list)
        list_selection = manage_selection(index_season_selected, max_count)

        for val in list_selection:
            # manage_selection() returns indices (1-based)
            if 1 <= val <= len(seasons_list):
                list_season_select.append(seasons_list[val - 1].number)
            elif val in available_numbers:
                list_season_select.append(val)
            else:
                console.print(f"[yellow]Warning: Selection {val} is neither a valid index nor a valid season number.")
    else:
        # Pre-selected mode (from GUI/CLI) - accept a 1-based index (position in the displayed list) first, then fall back to matching an actual season number.
        def _resolve_season_num(num: int) -> None:
            if 1 <= num <= len(seasons_list):
                list_season_select.append(seasons_list[num - 1].number)
            elif num in available_numbers:
                list_season_select.append(num)
            else:
                console.print(f"[yellow]Warning: Season {num} is not available. Available: {available_numbers}")

        for val in index_season_selected.split(","):
            val = val.strip()
            if val == "*":
                list_season_select = list(available_numbers)
                break

            if "-" in val:
                start_s, end_s = (p.strip() for p in val.split("-", 1))
                try:
                    start = int(start_s)
                except ValueError:
                    console.print(f"[yellow]Warning: '{val}' is not a valid season range.")
                    continue
                end = int(end_s) if end_s.isdigit() else len(seasons_list)
                for num in range(start, end + 1):
                    _resolve_season_num(num)
                continue

            try:
                num = int(val)
            except ValueError:
                console.print(f"[yellow]Warning: '{val}' is not a valid season number.")
                continue

            _resolve_season_num(num)

    if not list_season_select:
        if is_manual_input:
            list_season_select = validate_selection(list_selection, available_numbers)
        else:
            return

    # Loop through the selected seasons and download episodes
    for season_number in list_season_select:
        if _is_user_stop_requested():
            console.print("[yellow]Download stopped by user.")
            break

        season = scrape_serie.seasons_manager.get_season_by_number(season_number)

        if not season:
            console.print(f"[red]Season {season_number} not found! Available seasons: {available_numbers}")
            continue

        # Determine if we should download all episodes
        download_all = len(list_season_select) > 1 or index_season_selected == "*"

        # Call the download callback with appropriate parameters
        download_episode_callback(
            season_number=season_number,
            download_all=download_all,
            episode_selection=episode_selection if not download_all else None,
        )


def process_episode_download(
    index_season_selected: int,
    scrape_serie: Any,
    download_video_callback: Callable,
    download_all: bool = False,
    episode_selection: str | None = None,
) -> None:
    """
    Handle downloading episodes for a specific season.

    Parameters:
        index_season_selected (int): Season number
        - scrape_serie: Scraper object with series information
        download_video_callback (Callable): Function to call for downloading individual videos
        download_all (bool): Whether to download all episodes
        episode_selection (str, optional): Pre-defined episode selection
    """
    episodes = scrape_serie.getEpisodeSeasons(index_season_selected)
    episodes_count = len(episodes)

    if episodes_count == 0:
        console.print(f"[red]No episodes found for season {index_season_selected}")
        return

    # Track per-episode outcomes so a season where nothing downloaded is
    # reported as a failure (non-zero CLI exit / "Failed" in the GUI) instead
    # of silently completing.
    failed_episodes: list[int] = []
    downloaded_any = False
    last_error: str | None = None

    if download_all:
        context_tracker.cli_episode_selection = "*"
        for i_episode in range(1, episodes_count + 1):
            if _is_user_stop_requested():
                console.print(f"[yellow]Download stopped by user before episode {i_episode}.")
                break

            # Update context tracker for the current episode
            context_tracker.season = index_season_selected
            context_tracker.episode = i_episode
            ep_obj = episodes[i_episode - 1]
            context_tracker.episode_name = (ep_obj.get("name") if isinstance(ep_obj, dict) else getattr(ep_obj, "name", None))
            context_tracker.poster_url = tmdb_artwork.resolve_episode_artwork_url(
                context_tracker.series_tmdb_id, index_season_selected, i_episode
            )

            if is_cached():
                console.print(f"[dim]Skipping episode {i_episode} of season {index_season_selected} — already in cache.")
                continue

            # Trigger the download callback for the current episode
            try:
                result = download_video_callback(episodes[i_episode - 1], index_season_selected, i_episode)
            except Exception as e:
                logger.exception("Error downloading episode %s of season %s", i_episode, index_season_selected)
                console.print(f"[red]Error downloading episode {i_episode} of season {index_season_selected}: {e}")
                result = DownloadResult(None, True, str(e) or e.__class__.__name__)

            res = DownloadResult.from_raw(result)
            stopped, error_msg = res.stopped, res.error

            # If callback signalled stop/failure, surface returned error (if any)
            if _is_user_stop_requested() or stopped:
                if _is_user_stop_requested():
                    break
                if error_msg:
                    console.print(f"[red]Error: {error_msg}")
                console.print(f"[yellow]Warning: episode {i_episode} failed for season {index_season_selected}.")
                failed_episodes.append(i_episode)
                last_error = error_msg or last_error
            else:
                downloaded_any = True

    else:
        # Display episodes list and manage user selection
        if episode_selection is None:
            last_command = display_episodes_list(episodes)
        else:
            last_command = episode_selection
            # console.print(f"\n[cyan]Using provided episode selection: [yellow]{episode_selection}")
            logger.info(f"Using provided episode selection: {episode_selection}")

        # Get available episode numbers
        available_episode_numbers = [
            ep.get("number") if isinstance(ep, dict) else getattr(ep, "number", idx)
            for idx, ep in enumerate(episodes, 1)
        ]

        # Determine the maximum "index" or "number" to allow in manage_selection
        m_count = max(len(episodes), max([n for n in available_episode_numbers if isinstance(n, (int, float))] or [0]))

        while True:
            list_selection = manage_selection(last_command, m_count)

            # Map selection to indices
            list_episode_select = []
            for val in list_selection:
                # 1. Check if it's a valid index (1-based)
                if 1 <= val <= len(episodes):
                    list_episode_select.append(val)

                # 2. Check if it's a valid episode number
                else:
                    print("Failed index check, trying episode number check...")
                    found = False
                    for idx, ep in enumerate(episodes, 1):
                        ep_num = ep.get("number") if isinstance(ep, dict) else getattr(ep, "number", None)

                        # Compare as strings to handle numeric-like strings from API GUI
                        if str(ep_num) == str(val):
                            list_episode_select.append(idx)
                            found = True
                            break

                    if not found:
                        console.print(f"[yellow]Warning: Episode selection {val} is neither a valid index nor a valid episode number.")

            if list_episode_select:
                break

            console.print(f"[red]No valid episodes selected. Available indices: 1-{len(episodes)}, Available numbers: {available_episode_numbers}")
            if episode_selection is not None:
                return

            last_command = Prompt.ask("[red]Enter valid episode numbers or indices")
        context_tracker.cli_episode_selection = last_command

        # Download selected episodes if not stopped
        for i_episode in list_episode_select:
            if _is_user_stop_requested():
                console.print(f"[yellow]Download stopped by user before episode {i_episode}.")
                break

            # Update context tracker for the current episode
            context_tracker.season = index_season_selected
            context_tracker.episode = i_episode
            ep_obj = episodes[i_episode - 1]
            context_tracker.episode_name = (ep_obj.get("name") if isinstance(ep_obj, dict) else getattr(ep_obj, "name", None))
            context_tracker.poster_url = tmdb_artwork.resolve_episode_artwork_url(
                context_tracker.series_tmdb_id, index_season_selected, i_episode
            )

            if is_cached():
                console.print(f"[dim]Skipping episode {i_episode} of season {index_season_selected} — already in cache.")
                continue

            # Trigger the download callback for the current episode
            try:
                result = download_video_callback(episodes[i_episode - 1], index_season_selected, i_episode)
            except Exception as e:
                logger.exception("Error downloading episode %s of season %s", i_episode, index_season_selected)
                console.print(f"[red]Error downloading episode {i_episode} of season {index_season_selected}: {e}")
                result = DownloadResult(None, True, str(e) or e.__class__.__name__)

            res = DownloadResult.from_raw(result)
            stopped, error_msg = res.stopped, res.error

            # If callback signalled stop/failure, surface returned error (if any)
            if stopped or _is_user_stop_requested():
                if _is_user_stop_requested():
                    break
                if error_msg:
                    console.print(f"[red]Error: {error_msg}")
                console.print(f"[yellow]Warning: episode {i_episode} failed for season {index_season_selected}.")
                failed_episodes.append(i_episode)
                last_error = error_msg or last_error
            else:
                downloaded_any = True

    if failed_episodes:
        detail = f" - {last_error}" if last_error else ""
        summary = (
            f"{len(failed_episodes)} episode(s) failed for season "
            f"{index_season_selected} (episodes {failed_episodes}){detail}"
        )
        
        if not downloaded_any:
            # Nothing downloaded for this season: propagate so the CLI exits
            # non-zero and the GUI marks the download Failed instead of Done.
            raise RuntimeError(summary)
        
        console.print(f"[yellow]{summary}")
        logger.warning(summary)
