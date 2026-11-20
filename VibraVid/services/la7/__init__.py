# 22.08.26
# By @sync-luca98

import re
import sys
import unicodedata
from urllib.parse import urljoin, urlparse

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film, download_series

indice = 10
_useFor = "Film_Serie"
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()
BASE_URL = "https://www.la7.it"
CINEMA_BASE = "/la7-cinema-tutti-i-film"


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


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
        # Clean label: remove date/ID suffix, replace hyphens with spaces, title case
        if not text or text == href.rsplit("/", 1)[-1]:
            text = href.rsplit("/", 1)[-1]
            # Remove trailing date/ID pattern like -07-10-2026-660714
            text = re.sub(r'-\d{2}-\d{2}-\d{4}-\d+$', '', text)
            text = text.replace('-', ' ').title()
        links.append((text, href))
    return list(dict.fromkeys(links))


def _program_episode_links(path: str) -> list[tuple[str, str]]:
    """Collect each episode from the paginated LA7 program archive."""
    archive_path = path.rstrip("/") + "/puntate"
    with create_client(headers={"user-agent": get_userAgent()}) as client:
        response = client.get(urljoin(BASE_URL, archive_path), timeout=10)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        first_page = response.text

        page_numbers = [
            int(page) for page in re.findall(
                r'href=["\']' + re.escape(archive_path) + r'\?page=(\d+)["\']',
                first_page,
            )
        ]
        last_page = min(max(page_numbers, default=1), 100)
        pages = [first_page]
        for number in range(2, last_page + 1):
            response = client.get(
                urljoin(BASE_URL, f"{archive_path}?page={number}"), timeout=10
            )
            response.raise_for_status()
            pages.append(response.text)

    episodes = {}
    prefixes = (path.rstrip("/") + "/rivedila7/", path.rstrip("/") + "/puntate/")
    for html in pages:
        for href in re.findall(r'href=["\']([^"\']+)["\']', html):
            url = urljoin(BASE_URL, href).split("?", 1)[0]
            # LA7 redirects to la7play.it; normalize back to LA7.
            url_path = urlparse(url).path
            if not url_path.startswith(prefixes):
                continue
            slug = url_path.rsplit("/", 1)[-1]
            if not re.search(r"-\d+$", slug):
                continue
            title = re.sub(r"-\d{1,2}-\d{1,2}-\d{4}-\d+$", "", slug)
            program_slug = path.strip("/").split("/")[-1]
            if title.startswith(program_slug + "-"):
                title = title[len(program_slug) + 1:]
            display = title.replace("-", " ").strip().title() or slug
            date = re.search(r"(\d{1,2}-\d{1,2}-\d{4})-\d+$", slug)
            if date:
                display += f" ({date.group(1)})"
            episodes.setdefault(BASE_URL + url_path, display)
    return [(name, url) for url, name in episodes.items()]


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()
    query_key = _normalize(query)

    try:
        candidates = [
            f"/{query_key.replace(' ', '-')}",
            f"/{query_key.replace(' ', '')}",
        ]
        compact = re.sub(r"[^a-z0-9]+", "", query_key)
        if compact != query_key.replace(" ", ""):
            candidates.append(f"/{compact}")

        seen_urls = set()
        for path in dict.fromkeys(candidates):
            try:
                episodes = _program_episode_links(path)
            except Exception as exc:
                console.print(f"[yellow]La7 archive search failed for {path}: {exc}")
                continue
            for name, url in episodes:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                entries_manager.add(Entries(
                    name=name,
                    type="tv",
                    url=url,
                    year="9999",
                    path_id=path,
                ))
    except Exception as exc:
        console.print(f"[red]La7 programs search error: {exc}")

    # Search films (LA7 Cinema)
    try:
        links = _page_links_cinema(CINEMA_BASE + "/rivedila7")
        for label, url in links:
            if any(token in _normalize(label + " " + url) for token in query_key.split() if token):
                entries_manager.add(Entries(
                    name=label,
                    type="movie",
                    url=url,
                    year="9999",
                ))
    except Exception as exc:
        console.print(f"[red]La7 cinema search error: {exc}")
    
    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
