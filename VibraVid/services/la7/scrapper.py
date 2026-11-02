# 02.11.26

import concurrent.futures
import hashlib
import json
import logging
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import HTTPError
from rich.console import Console

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils import disk_cache
from VibraVid.utils.http_client import create_client, get_userAgent

from ._helpers import BASE_URL, _normalize

console = Console()
logger = logging.getLogger(__name__)

MAX_PAGES = 1000
CATEGORY_CACHE_TTL = 15 * 60
PAGE_WORKERS = 2


class GetSerieInfo:
    def __init__(self, path_id: str):
        self.base_url = BASE_URL
        self.path_id = path_id
        self.series_name = None
        self.year = None
        self.seasons_manager = SeasonManager()
        self._loaded_sections = set()

    @property
    def program_path(self) -> str:
        path = "/" + self.path_id.strip("/")
        if path.endswith("/video"):
            path = path[:-len("/video")]
        return path.rstrip("/")

    def _fetch_page(self, client, section_url: str, page_num: int) -> str:
        """Fetch one page from a La7 content section."""
        section_url = urljoin(self.base_url, section_url)
        if page_num > 1:
            section_url = f"{section_url}?page={page_num}"
        response = client.get(section_url, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_sections(self, html: str) -> list[tuple[str, str]]:
        """Discover video sections from the program navigation."""
        soup = BeautifulSoup(html, "html.parser")
        sections = []
        seen_urls = set()

        for link in soup.find_all("a", href=True):
            section_url = urljoin(self.base_url, link["href"])
            section_path = urlparse(section_url).path.rstrip("/")
            if not section_path.startswith(self.program_path + "/"):
                continue
            relative_path = section_path[len(self.program_path) + 1:]
            name = " ".join(link.stripped_strings).strip()
            if relative_path == "puntate" or relative_path == "video":
                pass
            elif relative_path.startswith("video/") and relative_path.count("/") == 1:
                pass
            elif (
                relative_path in {"clip", "clips"}
                or relative_path.startswith(("clip/", "clips/"))
                or _normalize(name) in {"clip", "clips"}
            ):
                pass
            else:
                continue
            if section_url in seen_urls:
                continue

            if not name:
                name = relative_path.rsplit("/", 1)[-1].replace("-", " ").title()
            sections.append((name, section_url))
            seen_urls.add(section_url)

        specific_video_sections = [
            section for section in sections
            if urlparse(section[1]).path.rstrip("/").startswith(
                self.program_path + "/video/"
            )
        ]
        if specific_video_sections:
            sections = [
                section for section in sections
                if urlparse(section[1]).path.rstrip("/") != self.program_path + "/video"
            ]

        return sections

    def _extract_episodes(self, html: str, section_url: str) -> list[tuple[str, str]]:
        """Extract video cards using the section's structured data or card links."""
        soup = BeautifulSoup(html, "html.parser")
        episodes = []
        seen_urls = set()
        program_prefix = self.program_path + "/"

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or script.get_text())
            except json.JSONDecodeError:
                continue
            nodes = data.get("@graph", []) if isinstance(data, dict) else data
            if not isinstance(nodes, list):
                nodes = [nodes]
            for node in nodes:
                if not isinstance(node, dict) or node.get("@type") != "ItemList":
                    continue
                for entry in node.get("itemListElement", []):
                    item = entry.get("item", {}) if isinstance(entry, dict) else {}
                    if not isinstance(item, dict):
                        continue
                    episode_url = item.get("url") or item.get("@id", "").split("#", 1)[0]
                    episode_path = urlparse(urljoin(self.base_url, episode_url)).path
                    if not episode_path.startswith(program_prefix) or episode_path in seen_urls:
                        continue
                    name = item.get("name")
                    if name:
                        episodes.append((name.strip(), urljoin(self.base_url, episode_url)))
                        seen_urls.add(episode_path)

        if episodes:
            return episodes

        section_path = urlparse(section_url).path.rstrip("/")
        is_episodes_section = section_path == self.program_path + "/puntate"
        for link in soup.find_all("a", href=True):
            image = link.find("img", alt=True)
            if not image:
                continue
            episode_url = urljoin(self.base_url, link["href"])
            episode_path = urlparse(episode_url).path
            if not episode_path.startswith(program_prefix) or episode_path in seen_urls:
                continue
            if is_episodes_section:
                valid_route = any(
                    episode_path.startswith(f"{self.program_path}/{route}/")
                    for route in ("puntate", "rivedila7")
                )
            else:
                valid_route = episode_path != section_path
            if not valid_route:
                continue
            name = image.get("alt", "").strip() or " ".join(link.stripped_strings)
            if name:
                episodes.append((name, episode_url))
                seen_urls.add(episode_path)

        return episodes

    def _has_next_page(self, html: str, section_url: str, page_num: int) -> bool:
        """Check whether the current section links to another page."""
        section_path = urlparse(section_url).path.rstrip("/")
        next_page = str(page_num + 1)
        soup = BeautifulSoup(html, "html.parser")

        for link in soup.find_all("a", href=True):
            page_url = urlparse(urljoin(self.base_url, link["href"]))
            if (
                page_url.path.rstrip("/") == section_path
                and parse_qs(page_url.query).get("page") == [next_page]
            ):
                return True

        return False

    def _page_numbers(self, html: str, section_url: str) -> list[int]:
        """Return page numbers linked by the section's pagination controls."""
        section_path = urlparse(section_url).path.rstrip("/")
        pages = {1}
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.find_all("a", href=True):
            page_url = urlparse(urljoin(self.base_url, link["href"]))
            if page_url.path.rstrip("/") != section_path:
                continue
            page_values = parse_qs(page_url.query).get("page", [])
            if len(page_values) != 1 or not page_values[0].isdigit():
                continue
            page_number = int(page_values[0])
            if 1 < page_number <= MAX_PAGES:
                pages.add(page_number)
        return sorted(pages)

    def _fetch_additional_page(self, section_url: str, page_num: int) -> str:
        """Fetch a pagination page with an independent HTTP client."""
        for attempt in range(2):
            try:
                with create_client(headers={"user-agent": get_userAgent()}) as client:
                    return self._fetch_page(client, section_url, page_num)
            except HTTPError as exc:
                if (
                    attempt == 1
                    or exc.response is None
                    or exc.response.status_code != 429
                ):
                    raise
                retry_after = exc.response.headers.get("Retry-After", "1")
                try:
                    delay = min(float(retry_after), 5.0)
                except ValueError:
                    delay = 1.0
                logger.warning(
                    "La7 rate-limited page %s; retrying after %.1f seconds",
                    page_num,
                    delay,
                )
                time.sleep(delay)

    def _category_cache_name(self, category_url: str) -> str:
        key = hashlib.sha256(category_url.encode("utf-8")).hexdigest()
        return f"category_{key}"

    def _add_cached_episodes(self, season: Season, cached: dict) -> bool:
        entries = cached.get("episodes")
        if not isinstance(entries, list):
            return False
        if any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("url"), str)
            for entry in entries
        ):
            return False
        for index, entry in enumerate(entries, 1):
            season.episodes.add(
                Episode(
                    id=str(index),
                    number=index,
                    name=entry["name"],
                    url=entry["url"],
                )
            )
        return True

    def _extract_series_name(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        heading = soup.find("h1")
        if heading:
            return " ".join(heading.stripped_strings)
        meta = soup.find("meta", attrs={"property": "og:title"})
        if meta and meta.get("content"):
            return meta["content"].split("|")[0].strip()
        return None

    def collect_info_title(self) -> None:
        """Discover the program's video categories."""
        try:
            with create_client(headers={"user-agent": get_userAgent()}) as client:
                program_url = urljoin(self.base_url, self.program_path)
                response = client.get(program_url, timeout=30)
                response.raise_for_status()
                program_html = response.text
                self.series_name = self._extract_series_name(program_html)
                sections = self._extract_sections(program_html)

                if not sections:
                    sections = [("Puntate", self.program_path + "/puntate")]

                for category_number, (category_name, category_url) in enumerate(sections, 1):
                    self.seasons_manager.add(
                        Season(
                            number=category_number,
                            name=category_name,
                            type="Category",
                            slug=category_url,
                        )
                    )
        except Exception as e:
            console.print(f"[red]Error collecting series info: {e}")

    def _collect_category_episodes(
        self, client, season: Season, cache_name: str
    ) -> None:
        category_url = season.slug
        seen_urls = {episode.url for episode in season.episodes.episodes}

        try:
            first_page = self._fetch_page(client, category_url, 1)
            page_numbers = self._page_numbers(first_page, category_url)
            page_html_by_number = {1: first_page}

            if len(page_numbers) > 2:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=PAGE_WORKERS
                ) as executor:
                    futures = {
                        executor.submit(
                            self._fetch_additional_page, category_url, page_num
                        ): page_num
                        for page_num in range(2, page_numbers[-1] + 1)
                    }
                    for future in concurrent.futures.as_completed(futures):
                        page_html_by_number[futures[future]] = future.result()
            elif self._has_next_page(first_page, category_url, 1):
                page_num = 1
                page_html = first_page
                while self._has_next_page(page_html, category_url, page_num):
                    page_num += 1
                    if page_num > MAX_PAGES:
                        console.print(
                            f"[yellow]La7 category '{season.name}' reached the "
                            f"{MAX_PAGES}-page limit"
                        )
                        break
                    page_html = self._fetch_page(client, category_url, page_num)
                    page_html_by_number[page_num] = page_html

            for page_num in sorted(page_html_by_number):
                page_html = page_html_by_number[page_num]
                episodes = self._extract_episodes(page_html, category_url)
                for name, url in episodes:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    episode_number = len(season.episodes) + 1
                    season.episodes.add(
                        Episode(
                            id=str(episode_number),
                            number=episode_number,
                            name=name,
                            url=url,
                        )
                    )

            disk_cache.save(
                "la7",
                cache_name,
                {
                    "episodes": [
                        {"name": episode.name, "url": episode.url}
                        for episode in season.episodes.episodes
                    ]
                },
                ttl=CATEGORY_CACHE_TTL,
            )
            self._loaded_sections.add(season.number)
        except Exception as exc:
            console.print(f"[red]Error collecting La7 category '{season.name}': {exc}")

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Load and return all videos for a selected content category."""
        season = self.seasons_manager.get_season_by_number(season_number)

        if not season:
            console.print(f"[red]La7 category {season_number} not found")
            return []

        if season.number not in self._loaded_sections:
            cache_name = self._category_cache_name(season.slug)
            cached = disk_cache.load_fresh("la7", cache_name)
            if cached and self._add_cached_episodes(season, cached):
                self._loaded_sections.add(season.number)
            else:
                with create_client(headers={"user-agent": get_userAgent()}) as client:
                    self._collect_category_episodes(client, season, cache_name)

        return season.episodes.episodes


def get_films(query: str) -> list[tuple[str, str]]:
    """Search the current La7 film catalog using its structured movie list."""
    query_key = _normalize(query)
    with create_client(headers={"user-agent": get_userAgent()}) as client:
        response = client.get(urljoin(BASE_URL, "/film"), timeout=30)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    films = []
    seen_urls = set()

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", []) if isinstance(data, dict) else data
        if not isinstance(nodes, list):
            nodes = [nodes]
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "ItemList":
                continue
            for entry in node.get("itemListElement", []):
                item = entry.get("item", {}) if isinstance(entry, dict) else {}
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type")
                if item_type != "Movie" and not (
                    isinstance(item_type, list) and "Movie" in item_type
                ):
                    continue
                title = item.get("name")
                film_url = item.get("url") or item.get("@id", "").split("#", 1)[0]
                if not title or not film_url:
                    continue
                film_url = urljoin(BASE_URL, film_url)
                film_path = urlparse(film_url).path
                if not film_path.startswith("/film/") or film_url in seen_urls:
                    continue
                if query_key and not any(
                    token in _normalize(title + " " + film_url)
                    for token in query_key.split()
                    if token
                ):
                    continue
                films.append((title.strip(), film_url))
                seen_urls.add(film_url)

    if films:
        return films

    for link in soup.find_all("a", href=True):
        image = link.find("img", alt=True)
        film_url = urljoin(BASE_URL, link["href"])
        film_path = urlparse(film_url).path
        if not image or not film_path.startswith("/film/") or film_url in seen_urls:
            continue
        if not re.search(r"-\d+$", film_path):
            continue
        title = image.get("alt", "").strip()
        if not title:
            continue
        if query_key and not any(
            token in _normalize(title + " " + film_url)
            for token in query_key.split()
            if token
        ):
            continue
        films.append((title, film_url))
        seen_urls.add(film_url)

    return films
