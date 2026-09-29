# 29.09.26

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .client import get_player_url
from .downloader import download_film, download_series

indice = 16
_useFor = "Film_Serie"

msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()
_TMDB_IMG = "https://image.tmdb.org/t/p/w500"


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()

    if not tmdb_client.api_key:
        console.print(
            "\n[red]This site requires a TMDB API key to search.[white] See "
            "https://astraelabs.github.io/VibraVid/configuration/#tmdb-api-key for how to set it."
        )
        return 0

    for movie in tmdb_client.search_movies(query):
        tmdb_id = movie["id"]
        poster = (
            f"{_TMDB_IMG}{movie['poster_path']}"
            if movie.get("poster_path")
            else None
        )
        year = (movie.get("release_date") or "")[:4] or None
        entries_manager.add(
            Entries(
                id=tmdb_id,
                tmdb_id=tmdb_id,
                name=movie.get("title", ""),
                type="film",
                slug="movie",
                url=get_player_url(tmdb_id, "movie"),
                image=poster,
                year=year,
            )
        )

    for show in tmdb_client.search_series(query):
        tmdb_id = show["id"]
        poster = (
            f"{_TMDB_IMG}{show['poster_path']}"
            if show.get("poster_path")
            else None
        )
        year = (show.get("first_air_date") or "")[:4] or None
        entries_manager.add(
            Entries(
                id=tmdb_id,
                tmdb_id=tmdb_id,
                name=show.get("name", ""),
                type="tv",
                slug="tv",
                url=None,
                image=poster,
                year=year,
            )
        )

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
