# 25.07.25

from datetime import datetime


# External libraries
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client
from StreamingCommunity.Api.Template import site_constants, MediaManager
from StreamingCommunity.Util.table import TVShowManager


# Logic
from .util.get_license import get_bearer_token


# Variable
console = Console()
media_search_manager = MediaManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles based on a search query.
      
    Parameters:
        - query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    media_search_manager.clear()
    table_show_manager.clear()
    class_mediaset_api = get_bearer_token()
    search_url = 'https://mediasetplay.api-graph.mediaset.it/'
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    params = {
        'extensions': f'{{"persistedQuery":{{"version":1,"sha256Hash":"{class_mediaset_api.getHash256()}"}}}}',
        'variables': f'{{"first":10,"property":"search","query":"{query}","uxReference":"filteredSearch"}}',
    }
    
    try:
        response = create_client(headers=class_mediaset_api.generate_request_headers()).get(search_url, params=params)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Parse response
    resp_json = response.json()
    items = resp_json.get("data", {}).get("getSearchPage", {}).get("areaContainersConnection", {}).get("areaContainers", [])[0].get("areas", [])[0].get("sections", [])[0].get("collections", [])[0].get("itemsConnection", {}).get("items", [])

    if len(items) == 1:
        return 0

    # Process items
    for item in items:
        is_series = (
            item.get("__typename") == "SeriesItem"
            or item.get("cardLink", {}).get("referenceType") == "series"
            or bool(item.get("seasons"))
        )
        item_type = "tv" if is_series else "film"

        # Get date
        date = item.get("year") or ''
        if not date:
            updated = item.get("updated") or item.get("r") or ''
            if updated:
                try:
                    date = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).year
                except Exception:
                    date = ''

        media_search_manager.add_media({
            "id": item.get("guid", ""),
            "name": item.get("cardTitle", "No Title"),
            "type": item_type,
            "image": None,
            "date": date,
            "url": item.get("cardLink", {}).get("value", "")
        })

    return media_search_manager.get_length()