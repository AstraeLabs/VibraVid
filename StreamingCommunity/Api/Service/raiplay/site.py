# 21.05.24

# External libraries
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_headers
from StreamingCommunity.Api.Template import site_constants, MediaManager
from StreamingCommunity.Util.table import TVShowManager


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

    search_url = "https://www.raiplay.it/atomatic/raiplay-search-service/api/v1/msearch"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    json_data = {
        'templateIn': '6470a982e4e0301afe1f81f1',
        'templateOut': '6516ac5d40da6c377b151642',
        'params': {
            'param': query,
            'from': None,
            'sort': 'relevance',
            'onlyVideoQuery': False,
        },
    }

    try:
        response = create_client(headers=get_headers()).post(search_url, json=json_data)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    try:
        response_data = response.json()
        cards = response_data.get('agg', {}).get('titoli', {}).get('cards', [])
        
        # Limit to only 15 results for performance
        data = cards[:15]
        console.print(f"[cyan]Found {len(cards)} results, processing first {len(data)}...")
        
    except Exception as e:
        console.print(f"[red]Error parsing search results: {e}")
        return 0
    
    # Process each item and add to media manager
    for idx, item in enumerate(data, 1):
        try:
            # Get path_id
            path_id = item.get('path_id', '')
            if not path_id:
                console.print("[yellow]Skipping item due to missing path_id")
                continue

            # Get image URL - handle both relative and absolute URLs
            image = item.get('immagine', '')
            if image and not image.startswith('http'):
                image = f"https://www.raiplay.it{image}"
            
            # Get URL - handle both relative and absolute URLs
            url = item.get('url', '')
            if url and not url.startswith('http'):
                url = f"https://www.raiplay.it{url}"

            media_search_manager.add_media({
                'id': item.get('id', ''),
                'path_id': path_id,
                'name': item.get('titolo', 'Unknown'),
                'type': 'tv',
                'url': url,
                'image': image,
                'year': image.split("/")[5]
            })
    
        except Exception as e:
            console.print(f"[red]Error processing item '{item.get('titolo', 'Unknown')}': {e}")
            continue
    
    return media_search_manager.get_length()