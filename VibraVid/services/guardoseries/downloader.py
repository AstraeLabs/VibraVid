# 20.10.26
# By @Syncs-Luca98

import logging
import os

from VibraVid.core.downloader import HLS_Downloader
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.services.guardoseries.scrapper import (
    GetSerieInfo,
    get_episode_stream,
    get_film_stream,
    get_serie_episodes,
    search_guardoseries_slug,
)
from VibraVid.utils import config_manager, start_message

logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")

UQLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0",
    "Referer": "https://uqload.vc/",
    "Origin": "https://uqload.vc",
}

LOADM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0",
    "Referer": "https://loadm.cam/",
    "Origin": "https://loadm.cam",
}


def _stream_headers(stream_url: str) -> dict[str, str]:
    return LOADM_HEADERS if "loadm.cam" in stream_url.lower() else UQLOAD_HEADERS


def _download_from_stream(stream_url: str, output_path: str, movie_name: str) -> str:
    return HLS_Downloader(
        m3u8_url=stream_url,
        headers=_stream_headers(stream_url),
        manifest_refresh_fn=lambda: stream_url,
        output_path=os.path.join(output_path, movie_name),
    ).start()


def _resolve_slug(select_title: Entries) -> tuple[str | None, str | None]:
    """Prefer the slug already found by the site search, then fall back to a new search."""
    slug = (getattr(select_title, "slug", "") or "").strip()
    if slug:
        media_type = str(getattr(select_title, "type", "") or "").lower()
        return slug, "serie" if media_type in ("tv", "serie", "show") else "film"
    return search_guardoseries_slug(select_title.name, getattr(select_title, "year", None))


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.

    Parameters:
        select_title (Entries): Class with info about film title.

    Return:
        - str: output path
    """
    start_message()
    print_download_header(select_title)

    slug, content_type = _resolve_slug(select_title)
    if not slug:
        logger.error(f"No Guardaserie slug found for {select_title.name}")
        return None

    stream_url = get_film_stream(slug)
    if not stream_url:
        logger.error(f"No stream found for film {select_title.name}")
        return None

    # Define output path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    return _download_from_stream(stream_url, movie_path, movie_name)


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, serie_slug):
    """Downloads a specific episode from a Guardaserie-backed series."""
    start_message()
    series_display = getattr(scrape_serie, "series_display_name", None) or scrape_serie.series_name
    print_episode_header(series_display, obj_episode.name, index_season_selected, index_episode_selected)

    # Find the episode URL from scrape_serie's cached episodes
    episodes_by_season = getattr(scrape_serie, "_episodes_by_season", {}) or {}
    season_episodes = episodes_by_season.get(index_season_selected, [])
    episode_url = None
    for ep in season_episodes:
        if ep["number"] == index_episode_selected:
            episode_url = ep["url"]
            break

    if not episode_url:
        logger.error(f"No episode URL found for S{index_season_selected}E{index_episode_selected}")
        return None

    stream_url = get_episode_stream(episode_url)
    if not stream_url:
        logger.error(f"No stream found for episode {episode_url}")
        return None

    # Define output path
    path_components, filename = map_episode_path(
        series_display,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = series_folder(*path_components)
    episode_name = f"{filename}.{extension_output}"

    return _download_from_stream(stream_url, episode_path, episode_name)


def download_series(
    select_title: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    slug, content_type = _resolve_slug(select_title)
    if not slug:
        logger.error(f"No Guardaserie slug found for series {select_title.name}")
        return

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(
            select_title.name,
            getattr(select_title, "tmdb_id", None),
            select_title.year,
            serie_slug=slug,
        )
        scrape_serie.getNumberSeason()

    # Get all episodes grouped by season
    episodes_by_season = get_serie_episodes(slug)
    scrape_serie._episodes_by_season = episodes_by_season

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie, slug)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=len(scrape_serie.seasons_manager),
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
