# 26.11.25

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability, create_client, get_userAgent

from .downloader import download_series

indice = 11
_useFor = "Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


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

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    search_url = f"https://public.aurora.enhanced.live/site/search/page/?include=default&filter[environment]=discoverychannelit&v=2&q={query}&page[number]=1&page[size]=20"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        with create_client(headers={"user-agent": get_userAgent()}) as client:
            response = client.get(search_url)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Collect json data
    try:
        data = response.json().get("data")
    except Exception as e:
        console.print(f"Error parsing JSON response: {e}")
        return 0

    for dict_title in data:
        try:
            # Skip non-showpage entries
            if dict_title.get("type") != "showpage":
                continue

            entries_manager.add(
                Entries(
                    name=dict_title.get("title"),
                    type="tv",
                    year=dict_title.get("dateLastModified").split("-")[0],
                    image=dict_title.get("image").get("url"),
                    url=f"https://public.aurora.enhanced.live/site/page/{str(dict_title.get('slug')).lower().replace(' ', '-')}/?include=default&filter[environment]=discoverychannelit&v=2&parent_slug={dict_title.get('parentSlug')}",
                )
            )

        except Exception as e:
            console.print(f"Error parsing a film entry: {e}")

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_series=download_series,
)
