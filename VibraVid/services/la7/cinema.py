# 11.10.26
# LA7 Cinema section - PlayReady DRM

import json
import re
import base64
from urllib.parse import urljoin

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader_cinema import download_film_cinema

indice = 18
_useFor = "Film"
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()
BASE_URL = "https://www.la7.it"
CINEMA_BASE = "/la7-cinema-tutti-i-film"


def _normalize(value: str) -> str:
    value = json.loads('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"')
    value = re.sub(r'[^\x00-\x7F]+', '', value)
    return value.lower()


def _page_links_cinema(path: str) -> list[tuple[str, str]]:
    with create_client(headers={"user-agent": get_userAgent()}) as client:
        try:
            response = client.get(urljoin(BASE_URL, path))
            response.raise_for_status()
        except HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []
            raise
    links = []
    for href, label in re.findall(r'href=["\']([^"\']+)["\'][^>]*>(.*?)</', response.text, re.I | re.S):
        href = urljoin(BASE_URL, href)
        if "/la7-cinema-tutti-i-film/rivedila7/" not in href:
            continue
        if not re.search(r"-\d+(?:[/?#]|$)", href):
            continue
        text = re.sub(r"<[^>]+>", " ", label)
        text = re.sub(r"\s+", " ", text).strip()
        links.append((text or href.rsplit("/", 1)[-1], href))
    return list(dict.fromkeys(links))


def _content_type_cinema() -> str:
    if not __import__('sys').stdin.isatty():
        return "all"
    try:
        return Prompt.ask(
            "Content type",
            choices=["episodes", "all"],
            default="all",
        )
    except EOFError:
        return "all"


def title_search_cinema(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()
    query_key = _normalize(query)
    links: list[tuple[str, str]] = []
    try:
        links.extend(_page_links_cinema(CINEMA_BASE + "/rivedila7"))
    except Exception as exc:
        console.print(f"[red]La7 Cinema search error: {exc}")
        return 0

    links = [(label, url) for label, url in links if "/la7-cinema-tutti-i-film/rivedila7/" in url]

    for label, url in links:
        if any(token in _normalize(label + " " + url) for token in query_key.split() if token):
            entries_manager.add(Entries(name=label, type="movie", url=url, year="9999"))
    return len(entries_manager)


search_cinema, process_search_result_cinema = make_search_entrypoints(
    title_search=title_search_cinema,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film_cinema,
)