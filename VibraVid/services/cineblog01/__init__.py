# 27.09.26

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search
from VibraVid.utils import TVShowManager

from .client import fetch_search_page, get_base_url
from .downloader import download_film
from .scrapper import parse_search_results

indice = 98
_useFor = "Film_Serie"
_hide = False

msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """Search CB01 DataLife Engine results and retain every matching card."""
    entries_manager.clear()
    table_show_manager.clear()

    try:
        html, final_url = fetch_search_page(query)
    except Exception as error:
        console.print(f"[red]CB01 search request failed: {error}")
        return 0

    for index, result in enumerate(parse_search_results(html, final_url), 1):
        entries_manager.add(
            Entries(
                id=index,
                name=result.title,
                type="film",
                url=result.url,
                image=result.image,
                year=result.year or "9999",
                slug="",
                provider_language="it",
                raw_title=result.raw_title,
            )
        )

    return len(entries_manager)


def process_search_result(select_title, selections=None, scrape_serie=None):
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=None,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def search(
    string_to_search: str = None,
    get_onlyDatabase: bool = False,
    direct_item: dict = None,
    selections: dict = None,
    scrape_serie=None,
):
    return base_search(
        title_search_func=title_search,
        process_result_func=process_search_result,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        site_name=site_constants.SITE_NAME,
        string_to_search=string_to_search,
        get_onlyDatabase=get_onlyDatabase,
        direct_item=direct_item,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def _resolve_url_to_item(url: str) -> dict | None:
    """Allow the GUI generic adapter to accept a direct CB01 detail URL."""
    base_url = get_base_url()
    if not url.startswith(base_url):
        return None

    return {
        "name": url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title(),
        "type": "film",
        "url": url,
        "year": None,
        "provider_language": "it",
    }
