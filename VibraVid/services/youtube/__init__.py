# 16.10.26
# By @Syncs-Luca98

import logging

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


def _search_youtube(query: str, limit: int = 25) -> list[dict]:
    """Search YouTube with the managed yt-dlp binary (ytsearch)."""
    info = YTDLPDownloader.extract_info(f"ytsearch{limit}:{query}")
    if not info:
        return []

    items = []
    for entry in info.get("entries") or []:
        video_id = entry.get("id") if entry else None
        if not video_id:
            continue
        items.append({
            "id": video_id,
            "title": entry.get("title") or "Unknown",
            "uploader": entry.get("uploader") or entry.get("channel") or "Unknown",
            "duration": entry.get("duration") or 0,
            "thumbnail": entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
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
        title = r["title"]
        uploader = r["uploader"]
        duration = r["duration"]
        entry = Entries(
            id=r["id"],
            name=f"{uploader} - {title}" if uploader else title,
            type="film",
            year="",
            image=r["thumbnail"],
            url=r["url"],
        )
        entry.title = title
        entry.artist = uploader
        entry.duration_seconds = duration or None
        entries_manager.add(entry)

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
    process_result=process_search_result,
)
