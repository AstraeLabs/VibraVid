# 22.08.26
# By @sync-luca98

import re
import sys
import unicodedata
from urllib.parse import urljoin

from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film, download_series

indice = 15
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


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()
    query_key = _normalize(query)
    
    # Search both programs and films (equivalent to "all")
    try:
        # Construct likely slugs from query
        candidates = [
            f"/{query_key.replace(' ', '-')}",
            f"/{query_key.replace(' ', '')}",
        ]
        # Add compact version only if different
        compact = re.sub(r'[^a-z0-9]+', '', query_key)
        if compact != query_key.replace(' ', ''):
            candidates.append(f"/{compact}")
        
        for slug in dict.fromkeys(candidates):
            path = slug if slug.startswith("/") else urljoin(BASE_URL, slug).replace(BASE_URL, "")
            # Quick check if program page exists (has rivedila7 content)
            check_url = urljoin(BASE_URL, path.rstrip("/") + "/rivedila7")
            page_html = None
            with create_client(headers={"user-agent": get_userAgent()}) as client:
                try:
                    resp = client.get(check_url, timeout=5)
                    if resp.status_code != 200:
                        continue
                    # Check if page has episode links
                    if "rivedila7/" not in resp.text and "video/" not in resp.text:
                        continue
                    page_html = resp.text
                except Exception:
                    continue
            # Extract clean program name from slug
            program_name = path.strip("/").split("/")[-1].replace("-", " ").title()
            # Try to get latest episode date from page for display
            display_name = program_name
            if page_html:
                dates = re.findall(r'(\d{2}-\d{2}-\d{4})', page_html)
                if dates:
                    # Sort dates and pick the latest
                    from datetime import datetime
                    parsed_dates = [(datetime.strptime(d, "%d-%m-%Y"), d) for d in dates]
                    parsed_dates.sort(reverse=True)
                    display_name = f"{program_name} {parsed_dates[0][1]}"
            entries_manager.add(Entries(
                name=display_name,
                type="tv",
                url=check_url,
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
