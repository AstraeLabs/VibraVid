# 26.11.2025

import re

from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client

from .client import get_api, hub_search
from .downloader import download_film, download_series

indice = 14
_useFor = "Serie"
_region = ["IT"]
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_SHOW_URL_RE = re.compile(r"pluto\.tv/(?:[a-z]{2}/)?shows/(?P<slug>[^/?#]+)", re.I)
_MOVIE_URL_RE = re.compile(r"pluto\.tv/(?:[a-z]{2}/)?movies/(?P<id>[^/?#]+)", re.I)


def register_cli_args(parser) -> list:
    """Register CLI options."""
    group = parser.add_argument_group("Pluto TV options")
    group.add_argument("--url", dest="url", default=None, metavar="URL", help="Pluto TV title URL (show or movie page, e.g. https://pluto.tv/it/shows/the-jeffersons-it/).")
    group.add_argument("--protocol", dest="protocol", default="dash", choices=["hls", "dash"], help="Streaming protocol to use (default: dash, PlayReady keys resolved automatically via the Pluto license server, needs a PlayReady CDM; hls is AES-128 and needs no CDM). Ads are always skipped: the episode's content clip is downloaded directly.")
    return ["url", "protocol"]


def _resolve_url_to_item(url: str) -> dict | None:
    """Resolve a Pluto TV show/movie page URL to a direct item dict (``--url``)."""
    text = (url or "").strip()
    if not text:
        return None

    show_match = _SHOW_URL_RE.search(text)
    if show_match:
        slug = show_match.group("slug").rstrip("/")
        console.print(f"[cyan]Detected series from URL: [green]{slug}")
        return {"id": slug, "name": slug, "type": "tv", "url": text, "year": None, "slug": slug}

    movie_match = _MOVIE_URL_RE.search(text)
    if movie_match:
        movie_id = movie_match.group("id").rstrip("/")
        console.print(f"[cyan]Detected movie from URL: [green]{movie_id}")
        return {"id": movie_id, "name": movie_id, "type": "movie", "url": text, "year": None}

    return None


def title_search(query: str) -> int:
    """
    Search for titles on Pluto TV

    Parameters:
        query (str): Search query

    Returns:
        int: Number of results found
    """
    entries_manager.clear()
    table_show_manager.clear()

    # Direct URL pasted as search text ("stringa o --url").
    if "pluto.tv" in (query or ""):
        direct = _resolve_url_to_item(query)
        if direct:
            entries_manager.add(Entries(**direct))
            return len(entries_manager)

    search_url = f"https://service-media-search.clusters.pluto.tv/v1/search?q={query}&limit=10"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        api = get_api()
        response = create_client(headers=api.get_request_headers()).get(search_url)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Parse response
    data = response.json().get("data", [])
    seen_ids = set()
    for dict_title in data:
        try:
            if dict_title.get("type") == "channel":
                continue

            define_type = "tv" if dict_title.get("type") == "series" else dict_title.get("type")
            entry_id = dict_title.get("id")

            entries_manager.add(Entries(id=entry_id, name=dict_title.get("name"), type=define_type, image=None, year=None))
            seen_ids.add(entry_id)

        except Exception as e:
            console.print(f"Error parsing entry: {e}")

    # The v1/search REST index is incomplete for some titles
    try:
        for hub_title in hub_search(query):
            entry_id = hub_title.get("id")
            if not entry_id or entry_id in seen_ids:
                continue

            entries_manager.add(
                Entries(
                    id=entry_id,
                    name=hub_title.get("name"),
                    type=hub_title.get("type"),
                    image=None,
                    year=None,
                    slug=hub_title.get("slug"),
                )
            )
            seen_ids.add(entry_id)

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, hub search error: {e}")

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
    resolve_url=_resolve_url_to_item,
)
