# 16.03.25

import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager, config_manager

from .client import CrunchyrollClient
from .downloader import download_film, download_series
from .scrapper import GetSerieInfo

indice = 6
_useFor = "Anime"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_SERIES_ID_RE = re.compile(r"/series/([A-Z0-9]+)", re.IGNORECASE)


def register_cli_args(parser) -> list:
    """
    Register CLI options.
    """
    group = parser.add_argument_group("Crunchyroll options")
    group.add_argument("--url", dest="url", default=None, metavar="URL", help="Crunchyroll series URL.")
    return ["url"]


def _resolve_url_to_item(url: str):
    """Resolve a Crunchyroll series URL to an item dict"""
    match = _SERIES_ID_RE.search(url)
    if not match:
        console.print("[red]Could not extract series ID from URL (expected .../series/<ID>/...)")
        return None

    series_id = match.group(1)

    try:
        info = GetSerieInfo(series_id)
        info.collect_season()
        name = getattr(info, "series_name", None) or series_id
        info.close()
    except Exception as e:
        console.print(f"[red]Error resolving Crunchyroll URL: {e}")
        return None

    console.print(f"[cyan]Detected series from URL: [green]{name}")
    return {"id": series_id, "name": name, "type": "tv", "url": f"https://www.crunchyroll.com/series/{series_id}", "image": None}


def title_search(query: str) -> int:
    """
    Search for titles based on a search query.

    Parameters:
        query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not config_manager.login.get("crunchyroll", "device_id") or not config_manager.login.get("crunchyroll", "etp_rt"):
        console.print(
            "[yellow]\nWarning: Crunchyroll device_id/etp_rt are not set, search will return no results for this site.\n"
            "[yellow]Set them in [cyan]Conf/login.json[/cyan] under [cyan]crunchyroll.device_id[/cyan] and [cyan]crunchyroll.etp_rt[/cyan]."
        )
        return 0

    client = CrunchyrollClient()
    if not client.start():
        console.print("[red] Failed to authenticate with Crunchyroll.")
        raise Exception("Failed to authenticate with Crunchyroll.")

    api_url = f"{client.api_base_url}/content/v2/discover/search"
    params = {
        "q": query,
        "n": 20,
        "type": "series,movie_listing",
        "ratings": "true",
        "locale": client.locale,
    }

    console.print(f"[cyan]Search url: [yellow]{api_url} [dim](locale {client.locale})")

    try:
        response = client.request(
            "GET",
            api_url,
            params=params,
            headers={"Referer": "https://www.crunchyroll.com/", "Origin": "https://www.crunchyroll.com"},
        )
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0
    finally:
        client.close()

    data = response.json()
    seen_ids = set()

    # Parse results
    for block in data.get("data", []):
        block_type = block.get("type")
        if block_type not in ("series", "movie_listing", "top_results"):
            continue

        for item in block.get("items", []):
            item_id = item.get("id")
            if not item_id or item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            tipo = None

            if item.get("type") == "movie_listing":
                tipo = "film"
            elif item.get("type") == "series":
                meta = item.get("series_metadata", {})

                # Heuristic: single episode series might be films
                if (
                    meta.get("episode_count") == 1
                    and meta.get("season_count", 1) == 1
                    and meta.get("series_launch_year")
                ):
                    description = item.get("description", "").lower()
                    if "film" in description or "movie" in description:
                        tipo = "film"
                    else:
                        tipo = "tv"
                else:
                    tipo = "tv"
            else:
                continue

            url = f"https://www.crunchyroll.com/series/{item_id}"
            title = item.get("title", "")

            # Get image
            poster_image = None
            list_image = item.get("images", {})
            if list_image:
                poster_wide = list_image.get("poster_wide")
                if poster_wide and len(poster_wide) > 0:
                    poster_image = poster_wide[0][-1].get("source")

            entries_manager.add(Entries(id=item_id, name=title, type=tipo, url=url, image=poster_image))

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
    resolve_url=_resolve_url_to_item,
)
