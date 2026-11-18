# 16.11.26

import logging

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film, download_series

indice = 1
_useFor = "Anime"
msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def _pick_title(item: dict) -> str:
    return item.get("title_it") or item.get("title") or "No Title"


def title_search(query: str) -> int:
    """Search the site API (``/api/search?q=``) and collect anime entries."""
    entries_manager.clear()
    table_show_manager.clear()

    headers = {"user-agent": get_userAgent(), "accept": "application/json", "referer": f"{site_constants.FULL_URL}/"}
    try:
        with create_client(headers=headers) as client:
            response = client.get(f"{site_constants.FULL_URL}/api/search", params={"q": query})
        response.raise_for_status()
        items = response.json().get("data") or []
    except Exception as error:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {error}")
        return 0

    for item in items:
        kind = str(item.get("type") or "tv").lower().replace("_", " ")
        entries_manager.add(
            Entries(
                id=item.get("id"),
                name=_pick_title(item),
                type=kind,
                url=f"{site_constants.FULL_URL}/anime/{item.get('id')}",
                slug=item.get("slug") or "",
                year=str(item.get("year") or ""),
                image=(item.get("images") or {}).get("poster") or "",
                score=str(item.get("score") or ""),
                status=item.get("status"),
                episodes_count=item.get("episodes_count") or item.get("total_episodes"),
                mal_id=item.get("mal_id"),
                anilist_id=item.get("anilist_id"),
            )
        )

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
