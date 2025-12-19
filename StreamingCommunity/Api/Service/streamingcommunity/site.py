# 10.12.23

import json


# External libraries
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent
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

    try:
        response = create_client(headers={'user-agent': get_userAgent()}).get(f"{site_constants.FULL_URL}/it")
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        version = json.loads(soup.find('div', {'id': "app"}).get("data-page"))['version']

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME} version, request error: {e}")
        return 0

    search_url = f"{site_constants.FULL_URL}/it/search?q={query}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client(headers={'user-agent': get_userAgent(), 'x-inertia': 'true', 'x-inertia-version': version}).get(search_url)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Collect json data
    try:
        data = response.json().get('props').get('titles')
    except Exception as e:
        console.log(f"Error parsing JSON response: {e}")
        return 0

    for i, dict_title in enumerate(data):
        try:
            images = dict_title.get('images') or []
            filename = None
            preferred_types = ['poster', 'cover', 'cover_mobile', 'background']
            for ptype in preferred_types:
                for img in images:
                    if img.get('type') == ptype and img.get('filename'):
                        filename = img.get('filename')
                        break

                if filename:
                    break

            if not filename and images:
                filename = images[0].get('filename')

            image_url = None
            if filename:
                image_url = f"{site_constants.FULL_URL.replace('stream', 'cdn.stream')}/images/{filename}"

            # Extract date: prefer last_air_date, otherwise try translations (last_air_date or release_date)
            date = dict_title.get('last_air_date')
            if not date:
                for trans in dict_title.get('translations') or []:
                    if trans.get('key') in ('last_air_date', 'release_date') and trans.get('value'):
                        date = trans.get('value')
                        break

            media_search_manager.add_media({
                'id': dict_title.get('id'),
                'slug': dict_title.get('slug'),
                'name': dict_title.get('name'),
                'type': dict_title.get('type'),
                'date': date,
                'image': image_url
            })
            
        except Exception as e:
            print(f"Error parsing a film entry: {e}")

    # Return the number of titles found
    return media_search_manager.get_length()