# 14.10.26
# By @Syncs-Luca98

import re

from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability

from . import client as api
from .downloader import download_film, download_series

indice = 17
_useFor = "Film_Serie"
_region = api.REGIONS
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_MOVIE_URL_RE = re.compile(r"chili\.com/(?:(?P<cc>[a-z]{2})/)?(?:film|movie)/(?P<id>[a-z0-9-]+)", re.I)
_SHOW_URL_RE = re.compile(r"chili\.com/(?:(?P<cc>[a-z]{2})/)?(?:serie|series)/(?P<id>[a-z0-9-]+)", re.I)
_SEASON_URL_RE = re.compile(r"chili\.com/(?:(?P<cc>[a-z]{2})/)?seasons/(?P<id>[a-z0-9-]+)", re.I)


def register_cli_args(parser) -> list:
    """Register CLI options."""
    group = parser.add_argument_group("Chili options")
    group.add_argument("--url", dest="url", default=None, metavar="URL", help="Chili title URL (movie, show or player/stream page).")
    group.add_argument("--device", dest="device", default="lgui40", choices=["lgui40", "atvui40", "web"], help="Device profile: lgui40 = 1080p PlayReady (default), atvui40 = 1080p Widevine, web = 720p Widevine.",)
    group.add_argument("--quality", dest="quality", default="FHD", choices=["FHD", "UHD"], help="Requested video quality (UHD only if the title offers it).",)
    group.add_argument("--audio-lang", dest="audio_lang", default=None, help="Audio language code, e.g. ITA, NLD, ENG (default: the main language of the country).",)
    group.add_argument("--country", dest="country", default=None, metavar="CC",help="Chili country for search and titles, e.g. it, uk, de, at, pl, es (default: from the URL, else 'country' in login.toml, else it).",)
    return ["url", "device", "quality", "audio_lang", "country"]


def _market_of(url_match, fallback: str | None = None) -> str:
    """Country of a Chili URL (chili.com/it/...), else the configured default."""
    code = url_match.group("cc") if url_match is not None else None
    return api.resolve_market(code or fallback)


def _movie_entry(item: dict, market: str) -> Entries | None:
    content_id = item.get("id") or item.get("catalogRedirectId")
    title = item.get("title") or item.get("display_name")
    if not content_id or not title:
        return None

    kind = api.purchase_kind(item)
    availability = item.get("availability") or api.availability_label(item)
    return Entries(
        id=content_id,
        name=title,
        type="movie",
        year=str(item.get("year") or item.get("productionYear") or "9999"),
        image=api.artwork(item),
        url=f"https://www.chili.com/{market}/film/{content_id}",
        slug=item.get("urlSlug") or content_id,
        kind=kind if kind != "unknown" else "avod",
        availability=availability,
        market=market,
    )


def _show_entry(item: dict, market: str) -> Entries | None:
    content_id = item.get("id") or item.get("catalogRedirectId")
    title = item.get("title") or item.get("display_name")
    if not content_id or not title:
        return None

    return Entries(
        id=content_id,
        name=title,
        type="tv",
        year=str(item.get("year") or item.get("productionYear") or "9999"),
        image=api.artwork(item),
        url=f"https://www.chili.com/{market}/serie/{content_id}",
        slug=item.get("urlSlug") or content_id,
        kind=item.get("kind", api.purchase_kind(item)),
        availability=item.get("availability") or api.availability_label(item),
        market=market,
    )


def title_search(query: str) -> int:
    """Search movies, TV shows"""
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    query = (query or "").strip()
    if not query:
        return 0

    market = api.resolve_market(None)

    # Direct URL / slug handling
    direct = (
        _resolve_url_to_item(query)
        if ("chili.com" in query or query.startswith(("movie:", "show:", "season:")))
        else None
    )
    if direct:
        entries_manager.add(Entries(**direct))
        return len(entries_manager)

    console.print(f"[cyan]Search query: [yellow]{query}")
    found = 0
    for endpoint, builder in (("movies", _movie_entry), ("tv_shows", _show_entry)):
        try:
            for item in api.search_endpoint(query, endpoint, market=market):
                try:
                    entry = builder(item, market)
                except Exception as e:
                    console.print(f"[yellow]Error parsing a {endpoint} entry: {e}")
                    continue

                if entry is not None:
                    entries_manager.add(entry)
                    found += 1

        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME}, {endpoint} search error: {e}")

    return len(entries_manager)


def _resolve_url_to_item(url: str) -> dict | None:
    """Resolve a Chili URL (page, player/stream, gizmo id) to a direct item dict."""
    text = (url or "").strip()
    if not text:
        return None

    for prefix, kind, entry_type in (
        ("movie:", "movies", "movie"),
        ("show:", "tv_shows", "tv"),
        ("season:", "seasons", "tv"),
    ):
        if text.startswith(prefix):
            content_id = text[len(prefix) :].strip()
            return _detail_item(content_id, kind, entry_type, text, api.resolve_market(None))

    movie_match = _MOVIE_URL_RE.search(text)
    if movie_match:
        return _detail_item(movie_match.group("id"), "movies", "movie", text, _market_of(movie_match))

    show_match = _SHOW_URL_RE.search(text)
    if show_match:
        return _detail_item(show_match.group("id"), "tv_shows", "tv", text, _market_of(show_match))

    season_match = _SEASON_URL_RE.search(text)
    if season_match:
        season_id = season_match.group("id")
        console.print(f"[cyan]Detected season from URL: [green]{season_id}")
        return {
            "id": season_id,
            "name": season_id,
            "type": "tv",
            "url": text,
            "year": "9999",
            "slug": season_id,
            "market": _market_of(season_match),
        }

    return None


def _detail_item(content_id: str, kind: str, entry_type: str, url: str, market: str) -> dict | None:
    """Fetch gizmo detail for a direct URL so name/year/kind are exact."""
    try:
        if kind == "movies":
            detail = api.get_movie_detail(content_id, market)
            entry = _movie_entry(detail, market)
        elif kind == "tv_shows":
            detail = api.get_tvshow_detail(content_id, market)
            entry = _show_entry(detail, market)
        else:
            return {
                "id": content_id,
                "name": content_id,
                "type": entry_type,
                "url": url,
                "year": "9999",
                "slug": content_id,
                "market": market,
            }
    except Exception as e:
        console.print(f"[red]Could not resolve Chili URL '{content_id}': {e}")
        return None

    if entry is None:
        return None

    item = entry.to_dict()
    item["url"] = url
    console.print(f"[cyan]Detected {entry_type} from URL: [green]{item.get('name')}")
    return item


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
    resolve_url=_resolve_url_to_item,
)
