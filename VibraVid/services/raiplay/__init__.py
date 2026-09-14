# 21.05.24

import concurrent.futures

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability, create_client, get_headers

from .downloader import download_film, download_series

indice = 4
_useFor = "Film_Serie"
_region = ["IT"]
_live_mux = True
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

DETECT_MEDIA_TYPE = True


def _detect_media_type(path_id: str) -> str:
    """Return 'movie' or 'tv' from RaiPlay program typology. Defaults to 'tv' on any error."""
    try:
        url = f"https://www.raiplay.it/{path_id.lstrip('/')}"
        with create_client(headers=get_headers()) as client:
            response = client.get(url)

        if response.status_code != 200:
            return "tv"

        data = response.json()
        typology = (
            (data.get("program_info", {}) or {}).get("typology")
            or (data.get("track_info", {}) or {}).get("typology")
            or ""
        )
        return "movie" if str(typology).strip().lower() == "film" else "tv"
    except Exception:
        return "tv"


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

    search_url = "https://www.raiplay.it/atomatic/raiplay-search-service/api/v1/msearch"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    json_data = {
        "templateIn": "6470a982e4e0301afe1f81f1",
        "templateOut": "6516ac5d40da6c377b151642",
        "params": {
            "param": query,
            "from": None,
            "sort": "relevance",
            "onlyVideoQuery": False,
        },
    }

    try:
        with create_client(headers=get_headers()) as client:
            response = client.post(search_url, json=json_data)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    try:
        response_data = response.json()
        cards = response_data.get("agg", {}).get("titoli", {}).get("cards", [])

        # Limit to only 15 results for performance
        data = cards[:15]

    except Exception as e:
        console.print(f"[red]Error parsing search results: {e}")
        return 0

    # Process each item and add to media manager
    for _idx, item in enumerate(data, 1):
        try:
            # Get path_id
            path_id = item.get("path_id", "")
            if not path_id:
                console.print("[yellow]Skipping item due to missing path_id")
                continue

            # Get image URL - handle both relative and absolute URLs
            image = item.get("immagine", "")
            if image and not image.startswith("http"):
                image = f"https://www.raiplay.it{image}"

            # Get URL - handle both relative and absolute URLs
            url = item.get("url", "")
            if url and not url.startswith("http"):
                url = f"https://www.raiplay.it{url}"

            entries_manager.add(
                Entries(
                    id=item.get("id", ""),
                    path_id=path_id,
                    name=item.get("titolo", "Unknown"),
                    type="tv",
                    url=url,
                    image=image,
                    year=image.split("/")[-4],
                )
            )

        except Exception as e:
            console.print(f"[red]Error processing item '{item.get('titolo', 'Unknown')}': {e}")
            continue

    if DETECT_MEDIA_TYPE:
        entries = [e for e in entries_manager.media_list if getattr(e, "path_id", None)]
        if entries:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                future_map = {executor.submit(_detect_media_type, e.path_id): e for e in entries}
                for future in concurrent.futures.as_completed(future_map):
                    try:
                        future_map[future].type = future.result()
                    except Exception:
                        pass

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
