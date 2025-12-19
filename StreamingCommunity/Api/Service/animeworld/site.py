# 21.03.25

import logging


# External libraries
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_headers
from StreamingCommunity.Api.Template import site_constants, MediaManager
from StreamingCommunity.Util.table import TVShowManager



# Variable
console = Console()
media_search_manager = MediaManager()
table_show_manager = TVShowManager()


def get_session_and_csrf() -> dict:
    """
    Get the session ID and CSRF token from the website's cookies and HTML meta data.
    """
    # Send an initial GET request to the website
    client = create_client(headers=get_headers())
    response = client.get(site_constants.FULL_URL)

    # Extract the sessionId from the cookies
    session_id = response.cookies.get('sessionId')
    logging.info(f"Session ID: {session_id}")

    # Use BeautifulSoup to parse the HTML and extract the CSRF-Token
    soup = BeautifulSoup(response.text, 'html.parser')

    # Try to find the CSRF token in a meta tag or hidden input
    csrf_token = None
    meta_tag = soup.find('meta', {'name': 'csrf-token'})
    if meta_tag:
        csrf_token = meta_tag.get('content')

    # If it's not in the meta tag, check for hidden input fields
    if not csrf_token:
        input_tag = soup.find('input', {'name': '_csrf'})
        if input_tag:
            csrf_token = input_tag.get('value')

    logging.info(f"CSRF Token: {csrf_token}")
    return session_id, csrf_token

def title_search(query: str) -> int:
    """
    Function to perform an anime search using a provided title.

    Parameters:
        - query (str): The query to search for.

    Returns:
        - int: A number containing the length of media search manager.
    """
    search_url = f"{site_constants.FULL_URL}/search?keyword={query}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    # Make the GET request
    try:
        response = create_client(headers=get_headers()).get(search_url)
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup istance
    soup = BeautifulSoup(response.text, 'html.parser')

    # Collect data from soup
    for element in soup.find_all('a', class_='poster'):
        try:
            title = element.find('img').get('alt')
            url = f"{site_constants.FULL_URL}{element.get('href')}"
            status_div = element.find('div', class_='status')
            is_dubbed = False
            anime_type = 'TV'

            if status_div:
                if status_div.find('div', class_='dub'):
                    is_dubbed = True
                
                if status_div.find('div', class_='movie'):
                    anime_type = 'Movie'
                elif status_div.find('div', class_='ona'):
                    anime_type = 'ONA'

                media_search_manager.add_media({
                    'name': title,
                    'type': anime_type,
                    'DUB': is_dubbed,
                    'url': url,
                    'image': element.find('img').get('src')
                })

        except Exception as e:
            print(f"Error parsing a film entry: {e}")

    # Return the length of media search manager
    return media_search_manager.get_length()
