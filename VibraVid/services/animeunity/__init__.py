# 21.05.24

import urllib.parse

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film, download_series

indice = 1
_useFor = "Anime"
_db_upload = True
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def get_token(user_agent: str) -> dict:
    """
    Retrieve session cookies from the site.
    """
    with create_client(headers={"user-agent": user_agent}) as client:
        response = client.get(site_constants.FULL_URL)
    response.raise_for_status()
    all_cookies = {name: value for name, value in response.cookies.items()}

    return {k: urllib.parse.unquote(v) for k, v in all_cookies.items()}


def get_real_title(record: dict) -> str:
    """
    Return the most appropriate title from the record.
    """
    if record.get("title_eng"):
        return record["title_eng"]
    elif record.get("title"):
        return record["title"]
    else:
        return record.get("title_it", "")


def title_search(query: str) -> int:
    """
    Perform anime search on animeunity.so.
    """
    entries_manager.clear()
    table_show_manager.clear()
    seen_titles = set()
    user_agent = get_userAgent()
    data = get_token(user_agent)

    cookies = {
        "XSRF-TOKEN": data.get("XSRF-TOKEN", ""),
        "animeunity_session": data.get("animeunity_session", ""),
    }

    headers = {
        "origin": site_constants.FULL_URL,
        "referer": f"{site_constants.FULL_URL}/",
        "user-agent": user_agent,
        "x-xsrf-token": data.get("XSRF-TOKEN", ""),
    }

    # First call: /livesearch
    try:
        with create_client(headers=headers) as client:
            response1 = client.post(f"{site_constants.FULL_URL}/livesearch", cookies=cookies, data={"title": query})
        response1.raise_for_status()
        process_results(response1.json().get("records", []), seen_titles, entries_manager)

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Second call: /archivio/get-animes
    try:
        json_data = {
            "title": query,
            "type": False,
            "year": False,
            "order": False,
            "status": False,
            "genres": False,
            "offset": 0,
            "dubbed": False,
            "season": False,
        }
        with create_client(headers=headers) as client:
            response2 = client.post(f"{site_constants.FULL_URL}/archivio/get-animes", cookies=cookies, json=json_data)
        response2.raise_for_status()
        process_results(response2.json().get("records", []), seen_titles, entries_manager)

    except Exception as e:
        console.print(f"Site: {site_constants.SITE_NAME}, archivio search error: {e}")

    result_count = len(entries_manager)
    return result_count


def process_results(records: list, seen_titles: set, entries_manager: EntriesManager) -> None:
    """
    Add unique results to the media manager.
    """
    for dict_title in records:
        try:
            title_id = dict_title.get("id")
            if title_id in seen_titles:
                continue

            seen_titles.add(title_id)
            dict_title["name"] = get_real_title(dict_title)

            entries_manager.add(
                Entries(
                    id=title_id,
                    slug=dict_title.get("slug"),
                    name=dict_title.get("name"),
                    type=dict_title.get("type"),
                    status=dict_title.get("status"),
                    episodes_count=dict_title.get("episodes_count"),
                    image=dict_title.get("imageurl"),
                    mal_id=dict_title.get("mal_id"),
                    anilist_id=dict_title.get("anilist_id"),
                )
            )

        except Exception as e:
            console.print(f"Error parsing a title entry: {e}")


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
