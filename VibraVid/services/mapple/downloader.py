# 29.09.26

import os

from rich.console import Console

from VibraVid.core.downloader import HLS_Downloader
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message

from .client import get_stream
from .scrapper import GetSerieInfo

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def _download_hls(
    stream_url: str,
    stream_headers: dict[str, str],
    subtitle_tracks: list[dict],
    output_path: str,
):
    return HLS_Downloader(
        m3u8_url=stream_url,
        headers=stream_headers or None,
        output_path=output_path,
        other_tracks=subtitle_tracks or None,
        use_curl_cffi_segments=True,
        curl_cffi_segment_browser=None,
        hls_playlist_retry_statuses=(404,),
        hls_playlist_retry_attempts=3,
    ).start()


def download_film(select_title: Entries):
    start_message()
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} "
        f"-> [cyan]{select_title.name}\n"
    )

    tmdb_id = getattr(select_title, "id", None) or getattr(select_title, "tmdb_id", None)
    if not tmdb_id:
        raise ValueError(f"[Mapple] No TMDB ID for '{select_title.name}'")

    stream_url, stream_headers, subtitle_tracks = get_stream(int(tmdb_id), "movie")
    console.print(f"[cyan]Stream: {stream_url[:70]}...\n")

    path_components, filename = map_movie_path(select_title.name, select_title.year)
    out_dir = os_manager.get_sanitize_path(movie_folder(*path_components))
    out_path = os.path.join(out_dir, f"{filename}.{extension_output}")

    return _download_hls(
        stream_url,
        stream_headers,
        subtitle_tracks,
        out_path,
    )


def download_episode(
    obj_episode,
    index: int,
    scrape_serie: GetSerieInfo,
    season_number: int,
):
    start_message()
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} "
        f"-> [cyan]{scrape_serie.series_name} "
        f"(S{season_number}E{obj_episode.number})\n"
    )

    stream_url, stream_headers, subtitle_tracks = get_stream(
        scrape_serie.tmdb_id,
        "tv",
        season=season_number,
        episode=int(obj_episode.number),
    )
    console.print(f"[cyan]Stream: {stream_url[:70]}...\n")

    path_components, filename = map_episode_path(
        series_name=scrape_serie.series_name,
        series_year=scrape_serie.series_year,
        season_number=season_number,
        episode_number=int(obj_episode.number),
        episode_name=obj_episode.name,
    )
    out_dir = os_manager.get_sanitize_path(series_folder(*path_components))
    out_path = os.path.join(out_dir, f"{filename}.{extension_output}")

    return _download_hls(
        stream_url,
        stream_headers,
        subtitle_tracks,
        out_path,
    )


def download_series(
    select_title: Entries,
    season_selection: str = None,
    episode_selection: str = None,
    scrape_serie: GetSerieInfo = None,
):
    start_message()

    tmdb_id = getattr(select_title, "id", None) or getattr(select_title, "tmdb_id", None)
    if not tmdb_id:
        raise ValueError(f"[Mapple] No TMDB ID for '{select_title.name}'")

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(int(tmdb_id), select_title.name)
        scrape_serie.getNumberSeason()

    seasons_count = scrape_serie.getNumberSeason()

    def download_episode_callback(
        season_number: int,
        download_all: bool,
        episode_selection: str = None,
    ):
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(
                obj_episode,
                episode_idx,
                scrape_serie,
                season_idx,
            )

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
