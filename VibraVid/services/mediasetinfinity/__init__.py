# 21.05.24

import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability, create_client, get_userAgent

from .client import get_client, get_metadata_by_guid
from .downloader import download_film, download_series
from .regions import get_region, region_conf

indice = 3
_useFor = "Film_Serie"
_region = ["IT", "ES"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_SERIES_ID_RE = re.compile(r"(SE\d+)")
_FILM_ID_RE = re.compile(r"(F\d{6,})")
_ES_CONTENT_ID_RE = re.compile(r'"?app-reference-id"?[^M]{0,40}(M\d{10,})')
_ES_FINDER_ID_RE = re.compile(r"finder/esp/(M\d{10,})")


def register_cli_args(parser) -> list:
    """Register CLI options."""
    group = parser.add_argument_group("Mediaset Infinity IT/ES options")
    group.add_argument("--url", dest="url", default=None, metavar="URL", help="Mediaset Infinity title URL.")
    group.add_argument("--country", dest="country", default="it", choices=["it", "es"])
    return ["url", "country"]


def _resolve_es_url(url: str):
    """Resolve a mediasetinfinity.es /player/ (or video) URL to a single downloadable item."""
    try:
        with create_client() as client:
            resp = client.get(url, headers={"user-agent": get_userAgent()})
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        console.print(f"[red]Failed to fetch ES page: {e}")
        return None

    m = _ES_CONTENT_ID_RE.search(html) or _ES_FINDER_ID_RE.search(html)
    if not m:
        console.print("[red]Could not extract ES content id (M...) from the page. Make sure it is a /player/ URL.")
        return None
    content_id = m.group(1)

    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    name = title_match.group(1).strip() if title_match else content_id
    name = re.sub(r"\s+Video$", "", name)  # trailing " Video" on ES player titles

    console.print(f"[cyan]Detected ES content from URL: [green]{name} [white]({content_id})")
    return {"id": content_id, "name": name, "type": "film", "url": url, "year": "9999"}


def _resolve_url_to_item(url: str):
    """Resolve a Mediaset Infinity URL to an item dict (region-aware)."""
    if get_region() == "es":
        return _resolve_es_url(url)

    series_match = _SERIES_ID_RE.search(url)
    if series_match:
        serie_id = series_match.group(1)
        entry = get_metadata_by_guid(serie_id, "mediaset-prod-all-series-v2")
        name = entry.get("title") if entry else serie_id
        year = str(entry.get("year")) if entry and entry.get("year") else "9999"
        console.print(f"[cyan]Detected series from URL: [green]{name}")
        return {"id": serie_id, "name": name, "type": "tv", "url": url, "year": year}

    film_match = _FILM_ID_RE.search(url)
    if film_match:
        film_id = film_match.group(1)
        entry = get_metadata_by_guid(film_id, "mediaset-prod-all-programs-v2")
        if not entry:
            console.print(f"[red]Could not resolve film metadata for id '{film_id}'")
            return None
        name = entry.get("title", film_id)
        year = str(entry.get("year")) if entry.get("year") else "9999"
        console.print(f"[cyan]Detected film from URL: [green]{name}")
        return {"id": film_id, "name": name, "type": "film", "url": url, "year": year}

    # No SE.../F... id in the URL: it is likely a brand/landing (browse) page.
    # Signalled by returning None; the caller falls back to browse resolution.
    return None


def title_search(query: str) -> int:
    """
    Search for titles based on a search query.

    Parameters:
        query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    conf = region_conf()
    api = get_client()
    if not api.getHash256():
        console.print("[yellow]Search not available for this region; use --url instead.")
        return 0

    search_url = conf["graphql_url"]
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    params = {
        "extensions": f'{{"persistedQuery":{{"version":1,"sha256Hash":"{api.getHash256()}"}}}}',
        "variables": f'{{"first":10,"property":"search","query":"{query}","uxReference":"filteredSearch"}}',
    }
    try:
        with create_client(headers=api.generate_request_headers()) as client:
            response = client.get(search_url, params=params)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    resp_json = response.json()
    try:
        items = resp_json["data"]["getSearchPage"]["areaContainersConnection"]["areaContainers"][0]["areas"][0][
            "sections"
        ][0]["collections"][0]["itemsConnection"]["items"]
    except (TypeError, KeyError, IndexError):
        console.print("[yellow]No search results.")
        return 0

    image_base_url = "https://img-prod-api2.mediasetplay.mediaset.it/api/images"
    for item in items:
        try:
            is_series = (
                item.get("__typename") == "SeriesItem"
                or item.get("cardLink", {}).get("referenceType") == "series"
                or bool(item.get("seasons"))
            )
            item_type = "tv" if is_series else "film"
        except Exception:
            break

        # Skip non-playable catalog/landing cards: these come back as GenericItem
        if not item.get("guid"):
            continue

        date = item.get("year") or None

        vertical_image = None
        for img in item.get("cardImages", []):
            if img.get("sourceType") == "image_vertical" or img.get("type") == "image_vertical":
                vertical_image = img
                break
        image_url = ""
        if vertical_image:
            image_url = f"{image_base_url}/{vertical_image.get('engine', 'mse')}/v5/{conf['image_region']}/{vertical_image.get('id', '')}/image_vertical/300/450"
            if vertical_image.get("r", ""):
                image_url += f"?r={vertical_image.get('r', '')}"

        entries_manager.add(
            Entries(
                id=item.get("guid", ""),
                name=item.get("cardTitle", "No Title"),
                type=item_type,
                image=image_url,
                year=date,
                url=item.get("cardLink", {}).get("value", ""),
            )
        )

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
    resolve_url=_resolve_url_to_item,
)
