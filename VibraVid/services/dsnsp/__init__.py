# 21.09.26

import base64
import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .client import get_client
from .downloader import download_film, download_series
from .scrapper import GetContentInfo

indice = 21
_useFor = "Film_Serie"
_live_mux = True
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


_TITLE_RE = re.compile(
    r"https?://(?:www\.)?disneyplus\.com/(?:[^/]+/){2}(?:browse)/(?P<id>entity-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def _result_type(result: dict) -> str:
    """Return the media type encoded in the search metadata."""
    for source in (result.get("actions", []), (result.get("attributes", {}) or {}).get("actions", [])):
        action = next(iter(source), {}) if source else {}
        info_block = action.get("infoBlock", "")
        if not info_block:
            continue
        try:
            decoded = base64.b64decode(info_block).decode("utf-8", errors="ignore").lower()
        except (ValueError, TypeError):
            decoded = ""
        if "series" in decoded:
            return "tv"
        return "film"
    return "film"


def _normalize_result(result: dict) -> dict:
    """Normalize a search result item from any response format."""
    attrs = result.get("attributes", {}) or {}
    if attrs:
        visuals = attrs.get("visuals", {}) or {}
        description = visuals.get("description", {}) or {}
        metastring = visuals.get("metastringParts", {}) or {}
        release_year_range = metastring.get("releaseYearRange", {}) or {}
        return {
            "id": result.get("id", ""),
            "actions": result.get("actions", []) or attrs.get("actions", []),
            "visuals": {
                "title": visuals.get("title", attrs.get("title", "")),
                "description": description,
                "metastringParts": metastring,
            },
            "year": release_year_range.get("startYear"),
            "type": result.get("type", attrs.get("type", "movie")),
        }
    
    visuals = result.get("visuals", {}) or {}
    description = visuals.get("description", {}) or {}
    metastring = visuals.get("metastringParts", {}) or {}
    release_year_range = metastring.get("releaseYearRange", {}) or {}
    return {
        "id": result.get("id", ""),
        "actions": result.get("actions", []),
        "visuals": {
            "title": visuals.get("title", ""),
            "description": description,
            "metastringParts": metastring,
        },
        "year": release_year_range.get("startYear"),
        "type": result.get("type", "movie"),
    }


def register_cli_args(parser) -> list:
    """
    Register CLI options.

    Returns:
        list[str]: dest names of registered args.
    """
    group = parser.add_argument_group(f"{site_constants.SITE_NAME} options")
    group.add_argument("--url", dest="url", default=None, metavar="URL", help="Title URL.")
    group.add_argument("--imax", dest="imax", action="store_true", default=False, help="Prefer IMAX Enhanced version.")
    group.add_argument("--remastered-ar", dest="remastered_ar", action="store_true", default=False, help="Prefer Remastered Aspect Ratio.")
    group.add_argument("--drm_type", "--drm-type", dest="drm_type", default="pr", choices=["wv", "pr"], help="DRM system to use (default: pr). Requested resolution is capped to what the configured CDM's security level actually gets granted: PlayReady SL3000 / Widevine L1 -> 4K, PlayReady SL2000 -> 1080p, Widevine L3 -> 720p.")
    return ["url", "imax", "remastered_ar", "drm_type"]


def _resolve_url_to_item(url: str):
    """Resolve a URL to an item dict."""
    match = _TITLE_RE.search(url)
    if not match:
        console.print("[red]Could not extract content ID from URL")
        return None
    entity_id = match.group("id")
    console.print(f"[cyan]Detected content: [green]{entity_id}")

    try:
        content_type = GetContentInfo(get_client(), entity_id).get_type()
    except Exception as e:
        console.print(f"[yellow][!][/yellow] Could not determine content type for {entity_id}: {e}")
        content_type = "movie"

    return {
        "id": entity_id,
        "name": entity_id,
        "type": "tv" if content_type == "series" else "movie",
        "url": url,
    }


def title_search(query: str) -> int:
    """
    Search for titles.

    Parameters:
        query (str): Search query.

    Returns:
        int: Number of results found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    client = get_client()
    console.print(f"[cyan]Searching for: [yellow]{query}")

    try:
        results = client.search(query)
    except Exception as e:
        console.print(f"[red]Error during search: {e}")
        return 0

    if not results:
        console.print("[red]No results found.")
        return 0

    for result in results:
        norm = _normalize_result(result)
        entity_id = "entity-" + norm.get("id", "")
        title = norm.get("visuals", {}).get("title", entity_id)
        description = norm.get("visuals", {}).get("description", {})
        year = norm.get("year")
        entries_manager.add(
            Entries(
                id=entity_id,
                name=title,
                type=_result_type(norm),
                year=str(year) if year else None,
                url=f"https://www.disneyplus.com/browse/{entity_id}",
                desc=description.get("brief", "") if description else "",
            )
        )

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
    resolve_url=_resolve_url_to_item,
)
