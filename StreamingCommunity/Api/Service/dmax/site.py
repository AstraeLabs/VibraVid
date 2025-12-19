# 26.11.2025


# External libraries
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent
from StreamingCommunity.Util.table import TVShowManager
from StreamingCommunity.Api.Template import site_constants, MediaManager


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

    search_url = f"https://public.aurora.enhanced.live/site/search/page/?include=default&filter[environment]=dmaxit&v=2&q={query}&page[number]=1&page[size]=20"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client(headers={'user-agent': get_userAgent()}).get(search_url)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Collect json data
    try:
        data = response.json().get('data')
    except Exception as e:
        console.log(f"Error parsing JSON response: {e}")
        return 0

    for dict_title in data:
        try:
            # Skip non-showpage entries
            if dict_title.get('type') != 'showpage':
                continue
            
            media_search_manager.add_media({
                'name': dict_title.get('title'),
                'type': 'tv',
                'date': dict_title.get('dateLastModified').split('T')[0],
                'image': dict_title.get('image').get('url'),
                'url': f'https://public.aurora.enhanced.live/site/page/{str(dict_title.get("slug")).lower().replace(" ", "-")}/?include=default&filter[environment]=dmaxit&v=2&parent_slug={dict_title.get("parentSlug")}',
            })
            
        except Exception as e:
            print(f"Error parsing a film entry: {e}")
	
    # Return the number of titles found
    return media_search_manager.get_length()