# 21.05.24

import json

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.utils.language import resolve_iso639_1
from VibraVid.core.utils.selector import FilterSpec, split_audio_slots
from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager, config_manager
from VibraVid.utils.http_client import create_client, get_userAgent

from .downloader import download_film, download_series

indice = 0
_useFor = "Film_Serie"
_db_upload = True
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def register_cli_args(parser) -> list:
    """Register CLI options."""
    group = parser.add_argument_group("StreamingCommunity options")
    group.add_argument("--skip-ts", dest="skip_ts", action="store_true", help="Skip TS/CAM releases.")
    return ["skip_ts"]


def _effective_languages() -> list[str]:
    """Derive which site catalog(s) to search/list from the global DOWNLOAD.select_audio filter"""
    select_audio = config_manager.config.get("DOWNLOAD", "select_audio", default="")
    if not select_audio:
        return ["it", "en"]

    raw = select_audio.strip()
    slots = split_audio_slots(raw)

    if slots is not None:
        groups_raw = [slots[num] for num in sorted(slots)]
    else:
        spec = FilterSpec.parse(raw, "audio")
        if spec.select_all or spec.drop:
            return ["it", "en"]
        groups_raw = [spec.langs] if spec.langs else []

    for langs in groups_raw:
        raw_codes = [c.strip() for c in langs.split("|") if c.strip()]
        languages = []
        for code in raw_codes:
            iso = resolve_iso639_1(code)
            if iso in ("it", "en") and iso not in languages:
                languages.append(iso)
        if languages:
            return languages

    return ["it", "en"]


def title_search(query: str) -> int:
    """
    Search for titles based on a search query in both IT and EN languages.

    Parameters:
        query (str): The query to search for.

    Returns:
        int: The number of unique titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    # Dictionary to track unique IDs
    seen_ids = set()
    languages = _effective_languages()

    for lang in languages:
        console.print(f"[cyan]Searching in language: [yellow]{lang}")

        try:
            with create_client(headers={"user-agent": get_userAgent()}) as client:
                response = client.get(f"{site_constants.FULL_URL}/{lang}")
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            version = json.loads(soup.find("div", {"id": "app"}).get("data-page"))["version"]
        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME} version ({lang}), request error: {e}")
            continue

        search_url = f"{site_constants.FULL_URL}/{lang}/search?q={query}"
        console.print(f"[cyan]Search url: [yellow]{search_url}")

        try:
            with create_client(
                headers={"user-agent": get_userAgent(), "x-inertia": "true", "x-inertia-version": version}
            ) as client:
                response = client.get(search_url)
            response.raise_for_status()
        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME} ({lang}), request search error: {e}")
            continue

        # Collect json data
        try:
            data = response.json().get("props").get("titles")
        except Exception as e:
            console.log(f"[red]Error parsing JSON response ({lang}): {e}")
            continue

        for _i, dict_title in enumerate(data):
            try:
                title_id = dict_title.get("id")

                # Skip if we've already seen this ID
                if title_id in seen_ids:
                    continue

                # Add ID to seen set
                seen_ids.add(title_id)

                images = dict_title.get("images") or []
                filename = None
                preferred_types = ["poster", "cover", "cover_mobile", "background"]
                for ptype in preferred_types:
                    for img in images:
                        if img.get("type") == ptype and img.get("filename"):
                            filename = img.get("filename")
                            break

                    if filename:
                        break

                if not filename and images:
                    filename = images[0].get("filename")

                image_url = None
                if filename:
                    image_url = f"{site_constants.FULL_URL.replace('stream', 'cdn.stream')}/images/{filename}"

                # Extract year: prefer first_air_date at root level, otherwise search in translations
                year = None
                if not year:
                    for trans in dict_title.get("translations") or []:
                        if trans.get("key") == "first_air_date" and trans.get("value"):
                            year = trans.get("value")
                            break

                # If still no year, try release_date in translations
                if not year:
                    for trans in dict_title.get("translations") or []:
                        if trans.get("key") == "release_date" and trans.get("value"):
                            year = trans.get("value")
                            break

                # If still no year, use root level fields
                if not year:
                    year = dict_title.get("last_air_date") or dict_title.get("release_date")

                entries_manager.add(
                    Entries(
                        id=title_id,
                        slug=dict_title.get("slug"),
                        name=dict_title.get("name"),
                        type=dict_title.get("type"),
                        image=image_url,
                        year=year.split("-")[0] if year and "-" in year else "9999",
                        provider_language=lang,
                        tmdb_id=dict_title.get("tmdb_id"),
                    )
                )

            except Exception as e:
                console.print(f"[red]Error parsing a film entry ({lang}): {e}")

    return len(entries_manager)


search, process_search_result = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    download_film=download_film,
    download_series=download_series,
)
