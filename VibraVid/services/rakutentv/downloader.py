# 09.10.26

import os

from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader
from VibraVid.core.downloader.base import DownloadResult
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from . import client as api
from .scrapper import GetSerieInfo

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")
_SDH_TYPES = {"sdh", "hearing_impaired", "hard_of_hearing", "hi"}
_CC_TYPES = {"cc", "closed_captions", "closed_caption"}


def _options() -> dict:
    return context_tracker.site_options or {}


def _resolve_stream(content_id: str, content_type: str, market: str | None = None) -> tuple[dict, str]:
    opts = _options()
    audio = api.audio_language(market, None, opts.get("audio_lang"))
    quality = (opts.get("quality") or "FHD").upper()
    device = (opts.get("device") or "lgui40").lower()
    if device not in api.DEVICES:
        device = "lgui40"

    kind = opts.get("kind") or "avod"
    if content_type in ("movies", "episodes") and kind == "unknown":
        kind = "avod"  # resolved below when detail is available

    if kind == "avod":
        return api.get_avod_streaming(content_id, content_type, audio, quality, device, market=market), kind
    return api.get_me_streaming(content_id, content_type, audio, quality, device, market=market), kind


def _subtitle_tracks(stream_info: dict) -> list[dict]:
    tracks = []
    for sub in stream_info.get("all_subtitles") or []:
        url = sub.get("url")
        if not url:
            continue

        fmt = str(sub.get("format") or "vtt").strip("[]' ").lower() or "vtt"
        locale = (sub.get("locale") or sub.get("language") or "und").lower()
        kind = str(sub.get("subtitle_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
        forced = kind == "forced" or bool(sub.get("forced"))
        sdh = kind in _SDH_TYPES
        cc = kind in _CC_TYPES
        flags = [name for name, on in (("forced", forced), ("sdh", sdh), ("cc", cc)) if on]
        label = str(sub.get("language") or locale).upper()
        tracks.append(
            {
                "type": "subtitle",
                "url": url,
                "language": "-".join([locale, *flags]),
                "format": fmt,
                "extension": fmt,
                "name": " ".join([label, *flags]),
                "forced": forced,
                "sdh": sdh,
                "cc": cc,
            }
        )
    return tracks


def _dash_download(mpd_url: str, license_url: str, streaming_id: str, output_path: str, subs: list):
    if api.is_playready_license(license_url):
        drm = DRMType.PLAYREADY
        license_headers = api.playready_license_headers(streaming_id)
    else:
        drm = DRMType.WIDEVINE
        license_headers = api.widevine_license_headers()
    
    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_headers=api.mpd_headers(),
        output_path=output_path,
        license_url=license_url,
        license_headers=license_headers,
        drm_preference=drm,
        other_tracks=subs or None,
    ).start()


def _movie_detail(movie_id: str, market: str | None) -> dict:
    try:
        return api.get_movie_detail(movie_id, market)
    except Exception as e:
        console.print(f"[yellow]Detail lookup failed for '{movie_id}': {e} — trying AVOD.")
        return {}


def download_film(select_title: Entries) -> DownloadResult:
    start_message()
    print_download_header(select_title)

    content_id = getattr(select_title, "id", None) or getattr(select_title, "slug", None)
    if not content_id:
        console.print("[red]Error: missing Rakuten content id.")
        return DownloadResult(None, True)

    market = getattr(select_title, "market", None)
    detail = _movie_detail(content_id, market)
    kind = getattr(select_title, "kind", None) or (api.purchase_kind(detail) if detail else "avod")
    if kind != "avod":
        console.print(f"[cyan]Title kind: {kind} — using authenticated /me streaming.")
    
    try:
        if kind == "avod":
            opts = _options()
            stream = api.get_avod_streaming(
                content_id,
                "movies",
                api.audio_language(market, detail, opts.get("audio_lang")),
                (opts.get("quality") or "FHD").upper(),
                (opts.get("device") or "lgui40").lower(),
                market=market,
            )
        else:
            stream = api.get_me_streaming(content_id, "movies", market=market)
    except Exception as e:
        console.print(f"[red]Error getting stream: {e}")
        return DownloadResult(None, True)

    path_components, filename = map_movie_path(select_title.name, getattr(select_title, "year", None))
    output_path = os.path.join(movie_folder(*path_components), f"{filename}.{extension_output}")
    return _dash_download(
        stream["url"], stream.get("license_url"), api.streaming_uuid(stream), output_path, _subtitle_tracks(stream)
    )


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    start_message()
    print_episode_header(scrape_serie.series_name, obj_episode.name, index_season_selected, index_episode_selected)

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    output_path = os.path.join(series_folder(*path_components), f"{filename}.{extension_output}")
    kind = getattr(scrape_serie, "kind", "avod")
    market = getattr(scrape_serie, "market", None)
    try:
        if kind == "avod":
            opts = _options()
            audio = api.audio_language(market, None, opts.get("audio_lang")) if opts.get("audio_lang") else (getattr(scrape_serie, "audio", None) or api.audio_language(market))
            stream = api.get_avod_streaming(
                obj_episode.id,
                "episodes",
                audio,
                (opts.get("quality") or "FHD").upper(),
                (opts.get("device") or "lgui40").lower(),
                market=market,
            )
        else:
            stream = api.get_me_streaming(obj_episode.id, "episodes", market=market)
    except Exception as e:
        console.print(f"[red]Error getting stream: {e}")
        return DownloadResult(None, True)

    return _dash_download(
        stream["url"], stream.get("license_url"), api.streaming_uuid(stream), output_path, _subtitle_tracks(stream)
    )


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(
            getattr(select_season, "id", None) or select_season.slug,
            select_season.name,
            getattr(select_season, "year", None),
            getattr(select_season, "market", None),
        )
        scrape_serie.getNumberSeason()
        scrape_serie.load_stream_info()
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


def download_live(select_title: Entries):
    start_message()
    print_download_header(select_title)

    content_id = getattr(select_title, "id", None)
    if not content_id:
        console.print("[red]Error: missing channel id.")
        return DownloadResult(None, True)

    try:
        stream = api.get_live_streaming(content_id, market=getattr(select_title, "market", None))
    except Exception as e:
        console.print(f"[red]Error getting live stream: {e}")
        return DownloadResult(None, True)

    url = stream.get("url") or ""
    path_components, filename = map_movie_path(select_title.name, getattr(select_title, "year", None))
    output_path = os.path.join(movie_folder(*path_components), f"{filename}.{extension_output}")

    if ".mpd" in url:
        return _dash_download(url, stream.get("license_url"), api.streaming_uuid(stream), output_path, [])
    return HLS_Downloader(m3u8_url=url, output_path=output_path).start()
