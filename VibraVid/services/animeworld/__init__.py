# 21.03.25

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_headers

from .downloader import download_film, download_series

indice = 5
_useFor = "Anime"
_db_upload = True
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Function to perform an anime search using a provided title.

    Parameters:
        query (str): The query to search for.

    Returns:
        - int: A number containing the length of media search manager.
    """
    entries_manager.clear()
    table_show_manager.clear()

    search_url = f"{site_constants.FULL_URL}/search?keyword={query}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    # Make the GET request
    try:
        with create_client(headers=get_headers()) as client:
            response = client.get(search_url)
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup istance
    soup = BeautifulSoup(response.text, "html.parser")

    # Collect data from soup
    for element in soup.find_all("a", class_="poster"):
        try:
            title = element.find("img").get("alt")
            url = f"{site_constants.FULL_URL}{element.get('href')}"
            status_div = element.find("div", class_="status")
            is_dubbed = False
            anime_type = "TV"

            if status_div:
                if status_div.find("div", class_="dub"):
                    is_dubbed = True

                if status_div.find("div", class_="movie"):
                    anime_type = "Movie"
                elif status_div.find("div", class_="ona"):
                    anime_type = "ONA"

                entries_manager.add(
                    Entries(name=title, type=anime_type, DUB=is_dubbed, url=url, image=element.find("img").get("src"))
                )

        except Exception as e:
            console.print(f"Error parsing a film entry: {e}")

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
