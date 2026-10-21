# 21.10.26

from concurrent.futures import ThreadPoolExecutor

from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability, create_client, get_userAgent

from .downloader import download_series

indice = 6
_useFor = "Serie"
_region = ["IT"]
_live_mux = True
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

BRANDS = {
    "Discovery": "discoverychannelit",
    "Nove": "nove",
    "Food Network": "foodnetwork",
    "HGTV": "hgtvit",
    "Real Time": "realtime",
    "DMAX": "dmaxit",
}

SEARCH_URL = "https://public.aurora.enhanced.live/site/search/page/"
PAGE_URL = "https://public.aurora.enhanced.live/site/page"


def _search_brand(brand: str, environment: str, query: str) -> list[Entries]:
    """Search a single Aurora brand; a failing brand returns no results instead of aborting the whole search."""
    params = {
        "include": "default",
        "filter[environment]": environment,
        "v": "2",
        "q": query,
        "page[number]": "1",
        "page[size]": "20",
    }

    try:
        with create_client(headers={"user-agent": get_userAgent()}) as client:
            response = client.get(SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json().get("data") or []
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, {brand} search error: {e}")
        return []

    entries = []
    for dict_title in data:
        try:
            # Skip non-showpage entries (articles, categories)
            if dict_title.get("type") != "showpage":
                continue

            slug = str(dict_title.get("slug")).lower().replace(" ", "-")
            entries.append(
                Entries(
                    name=dict_title.get("title"),
                    type="tv",
                    year="9999",  # the API only has page dates (not the first-air year): "9999" makes EntriesManager resolve it from TMDB
                    brand=brand,
                    image=(dict_title.get("image") or {}).get("url"),
                    url=f"{PAGE_URL}/{slug}/?include=default&filter[environment]={environment}&v=2&parent_slug={dict_title.get('parentSlug')}",
                )
            )

        except Exception as e:
            console.print(f"Error parsing a {brand} entry: {e}")

    return entries


def title_search(query: str) -> int:
    """
    Search for titles on every Aurora brand.

    Parameters:
        query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    console.print(f"[cyan]Search on: [yellow]{', '.join(BRANDS)}")

    with ThreadPoolExecutor(max_workers=len(BRANDS)) as executor:
        results = list(executor.map(lambda brand: _search_brand(brand, BRANDS[brand], query), BRANDS))

    # Merged in BRANDS order; EntriesManager drops the same show returned by more than one brand
    for brand_entries in results:
        for entry in brand_entries:
            entries_manager.add(entry)

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_series=download_series,
)
