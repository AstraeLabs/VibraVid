# 29.05.26
# By @UrloMythus

import html
import logging
import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film, download_series

indice = 16
_useFor = "Serie"

msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()
logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(?<![/\d])(19|20)\d{2}(?![/\d])")


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()

    base_url = site_constants.FULL_URL
    headers = {"User-Agent": get_userAgent()}

    try:
        with create_client(headers=headers) as client:
            resp = client.get(f"{base_url}/wp-json/wp/v2/search", params={"search": query, "_fields": "id"})
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        console.print(f"[red]Eurostreaming search error: {e}")
        return 0

    for item in results[:20]:
        post_id = item.get("id")
        if not post_id:
            continue

        try:
            with create_client(headers=headers) as client:
                post_resp = client.get(f"{base_url}/wp-json/wp/v2/posts/{post_id}", params={"_fields": "content,title"})
            post_resp.raise_for_status()
            data = post_resp.json()
            title = html.unescape(data.get("title", {}).get("rendered", ""))
            content = data.get("content", {}).get("rendered", "")

            year_m = _YEAR_RE.search(content)
            year = year_m.group(0) if year_m else None

            entries_manager.add(
                Entries(
                    id=post_id,
                    name=title,
                    type="tv",
                    slug="",
                    year=year,
                )
            )

        except Exception as e:
            logger.error(f"[Eurostreaming] Post fetch failed id={post_id}: {e}")

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
