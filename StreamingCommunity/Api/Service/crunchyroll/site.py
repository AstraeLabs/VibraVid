# 16.03.25

# External libraries
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.table import TVShowManager
from StreamingCommunity.Api.Template import site_constants, MediaManager


# Logic
from .util.get_license import CrunchyrollClient


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

    config = config_manager.get_dict("SITE_LOGIN", "crunchyroll")
    if not config.get('device_id') or not config.get('etp_rt'):
        console.print("[red] device_id or etp_rt is missing or empty in config.json.")
        raise Exception("device_id or etp_rt is missing or empty in config.json.")

    client = CrunchyrollClient()
    if not client.start():
        console.print("[red] Failed to authenticate with Crunchyroll.")
        raise Exception("Failed to authenticate with Crunchyroll.")

    api_url = "https://www.crunchyroll.com/content/v2/discover/search"

    params = {
        "q": query,
        "n": 20,
        "type": "series,movie_listing",
        "ratings": "true",
        "preferred_audio_language": "it-IT",
        "locale": "it-IT"
    }

    console.print(f"[cyan]Search url: [yellow]{api_url}")

    try:
        response = client._request_with_retry('GET', api_url, params=params)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    data = response.json()
    found = 0

    # Parse results
    for block in data.get("data", []):
        if block.get("type") not in ("series", "movie_listing", "top_results"):
            continue

        for item in block.get("items", []):
            tipo = None

            if item.get("type") == "movie_listing":
                tipo = "film"
            elif item.get("type") == "series":
                meta = item.get("series_metadata", {})

                # Heuristic: single episode series might be films
                if meta.get("episode_count") == 1 and meta.get("season_count", 1) == 1 and meta.get("series_launch_year"):
                    description = item.get("description", "").lower()
                    if "film" in description or "movie" in description:
                        tipo = "film"
                    else:
                        tipo = "tv"
                else:
                    tipo = "tv"
            else:
                continue

            url = ""
            if tipo in ("tv", "film"):
                url = f"https://www.crunchyroll.com/series/{item.get('id')}"
            else:
                continue

            title = item.get("title", "")

            media_search_manager.add_media({
                'name': title,
                'type': tipo,
                'url': url
            })
            found += 1

    return media_search_manager.get_length()