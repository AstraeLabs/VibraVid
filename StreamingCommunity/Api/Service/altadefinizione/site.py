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

    search_url = f"{site_constants.FULL_URL}/?story={query}&do=search&subaction=search"
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
    boxes = soup.find("div", id="dle-content").find_all("div", class_="box")
    for i, box in enumerate(boxes):
        
        title_tag = box.find("h2", class_="titleFilm")
        a_tag = title_tag.find("a")
        title = a_tag.get_text(strip=True)
        url = a_tag.get("href")

        # Image
        img_tag = box.find("img", class_="attachment-loc-film")
        image_url = None
        if img_tag:
            img_src = img_tag.get("src")
            if img_src and img_src.startswith("/"):
                image_url = f"{site_constants.FULL_URL}{img_src}"
            else:
                image_url = img_src

        # Type
        tipo = "tv" if "/serie-tv/" in url else "film"

        media_dict = {
            'url': url,
            'name': title,
            'type': tipo,
            'image': image_url
        }
        media_search_manager.add_media(media_dict)

    # Return the number of titles found
    return media_search_manager.get_length()