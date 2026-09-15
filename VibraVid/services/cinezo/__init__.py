# 17.04.26
# by @nu00

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .downloader import download_film, download_series

indice = 15
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

    for m in tmdb_client.search_movies(query):
        poster = f"{_TMDB_IMG}{m['poster_path']}" if m.get("poster_path") else None
        year = (m.get("release_date") or "")[:4] or None
        entries_manager.add(
            Entries(
                id=m["id"],
                name=m.get("title", ""),
                type="film",
                slug="movie",
                url=f"https://www.cinezo.net/watch/movie/{m['id']}",
                image=poster,
                year=year,
            )
        )

    for s in tmdb_client.search_series(query):
        poster = f"{_TMDB_IMG}{s['poster_path']}" if s.get("poster_path") else None
        year = (s.get("first_air_date") or "")[:4] or None
        entries_manager.add(
            Entries(
                id=s["id"],
                name=s.get("name", ""),
                type="tv",
                slug="tv",
                url=f"https://www.cinezo.net/watch/tv/{s['id']}",
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
