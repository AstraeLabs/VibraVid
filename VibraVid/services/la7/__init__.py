# 02.11.26

import concurrent.futures
from urllib.parse import urljoin

from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from ._helpers import (
    BASE_URL,
    _gql_request,
    _normalize,
)
from .downloader import download_film, download_series
from .scrapper import get_films

indice = 10
_useFor = "Film_Serie"
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def _search_programs(query_key: str) -> list[Entries]:
    """Search TV programs via GraphQL allPrograms."""
    found = []
    try:
        gql_data = _gql_request("allPrograms", {})
        if gql_data and "data" in gql_data:
            results = (
                gql_data["data"].get("graphqlTuttiProgrammi", {}).get("results", [])
            )
            for item in results:
                title = item.get("titolo", "")
                pathname = item.get("pathname", "")
                if not title or not pathname:
                    continue
                if any(
                    token in _normalize(title + " " + pathname)
                    for token in query_key.split()
                    if token
                ):
                    found.append(
                        Entries(
                            name=title,
                            type="tv",
                            url=urljoin(BASE_URL, pathname.rstrip("/") + "/video"),
                            year="9999",
                            path_id=pathname,
                        )
                    )
    except Exception as exc:
        console.print(f"[red]La7 programs search error: {exc}")
    return found


def _search_films(query_key: str) -> list[Entries]:
    try:
        return [
            Entries(name=title, type="movie", url=url, year="9999")
            for title, url in get_films(query_key)
        ]
    except Exception as exc:
        console.print(f"[red]La7 cinema search error: {exc}")
        return []


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()
    query_key = _normalize(query)

    # Run program and film searches in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        futures.append(executor.submit(_search_programs, query_key))
        futures.append(executor.submit(_search_films, query_key))

        for future in concurrent.futures.as_completed(futures):
            for entry in future.result():
                entries_manager.add(entry)

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
