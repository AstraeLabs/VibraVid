# 26.11.2025

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client

from .client import get_api, hub_search
from .downloader import download_series

indice = 17
_useFor = "Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def register_cli_args(parser) -> list:
    """Register CLI options."""
    group = parser.add_argument_group("Pluto TV options")
    group.add_argument("--protocol", dest="protocol", default="hls", choices=["hls", "dash"], help="Streaming protocol to use (default: hls). DASH resolves DRM keys automatically via the Pluto PlayReady license server.")
    return ["protocol"]


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
    download_series=download_series,
)
