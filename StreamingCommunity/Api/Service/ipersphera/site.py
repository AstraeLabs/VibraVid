# 16.12.25


# External libraries
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client_curl, get_userAgent
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

    search_url = f"https://www.ipersphera.com/?s={query}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client_curl(headers={'user-agent': get_userAgent()}).get(search_url)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup instance
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("div", id="content")

    # Track seen URLs to avoid duplicates
    seen_urls = set()

    for i, element in enumerate(table.find_all("div")):

        # Extract title and URL from h2.posttitle > a
        title_element = element.find("h2", class_="posttitle")
        if not title_element:
            continue
            
        link = title_element.find("a")
        if not link:
            continue
            
        title = link.text.strip()
        url = link.get('href', '')
        
        # Skip duplicates
        if url in seen_urls:
            continue
        seen_urls.add(url)
        
        # Determine type based on categories
        categs_div = element.find("div", class_="categs")
        tipo = "film"
        if categs_div:
            categs_text = categs_div.get_text().lower()
            if "serie" in categs_text or "tv" in categs_text:
                tipo = "tv"

        media_dict = {
            'url': url,
            'name': title,
            'type': tipo
        }
        media_search_manager.add_media(media_dict)

    # Return the number of titles found
    return media_search_manager.get_length()