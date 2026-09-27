# 27.09.26

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

from rich.console import Console

from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.downloader.base import DownloadResult
from VibraVid.player.maxstream import MaxStreamSource
from VibraVid.player.vidxgo import VideoSource as VidXgoVideoSource
from VibraVid.services._base import Entries, movie_folder
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.utils import config_manager, start_message

from .client import fetch_detail_page
from .scrapper import CineblogSource, parse_detail_sources, source_kind

console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")

_IMDB_PATH_RE = re.compile(r"/tt(\d+)(?:/|$)", re.IGNORECASE)
_SUPPORTED_RESOLVER_KINDS = {"hls", "vidxgo", "maxstream"}


def _output_path(select_title: Entries) -> str:
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    return os.path.join(movie_path, f"{filename}.{extension_output}")


def _resolve_vidxgo(source: CineblogSource, referer: str) -> tuple[str | None, dict]:
    match = _IMDB_PATH_RE.search(urlsplit(source.url).path)
    if not match:
        return None, {}

    video_source = VidXgoVideoSource(
        match.group(1),
        embed_domain=f"{urlsplit(source.url).scheme}://{urlsplit(source.url).netloc}",
        content_type="movie",
        referer=referer,
    )
    playlist = video_source.get_playlist()
    return playlist, video_source.get_playback_headers() if playlist else {}


def _resolve_source(source: CineblogSource, referer: str) -> tuple[str | None, dict]:
    kind = source_kind(source)

    if kind == "hls":
        return source.url, {"Referer": referer}

    if kind == "vidxgo":
        return _resolve_vidxgo(source, referer)

    if kind == "maxstream":
        return MaxStreamSource(source.url, referer=referer).get_stream()

    return None, {}


def download_film(select_title: Entries):
    """Resolve an accessible CB01 player through existing VibraVid resolvers and download it."""
    start_message()
    console.print(
        f"[bold yellow]Download: [red]cineblog01[/red] -> [cyan]{select_title.name}[/cyan]"
    )

    try:
        html, final_url = fetch_detail_page(select_title.url)
    except Exception as error:
        return DownloadResult(None, True, f"CB01 detail request failed: {error}")

    sources = parse_detail_sources(html, final_url)
    if not sources:
        return DownloadResult(None, True, "CB01 did not expose any player or download source")

    player_sources = [source for source in sources if source.section == "player"]
    candidates = player_sources or sources

    errors: list[str] = []
    for source in candidates:
        kind = source_kind(source)

        if source.verification_required:
            errors.append(f"{source.host or source.label}: browser verification required")
            continue

        if kind == "vixsrc":
            errors.append(
                "vixsrc: player detected but no compatible VibraVid resolver is available"
            )
            continue

        if kind not in _SUPPORTED_RESOLVER_KINDS:
            errors.append(
                f"{source.host or source.label}: unsupported external source"
            )
            continue

        try:
            playlist, headers = _resolve_source(source, final_url)
        except Exception as error:
            logger.warning("CB01 resolver failed for %s: %s", source.url, error)
            errors.append(f"{kind}: {error}")
            continue

        if not playlist:
            errors.append(f"{kind}: no playable stream resolved")
            continue

        return HLS_Downloader(
            m3u8_url=playlist,
            headers=headers,
            output_path=_output_path(select_title),
        ).start()

    if errors:
        return DownloadResult(None, True, "CB01 source unavailable: " + "; ".join(errors))

    return DownloadResult(
        None,
        True,
        "CB01 sources are external and no compatible VibraVid resolver is available",
    )
