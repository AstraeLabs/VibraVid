# 26.05.24

from urllib.parse import quote_plus

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.provider.tmdb import tmdb
from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .downloader import download_film, download_series

indice = 2
_useFor = "Film_Serie"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles based on a search query using TMDB.

    Parameters:
        query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not tmdb.api_key:
        console.print(
            "\n[red]This site requires a TMDB API key to search.[white] See "
            "https://astraelabs.github.io/VibraVid/configuration/#tmdb-api-key for how to set it."
        )
        return 0

    # Search on TMDB
    movies = tmdb.search_movies(quote_plus(query))

    for movie in movies:
        year = None
        if movie.get("release_date"):
            try:
                year = movie["release_date"].split("-")[0]
            except Ellipsis:
                year = None

        media_item = Entries(
            id=movie["id"],
            name=movie["title"],
            slug="",
            path_id=None,
            type="film",
            url="",  # Not needed for download
            image=f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get("poster_path") else None,
            year=year,
        )

        entries_manager.add(media_item)

    series = tmdb.search_series(quote_plus(query))
    for show in series:
        year = None
        if show.get("first_air_date"):
            try:
                year = show["first_air_date"].split("-")[0]
            except Ellipsis:
                year = None

        media_item = Entries(
            id=show["id"],
            name=show["name"],
            slug="",
            path_id=None,
            type="tv",
            url="",
            image=f"https://image.tmdb.org/t/p/w500{show.get('poster_path')}" if show.get("poster_path") else None,
            year=year,
        )

        entries_manager.add(media_item)

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
