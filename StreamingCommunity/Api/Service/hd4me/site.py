# 16.03.25


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

    search_url = "https://hd4me.net/lista-film"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client(headers={'user-agent': get_userAgent()}).get(search_url)
        response.raise_for_status()
        
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup instance
    soup = BeautifulSoup(response.text, "html.parser")

    # Collect data from new structure
    for li in soup.find_all("li"):

        a = li.find("a", href=True, id=True)
        if not a:
            continue

        href = a["href"].strip()
        title = a.get_text().split("–")[0].strip()
        id_attr = a.get("id")

        if query.lower() in title.lower():
            media_dict = {
                'id': id_attr,
                'name': title,
                'type': 'film',
                'url': 'https://hd4me.net' + href,
                'image': None
            }
            media_search_manager.add_media(media_dict)

    # Return the number of titles found
    return media_search_manager.get_length()