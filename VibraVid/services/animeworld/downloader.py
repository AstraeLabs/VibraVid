# 11.03.24

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import MP4_Downloader
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.player.sweetpixel import VideoSource
from VibraVid.services._base import Entries, anime_folder, print_episode_header, site_constants
from VibraVid.services._base.tv_display_manager import manage_selection, map_episode_path
from VibraVid.services._base.tv_download_manager import _is_user_stop_requested
from VibraVid.utils import os_manager, start_message

from .scrapper import ScrapSerie

console = Console()
msg = Prompt()


def download_film(select_title: Entries):
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    scrape_serie = ScrapSerie(select_title.url, site_constants.FULL_URL)
    episodes = scrape_serie.get_episodes()

    # Get episode information
    episode_data = episodes[0]
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> ([cyan]{scrape_serie.get_name()}) \n")

    # Define filename and path for the downloaded video
    serie_name_with_year = os_manager.get_sanitize_file(scrape_serie.get_name(), select_title.year)
    mp4_name = f"{serie_name_with_year}.mp4"
    mp4_path = anime_folder(serie_name_with_year.replace(".mp4", ""))

    # Create output folder
    os_manager.create_path(mp4_path)

    # Get video source for the episode
    video_source = VideoSource(site_constants.FULL_URL, episode_data, scrape_serie.session_id, scrape_serie.csrf_token)
    try:
        mp4_link = video_source.get_playlist()
    finally:
        video_source.close()

    # Start downloading
    return MP4_Downloader(url=str(mp4_link).strip(), path=os.path.join(mp4_path, mp4_name))


def download_episode(episode_data, index_select, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()

    # Episode number is 1-based
    episode_number = index_select + 1
    episode_name = f"Episode {episode_number}"
    series_name = scrape_serie.get_name()
    print_episode_header(series_name, episode=episode_number)

    # Add episode information to context tracker
    context_tracker.season = 1
    context_tracker.episode = episode_number
    context_tracker.episode_name = episode_name

    path_components, filename = map_episode_path(
        series_name=series_name,
        series_year=None,
        season_number=1,
        episode_number=episode_number,
        episode_name=episode_name,
        absolute_number=episode_number,
    )
    episode_path = anime_folder(*path_components)
    episode_filename = f"{filename}.mp4"

    # Create output folder
    os_manager.create_path(episode_path)

    # Get video source for the episode
    video_source = VideoSource(site_constants.FULL_URL, episode_data, scrape_serie.session_id, scrape_serie.csrf_token)
    try:
        mp4_link = video_source.get_playlist()
    finally:
        video_source.close()

    # Start downloading
    return MP4_Downloader(url=str(mp4_link).strip(), path=os.path.join(episode_path, episode_filename))


def download_series(
    select_title: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
):
    """
    Handle downloading a complete series.
    """
    start_message()

    # Create scrap instance
    if not scrape_serie:
        scrape_serie = ScrapSerie(select_title.url, site_constants.FULL_URL)
    episodes = scrape_serie.get_episodes()

    # Get episode count
    console.print(f"\n[green]Episodes count: [red]{len(episodes)}")

    # Display episodes list and get user selection
    if episode_selection is None:
        last_command = msg.ask("\n[cyan]Insert media [red]index [yellow]or [red]* [cyan]to download all media [yellow]or [red]1-2 [cyan]or [red]3-* [cyan]for a range of media")
    else:
        last_command = episode_selection

    list_episode_select = manage_selection(last_command, len(episodes))

    # Download selected episodes
    if len(list_episode_select) == 1 and last_command != "*":
        obj_episode = episodes[list_episode_select[0] - 1]
        return download_episode(obj_episode, list_episode_select[0] - 1, scrape_serie)
    else:
        for i_episode in list_episode_select:
            if _is_user_stop_requested():
                console.print("[yellow]Download interrupted by user.")
                break

            obj_episode = episodes[i_episode - 1]
            try:
                path, kill_handler, msg_error = download_episode(obj_episode, i_episode - 1, scrape_serie)
            except Exception as e:
                console.print(f"[red]Error in episode {i_episode}: {e}")
                continue

            if msg_error:
                console.print(f"[red]Episode {i_episode}: {msg_error}")

            if _is_user_stop_requested():
                console.print("[yellow]Download interrupted by user.")
                break
