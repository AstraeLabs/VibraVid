# 20.10.26
# By @Syncs-Luca98

import difflib
import logging
import re
import threading
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
from rich.console import Console

from VibraVid.player.loadm import LoadmSource
from VibraVid.services._base.object import Entries, Episode, Season, SeasonManager
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)
console = Console()

_DEFAULT_GUARDOSERIES_BASE = "https://guardoseries.click"
_SEARCH_MAX_PAGES = 1
_URL_CACHE: dict[str, str | None] = {}
_UQLOAD_PAGE_RE = re.compile(r'https?://(?:www\.)?uqload\.(?:vc|io)/([a-zA-Z0-9]+)\.html')
_EPISODE_LINK_RE = re.compile(r'href="([^"]*/episodio/[^"]*?)/?"')
_pw_browser = None
_pw_lock = threading.Lock()


def _base_url() -> str:
    """Resolve the current Guardaserie domain from the shared domain config."""
    try:
        url = config_manager.domain.get("guardoseries", "full_url", default=None)
        if url:
            return str(url).rstrip("/")
    except Exception:
        pass
    return _DEFAULT_GUARDOSERIES_BASE


def _get_pw_browser():
    global _pw_browser
    with _pw_lock:
        if _pw_browser is None and PLAYWRIGHT_AVAILABLE:
            try:
                _pw = sync_playwright().start()
                _pw_browser = _pw.chromium.launch(headless=True)
            except Exception as error:
                logger.error(f"[Guardaserie] Failed to launch Playwright: {error}")
        return _pw_browser


def _fetch_html(url: str) -> str | None:
    """Fetch HTML: httpx first (fast), cached per URL."""
    if url in _URL_CACHE:
        return _URL_CACHE[url]
    html: str | None = None
    try:
        with create_client() as client:
            resp = client.get(url, timeout=30)
        resp.raise_for_status()
        if resp.text and len(resp.text) > 1000:
            html = resp.text
    except Exception:
        pass
    _URL_CACHE[url] = html
    return html


def _server_iframes(html: str) -> list[tuple[str, str]]:
    """Return list of (server, url) for known video iframes on a guardoseries page."""
    if not html:
        return []
    servers = []
    seen = set()
    soup = BeautifulSoup(html, "html.parser")
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("data-src") or iframe.get("src") or "").strip()
        if not src or src.startswith("data:") or src in seen:
            continue
        seen.add(src)
        low = src.lower()
        if "uqload" in low:
            servers.append(("uqload", src))
        elif "loadm" in low or "mload" in low:
            servers.append(("loadm", src))
        elif "vixsrc" in low:
            servers.append(("vixsrc", src))
        elif "dropload" in low:
            servers.append(("dropload", src))
    return servers


def _uqload_page_from_iframe(src: str) -> str | None:
    m = _UQLOAD_PAGE_RE.search(src)
    if m:
        return f"https://uqload.vc/{m.group(1)}.html"
    m3 = re.search(r'uqload\.(?:vc|io)/e/([a-zA-Z0-9]+)', src, re.IGNORECASE)
    if m3:
        return f"https://uqload.vc/{m3.group(1)}.html"
    if "uqload" in src and src.startswith("http"):
        return src
    return None


def _resolve_stream_via_browser(page_url: str) -> str | None:
    """Use the cached Playwright browser to intercept the real .m3u8 stream URL."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    browser = _get_pw_browser()
    if not browser:
        return None
    context = None
    page = None
    found = []
    try:
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def on_request(req):
            try:
                u = req.url
                if ".m3u8" in u.lower() and u not in found:
                    found.append(u)
            except Exception:
                pass

        page.on("request", on_request)
        page.goto(page_url, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        if not found:
            for selector in ["button.play", ".play-button", "#play", "[class*='play']", "button:has-text('Guarda')", "button:has-text('Play')"]:
                try:
                    els = page.query_selector_all(selector)
                    for el in els[:3]:
                        try:
                            el.click(timeout=1000)
                            page.wait_for_timeout(1500)
                        except Exception:
                            continue
                except Exception:
                    continue
            page.wait_for_timeout(3000)

        return found[0] if found else None
    except Exception as error:
        logger.error(f"[Guardaserie] Playwright stream resolve failed for {page_url}: {error}")
        return None
    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass


def resolve_uqload_stream(uqload_page_url: str) -> str | None:
    """Given a Uqload video page URL, return the direct .m3u8 stream URL."""
    m = _UQLOAD_PAGE_RE.search(uqload_page_url)
    if not m:
        return None
    return _resolve_stream_via_browser(uqload_page_url)


def resolve_stream_with_fallback(server_iframes: list[tuple[str, str]], referer: str = "") -> str | None:
    """Given a list of (server, iframe_url), try to resolve a direct .m3u8 stream URL."""
    available = [server for server, _ in server_iframes]
    logger.info(f"[Guardaserie] Servers found: {', '.join(available) or 'none'}")
    console.print(f"[dim]Servers found:[/dim] [cyan]{', '.join(available) or 'none'}[/cyan]")

    for server, src in server_iframes:
        if server != "uqload":
            continue
        logger.info("[Guardaserie] Try server: uqload")
        console.print("[yellow]Try server:[/yellow] [cyan]uqload[/cyan]")
        page = _uqload_page_from_iframe(src)
        if page:
            stream = _resolve_stream_via_browser(page)
            if stream:
                logger.info("[Guardaserie] Stream found on server: uqload")
                console.print("[green]Stream found on server:[/green] [cyan]uqload[/cyan]")
                return stream
        logger.warning("[Guardaserie] Server uqload failed")
        console.print("[red]Server uqload failed[/red]")

    for server, src in server_iframes:
        if server != "loadm":
            continue
        logger.info("[Guardaserie] Try server: loadm")
        console.print("[yellow]Try server:[/yellow] [cyan]loadm[/cyan]")
        stream, _headers = LoadmSource(src, referer=referer).get_stream()
        if stream:
            logger.info("[Guardaserie] Stream found on server: loadm")
            console.print("[green]Stream found on server:[/green] [cyan]loadm[/cyan]")
            return stream
        logger.warning("[Guardaserie] Server loadm failed")
        console.print("[red]Server loadm failed[/red]")

    logger.error("[Guardaserie] No working server found")
    console.print("[red]No working server found[/red]")
    return None


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _content_type_from_url(url: str) -> str:
    return "tv" if "/serie/" in url else "film"


def _image_from_style(style: str) -> str | None:
    match = re.search(r"url\((['\"]?)(.*?)\1\)", style or "")
    if match:
        return match.group(2).strip() or None
    return None


def _year_from_card(card) -> str | None:
    for link in card.select("a[href*='/release-year/']"):
        text = link.get_text(strip=True)
        if re.fullmatch(r"\d{4}", text):
            return text
    return None


def _make_entry(name: str, href: str, image: str = None, year: str = None, item_id=None) -> Entries | None:
    """Build an Entries item from the fields scraped off a search result card."""
    name = (name or "").strip()
    href = (href or "").strip()
    if not name or not href:
        return None

    url = urljoin(f"{_base_url()}/", href)
    slug = _slug_from_url(url)
    if not slug:
        return None

    return Entries(
        id=item_id,
        name=name,
        slug=slug,
        path_id=None,
        type=_content_type_from_url(url),
        url=url,
        image=image,
        year=year,
    )


def _parse_live_search(html: str | None) -> list[Entries]:
    """Parse the SearchWP live-search dropdown (a.thumb + a.ss-title)."""
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries: list[Entries] = []
    for item in soup.select("li"):
        title_link = item.select_one("a.ss-title")
        if not title_link:
            continue

        thumb = item.select_one("a.thumb")
        href = (title_link.get("href") or (thumb.get("href") if thumb else "") or "").strip()
        image = _image_from_style(thumb.get("style", "")) if thumb else None
        entry = _make_entry(title_link.get_text(" ", strip=True), href, image=image)
        if entry:
            entries.append(entry)
    return entries


def _parse_search_page(html: str | None) -> list[Entries]:
    """Parse a full search-results page (.ml-item cards)."""
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries: list[Entries] = []
    for card in soup.select(".ml-item"):
        link = card.select_one("a.ml-mask") or card.select_one("a[href]")
        if not link:
            continue

        title_el = card.select_one(".mli-info h2") or card.select_one(".qtip-title") or link
        img = card.select_one("img")
        image = (img.get("data-original") or img.get("data-src") or img.get("src") or "").strip() if img else None
        entry = _make_entry(
            name=title_el.get_text(" ", strip=True),
            href=link.get("href") or "",
            image=image,
            year=_year_from_card(card),
            item_id=card.get("data-movie-id"),
        )
        if entry:
            entries.append(entry)
    return entries


def _fetch_live_search(query: str) -> str | None:
    """POST to the site's SearchWP AJAX endpoint and return the result HTML."""
    cache_key = f"live-search:{query}"
    if cache_key in _URL_CACHE:
        return _URL_CACHE[cache_key]

    base = _base_url()
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": base,
        "Referer": f"{base}/",
        "X-Requested-With": "XMLHttpRequest",
    }
    html: str | None = None
    try:
        with create_client(headers=headers) as client:
            resp = client.post(
                f"{base}/wp-admin/admin-ajax.php",
                data={"s": query, "action": "searchwp_live_search", "swpengine": "default", "swpquery": query},
                timeout=30,
            )
        resp.raise_for_status()
        html = resp.text
    except Exception as error:
        logger.debug(f"[Guardaserie] Live search failed for '{query}': {error}")

    _URL_CACHE[cache_key] = html
    return html


def search_guardoseries(query: str, max_pages: int = _SEARCH_MAX_PAGES) -> list[Entries]:
    """Search Guardaserie using the site's own search (AJAX dropdown + first results page)."""
    query = (query or "").strip()
    if not query:
        return []

    entries: list[Entries] = []
    seen: set[str] = set()

    def add_all(items: list[Entries]) -> None:
        for item in items:
            key = item.url or f"{item.type}:{item.slug}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(item)

    for page in range(1, max_pages + 1):
        search_url = f"{_base_url()}/?s={quote_plus(query)}" if page == 1 else f"{_base_url()}/page/{page}/?s={quote_plus(query)}"
        page_entries = _parse_search_page(_fetch_html(search_url))
        if not page_entries:
            break
        add_all(page_entries)

    add_all(_parse_live_search(_fetch_live_search(query)))
    return entries


def search_guardoseries_slug(title: str, year: int = None) -> tuple[str | None, str | None]:
    """
    Search guardoseries for a title and return (slug, content_type).
    content_type is 'serie' or 'film'.
    """
    entries = search_guardoseries(title)
    if not entries:
        return None, None

    query = _normalize_title(title)
    best: Entries | None = None
    best_score = -1.0
    for entry in entries:
        score = difflib.SequenceMatcher(None, query, _normalize_title(entry.name)).ratio()
        if entry.name.strip().lower() == title.strip().lower():
            score += 1.0
        if year and str(entry.year) == str(year):
            score += 0.1

        if score > best_score:
            best_score = score
            best = entry

    if not best:
        return None, None
    return best.slug, "serie" if best.type == "tv" else "film"


def _season_number_from_text(value: str) -> int | None:
    match = re.search(r"(?:stagione|season|s)\s*(\d+)", (value or "").lower())
    return int(match.group(1)) if match else None


def _episode_number_from_url(url: str) -> int | None:
    match = re.search(r"episodio[-_/]?(\d+)", url, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _add_episode(episodes_by_season: dict[int, list[dict]], season_num: int, episode_num: int, ep_url: str) -> None:
    season_episodes = episodes_by_season.setdefault(season_num, [])
    if any(ep["number"] == episode_num for ep in season_episodes):
        return
    season_episodes.append({
        "number": episode_num,
        "name": f"E{episode_num}",
        "url": ep_url,
    })


def get_serie_episodes(serie_slug: str) -> dict[int, list[dict]]:
    """
    Enter a serie page and return episodes grouped by season number.
    Each episode dict: {number, name, url}
    """
    url = f"{_base_url()}/serie/{serie_slug}/"
    html = _fetch_html(url)
    if not html:
        return {}

    episodes_by_season: dict[int, list[dict]] = {}

    # Preferred: parse each season block ("Stagione 1" -> "Episodio 1", ...)
    soup = BeautifulSoup(html, "html.parser")
    for block in soup.select("#seasons .tvseason"):
        title_el = block.select_one(".les-title")
        season_num = _season_number_from_text(title_el.get_text(" ", strip=True) if title_el else "")
        if season_num is None:
            continue

        for link in block.select(".les-content a[href*='/episodio/']"):
            ep_url = (link.get("href") or "").strip()
            episode_num = _episode_number_from_url(ep_url)
            if ep_url and episode_num is not None:
                _add_episode(episodes_by_season, season_num, episode_num, ep_url)

    # Fallback: derive season/episode from the slug like 'anna-stagione-1-episodio-1'
    if not episodes_by_season:
        for match in _EPISODE_LINK_RE.finditer(html):
            ep_url = match.group(1)
            ep_slug = ep_url.rstrip("/").split("/")[-1]
            season_num = None
            episode_num = None
            parts = ep_slug.split("-")
            for i, part in enumerate(parts):
                if part == "stagione" and i + 1 < len(parts):
                    try:
                        season_num = int(parts[i + 1])
                    except ValueError:
                        pass
                if part == "episodio" and i + 1 < len(parts):
                    try:
                        episode_num = int(parts[i + 1])
                    except ValueError:
                        pass

            if season_num is not None and episode_num is not None:
                _add_episode(episodes_by_season, season_num, episode_num, ep_url)

    # Sort episodes within each season
    for season_num in episodes_by_season:
        episodes_by_season[season_num].sort(key=lambda e: e["number"])

    return episodes_by_season


class GetSerieInfo:
    def __init__(self, series_name: str, tmdb_id: str = None, year: int = None, serie_slug: str = None):
        self.tmdb_id = tmdb_id
        self.series_name = series_name
        self.series_display_name = series_name
        self.year = year
        self.serie_slug = serie_slug
        self.seasons_manager = SeasonManager()
        self._loaded = False
        self._load_lock = threading.Lock()
        self.episodes_by_season = {}
        self._episodes_by_season = {}

    def _load(self):
        with self._load_lock:
            self._load_locked()

    def _load_locked(self):
        if self._loaded:
            return

        self._loaded = True
        try:
            if not self.serie_slug:
                self.serie_slug, _ = search_guardoseries_slug(self.series_name, self.year)
            if not self.serie_slug:
                logger.error(f"[Guardaserie] No series slug found for {self.series_name}")
                return

            self._episodes_by_season = get_serie_episodes(self.serie_slug)
            self.episodes_by_season = self._episodes_by_season

            for season_number in sorted(self._episodes_by_season):
                self.seasons_manager.add(
                    Season(
                        id=season_number,
                        number=season_number,
                        name=f"S{season_number}",
                        slug=f"stagione-{season_number}",
                        type="season",
                    )
                )

        except Exception as error:
            logger.error(f"[Guardaserie] Series load failed: {error}")

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season."""
        self._load()

        return [
            Episode(
                id=raw_episode.get("number"),
                number=raw_episode.get("number"),
                name=raw_episode.get("name"),
                url=raw_episode.get("url"),
                year=self.year,
            )
            for raw_episode in self._episodes_by_season.get(season_number, [])
        ]


def get_episode_stream(episode_url: str) -> str | None:
    """Resolve a direct .m3u8 stream URL for a Guardaserie episode (uqload/loadm)."""
    html = _fetch_html(episode_url)
    if html:
        server_iframes = _server_iframes(html)
        stream = resolve_stream_with_fallback(server_iframes, referer=episode_url)
        if stream:
            return stream
    # Fallback: page loads hosters via JS -> use Playwright on the page itself
    return _resolve_page_streams(episode_url)


def get_film_stream(film_slug: str) -> str | None:
    """Resolve a direct .m3u8 stream URL for a Guardaserie film (uqload/loadm)."""
    url = f"{_base_url()}/{film_slug}/"
    html = _fetch_html(url)
    if html:
        server_iframes = _server_iframes(html)
        stream = resolve_stream_with_fallback(server_iframes, referer=url)
        if stream:
            return stream
    # Fallback: page loads hosters via JS -> use Playwright on the page itself
    return _resolve_page_streams(url)


def _resolve_page_streams(page_url: str) -> str | None:
    """Use Playwright to load the episode/film page and intercept the real .m3u8 stream URL."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    browser = _get_pw_browser()
    if not browser:
        return None
    context = None
    page = None
    try:
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(page_url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        # Reveal iframes hidden behind play/server buttons
        for selector in [
            "button.play",
            ".play-button",
            "#play",
            "[class*='play']",
            "button:has-text('Guarda')",
            "button:has-text('Play')",
            ".server-item",
            ".mirrors a",
            ".player-servers a",
            ".liste-server a",
        ]:
            try:
                els = page.query_selector_all(selector)
                for el in els[:5]:
                    try:
                        el.click(timeout=1000)
                        page.wait_for_timeout(1200)
                    except Exception:
                        continue
            except Exception:
                continue

        # Poll for video iframes injected by JS
        server_iframes: list[tuple[str, str]] = []
        for _ in range(12):  # up to ~12 seconds
            page.wait_for_timeout(1000)
            try:
                html = page.content()
            except Exception:
                break
            server_iframes = _server_iframes(html)
            if server_iframes:
                break
        if not server_iframes:
            return None
        return resolve_stream_with_fallback(server_iframes, referer=page_url)
    except Exception as error:
        logger.error(f"[Guardaserie] Playwright page resolve failed for {page_url}: {error}")
        return None
    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
