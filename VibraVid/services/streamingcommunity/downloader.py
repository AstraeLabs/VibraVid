# 3.12.23

import logging
import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.player.vixcloud import ENABLE_VIXCLOUD_API_V2, VideoSource
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from .scrapper import GetSerieInfo

console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    skip_ts = bool((context_tracker.site_options or {}).get("skip_ts"))

    scraper = None
    if skip_ts or tmdb_client.api_key:
        scraper = GetSerieInfo(
            f"{site_constants.FULL_URL}/{select_title.provider_language}",
            media_id=select_title.id,
            series_name=select_title.slug,
        )

    if skip_ts and scraper is not None and scraper.is_cam():
        console.print(f"[yellow][SKIP] Download aborted: TS/CAM version detected for '{select_title.name}'")
        return None

    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name} \n")

    tmdb_data = None
    if tmdb_client.api_key is not None:
        site_tmdb_id = scraper.get_tmdb_id() if scraper is not None else None

        if site_tmdb_id:
            logger.info(f"Using provider-supplied TMDB id {site_tmdb_id} for '{select_title.name}'")
            tmdb_data = {"id": site_tmdb_id}
        else:
            result = tmdb_client.get_type_and_id_by_slug_year(
                select_title.slug, select_title.year, "movie", select_title.provider_language
            )
            if result and result.get("id") and result.get("type") == "movie":
                tmdb_data = {"id": result.get("id")}

    # Init class
    video_source = VideoSource(
        f"{site_constants.FULL_URL}/{select_title.provider_language}", False, select_title.id, tmdb_data=tmdb_data
    )

    # Retrieve iframe unless the TMDB API V2 resolution path is enabled and available
    if tmdb_data is None or not ENABLE_VIXCLOUD_API_V2:
        video_source.get_iframe(select_title.id)

    video_source.get_content()
    master_playlist = video_source.get_playlist()

    if master_playlist is None:
        console.print("[red]Error: No master playlist found")
        return None

    # Define the filename and path for the downloaded film
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    return HLS_Downloader(m3u8_url=master_playlist, output_path=os.path.join(movie_path, movie_name)).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, video_source):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    series_display = getattr(scrape_serie, "series_display_name", None) or scrape_serie.series_name
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{series_display} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    path_components, filename = map_episode_path(
        series_display,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"

    resolved_via_tmdb = False
    if tmdb_client.api_key is not None:
        try:
            site_tmdb_id = scrape_serie.get_tmdb_id()
        except Exception:
            site_tmdb_id = None

        if site_tmdb_id:
            logger.info(f"Using provider-supplied TMDB id {site_tmdb_id} for '{scrape_serie.series_name}'")
            video_source.tmdb_id = site_tmdb_id
            video_source.season_number = index_season_selected
            video_source.episode_number = index_episode_selected
            resolved_via_tmdb = True

        else:
            series_slug = scrape_serie.series_name.lower().replace(" ", "-").replace("'", "")
            result = tmdb_client.get_type_and_id_by_slug_year(
                str(series_slug), int(scrape_serie.year), "tv", scrape_serie.provider_language
            )

            if result and result.get("id") and result.get("type") == "tv":
                tmdb_id = result.get("id")
                video_source.tmdb_id = tmdb_id
                video_source.season_number = index_season_selected
                video_source.episode_number = index_episode_selected
                resolved_via_tmdb = True

    # Retrieve iframe unless the TMDB API V2 resolution path is enabled and available
    if not resolved_via_tmdb or not ENABLE_VIXCLOUD_API_V2:
        video_source.get_iframe(obj_episode.id)

    video_source.get_content()
    master_playlist = video_source.get_playlist()

    if master_playlist is None:
        console.print("[red]Error: No master playlist found")
        return None

    # Download the episode
    return HLS_Downloader(m3u8_url=master_playlist, output_path=os.path.join(episode_path, episode_name)).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    video_source = VideoSource(f"{site_constants.FULL_URL}/{select_season.provider_language}", True, select_season.id)

    if scrape_serie is None:
        from . import _effective_languages

        scrape_serie = GetSerieInfo(
            f"{site_constants.FULL_URL}/{select_season.provider_language}",
            select_season.id,
            select_season.slug,
            select_season.year,
            select_season.provider_language,
            series_display_name=select_season.name,
            languages=_effective_languages(),
        )
        scrape_serie.getNumberSeason()
        scrape_serie.series_display_name = select_season.name
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie, video_source)

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
