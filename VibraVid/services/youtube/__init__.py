# 16.10.26
# By @Syncs-Luca98

import logging
import re

from rich.console import Console

from VibraVid.core.downloader import YTDLPDownloader
from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import base_process_search_result, make_search_entrypoints
from VibraVid.utils import TVShowManager

from .downloader import download_video

indice = 15
_useFor = "Film_Serie"
console = Console()
logger = logging.getLogger(__name__)
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")


def _video_from_info(entry: dict) -> dict | None:
    """Normalise one yt-dlp video object (search result or single video) to the fields used here."""
    video_id = entry.get("id") if entry else None
    if not video_id:
        return None
    return {
        "id": video_id,
        "title": entry.get("title") or "Unknown",
        "uploader": entry.get("uploader") or entry.get("channel") or "Unknown",
        "duration": entry.get("duration") or 0,
        "thumbnail": entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def _video_entry(video: dict) -> Entries:
    entry = Entries(
        id=video["id"],
        name=f"{video['uploader']} - {video['title']}" if video["uploader"] else video["title"],
        type="film",
        year="",
        image=video["thumbnail"],
        url=video["url"],
    )
    entry.title = video["title"]
    entry.artist = video["uploader"]
    entry.duration_seconds = video["duration"] or None
    return entry


def _resolve_url_to_item(url: str) -> dict | None:
    """Resolve a YouTube video URL (watch, youtu.be, shorts, embed, live) to a direct item dict."""
    match = _VIDEO_ID_RE.search(url or "")
    if not match:
        return None

    # Only the video: a `&list=` in the link must not turn the lookup into a playlist listing.
    info = YTDLPDownloader.extract_info(f"https://www.youtube.com/watch?v={match.group(1)}")
    video = _video_from_info(info) if info else None
    if not video:
        console.print(f"[red]Could not read the YouTube video: {url}")
        return None
    return _video_entry(video).to_dict()


def _search_youtube(query: str, limit: int = 25) -> list[dict]:
    """Search YouTube with the managed yt-dlp binary (ytsearch)."""
    info = YTDLPDownloader.extract_info(f"ytsearch{limit}:{query}")
    if not info:
        return []

    items = [video for video in map(_video_from_info, info.get("entries") or []) if video]
    return items[:limit]


def title_search(query: str) -> int:
    """Search YouTube for videos."""
    entries_manager.clear()
    table_show_manager.clear()

    try:
        results = _search_youtube(query, limit=25)
    except Exception as e:
        logger.exception(f"[youtube] Search failed for {query!r}")
        console.print(f"[red]Search failed: {e}")
        return 0

    logger.info(f"[youtube] query={query!r} -> {len(results)} video(s)")

    for r in results:
        entries_manager.add(_video_entry(r))

    return len(entries_manager)


def process_search_result(select_title, selections=None, scrape_serie=None):
    """Route selected video to download_video."""
    if select_title is None:
        console.print("[yellow]No title selected.")
        return False

    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_video,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    resolve_url=_resolve_url_to_item,
    process_result=process_search_result,
)
