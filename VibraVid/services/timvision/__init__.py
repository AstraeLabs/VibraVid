# 13.10.26
# By @Syncs-Luca98

import logging
import re
from typing import Any

from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .client import TimvisionClient
from .downloader import download_film, download_series
from .scrapper import get_content_metadata, search_timvision

console = Console()
logger = logging.getLogger(__name__)

indice = 20
_useFor = "Film_Serie"

entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def _extract_content_id_from_url(url: str) -> str | None:
    """Extract content ID from TIMvision URL."""
    # TIMvision URLs can be like:
    # https://www.timvision.it/film/sonic-2/12345
    # https://www.timvision.it/serie/sonic/12345
    # https://timvisiontv.tim.it/film/sonic-2/12345
    patterns = [
        r'/film/[^/]+/(\d+)',
        r'/serie/[^/]+/(\d+)',
        r'/cinema/[^/]+/(\d+)',
        r'/intrattenimento/[^/]+/(\d+)',
        r'contentId[=:](\d+)',
        r'["\']id["\']\s*:\s*["\'](\d+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _create_entry_from_metadata(metadata: dict, query: str) -> Entries | None:
    """Create an Entries object from content metadata."""
    try:
        content_id = metadata.get("id", metadata.get("contentId", metadata.get("pid", "")))
        if not content_id:
            return None
        
        title = metadata.get("title", metadata.get("name", metadata.get("displayName", query)))
        item_type = metadata.get("type", metadata.get("contentType", "")).lower()
        year = str(metadata.get("year", metadata.get("releaseYear", metadata.get("productionYear", "9999"))))
        image = metadata.get("image", metadata.get("poster", metadata.get("thumbnail", metadata.get("cover", ""))))
        
        if item_type in ("movie", "film", "vod"):
            content_type = "movie"
        elif item_type in ("series", "tv", "show", "season", "episode"):
            content_type = "tv"
        else:
            content_type = "movie"
        
        return Entries(
            id=str(content_id),
            name=title,
            type=content_type,
            year=year,
            image=image,
        )
    except Exception as e:
        logger.error(f"Error creating entry from metadata: {e}")
        return None


def title_search(query: str) -> int:
    """Search TIMvision for movies and TV shows."""
    entries_manager.clear()
    table_show_manager.clear()

    client = TimvisionClient()
    if not client.is_authenticated():
        if not client.login():
            console.print("[red]TIMvision: authentication required")
            return 0

    console.print(f"[cyan]Search query: [yellow]{query}")

    # Try hodor API search first
    results = search_timvision(query)
    
    if results:
        for item in results:
            entries_manager.add(Entries(
                id=item.get("id", ""),
                name=item.get("name", query),
                type=item.get("type", "movie"),
                year=item.get("year", "9999"),
                image=item.get("image", ""),
                availability=item.get("availability", "UNKNOWN"),
            ))
        return len(entries_manager)
    
    # Fallback: try to extract content ID from URL if user pasted a direct link
    if query.startswith("http"):
        content_id = _extract_content_id_from_url(query)
        if content_id:
            console.print(f"[cyan]Found content ID from URL: {content_id}")
            console.print("[cyan]Fetching metadata...")
            metadata = get_content_metadata(content_id)
            if metadata:
                entry = _create_entry_from_metadata(metadata, query)
                if entry:
                    # Add availability for direct URL fallback
                    try:
                        entry.availability = client.get_availability_type(content_id)
                    except Exception:
                        entry.availability = "UNKNOWN"
                    entries_manager.add(entry)
                    console.print(f"[green]Added: {entry.name} ({entry.type})")
                    return len(entries_manager)
    
    # No results
    console.print("[red]No results found.")
    return 0


def resolve_url(url: str) -> dict[str, Any] | None:
    """Resolve a direct TIMvision URL to content metadata."""
    content_id = _extract_content_id_from_url(url)
    if not content_id:
        console.print("[red]Could not extract content ID from URL")
        return None
    
    client = TimvisionClient()
    if not client.is_authenticated():
        if not client.login():
            return None
    
    metadata = get_content_metadata(content_id)
    if not metadata:
        console.print("[red]Could not fetch content metadata")
        return None
    
    entry = _create_entry_from_metadata(metadata, url)
    if not entry:
        return None
    
    return {
        "id": entry.id,
        "slug": entry.id,
        "name": entry.name,
        "type": entry.type,
        "image": entry.image,
        "year": entry.year,
    }


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
    site_name="timvision",
    resolve_url=resolve_url,
)