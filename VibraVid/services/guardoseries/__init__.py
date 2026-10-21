# 20.10.26
# By @Syncs-Luca98

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .downloader import download_film, download_series
from .scrapper import search_guardoseries

indice = 21
_useFor = "Film_Serie"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles using Guardaserie's own search.

    Parameters:
        query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    for media_item in search_guardoseries(query):
        entries_manager.add(media_item)

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
