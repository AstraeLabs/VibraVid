import re
import sys
import unicodedata
from urllib.parse import urljoin

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film

indice = 19
_useFor = "Film_Serie"
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()
BASE_URL = "https://www.la7.it"


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _program_slugs() -> dict[str, str]:
    with create_client(headers={"user-agent": get_userAgent()}) as client:
        response = client.get(f"{BASE_URL}/programmi")
        response.raise_for_status()
    result = {}
    for href, label in re.findall(r'href=["\']([^"\']+)["\'][^>]*>(.*?)</', response.text, re.I | re.S):
        if "/programma/" in href or href.count("/") >= 2:
            text = re.sub(r"<[^>]+>", " ", label)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                result[_normalize(text)] = href
    return result


def _page_links(path: str) -> list[tuple[str, str]]:
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
        if "/rivedila7/" not in href and "/video/" not in href:
            continue
        if not re.search(r"-\d+(?:[/?#]|$)", href):
            continue
        text = re.sub(r"<[^>]+>", " ", label)
        text = re.sub(r"\s+", " ", text).strip()
        links.append((text or href.rsplit("/", 1)[-1], href))
    return list(dict.fromkeys(links))


def _content_type() -> str:
    if not sys.stdin.isatty():
        return "all"
    try:
        return Prompt.ask(
            "Content type",
            choices=["episodes", "videos", "all"],
            default="all",
        )
    except EOFError:
        return "all"


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()
    query_key = _normalize(query)
    content_type = _content_type()
    links: list[tuple[str, str]] = []
    try:
        programs = _program_slugs()
        candidates = [
            slug for name, slug in programs.items() if query_key in name or name in query_key
        ]
        compact = re.sub(r"[^a-z0-9]+", "", query_key)
        candidates.extend(
            [
                f"/{compact}",
                f"/{query_key.replace(' ', '-')}",
                f"/{query_key.replace(' ', '')}",
            ]
        )
        for slug in dict.fromkeys(candidates):
            path = slug if slug.startswith("/") else urljoin(BASE_URL, slug).replace(BASE_URL, "")
            if content_type in ("episodes", "all"):
                links.extend(_page_links(path.rstrip("/") + "/rivedila7"))
            if content_type in ("videos", "all"):
                links.extend(_page_links(path.rstrip("/") + "/video"))
        if content_type in ("episodes", "all"):
            links.extend(_page_links("/rivedila7"))
        if content_type in ("videos", "all"):
            links.extend(_page_links("/la7teche"))
    except Exception as exc:
        console.print(f"[red]La7 search error: {exc}")
        return 0

    if content_type == "episodes":
        links = [(label, url) for label, url in links if "/rivedila7/" in url]
    elif content_type == "videos":
        links = [(label, url) for label, url in links if "/video/" in url]

    for label, url in links:
        if any(token in _normalize(label + " " + url) for token in query_key.split() if token):
            kind = "Episode" if "/rivedila7/" in url else "Video"
            entries_manager.add(Entries(name=f"[{kind}] {label}", type="movie", url=url, year="9999"))
    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
)
