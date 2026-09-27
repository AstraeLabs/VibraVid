# 16.03.25

import os
import time
from urllib.parse import parse_qs, urlparse

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.utils.language import resolve_locale
from VibraVid.core.utils.selector import FilterSpec, split_audio_slots
from VibraVid.services._base import Entries, anime_folder, movie_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message

from .client import CrunchyrollClient, get_episode_chapters, get_playback_session
from .manifest_builder import build_unified_manifest
from .scrapper import GetSerieInfo

console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")
CR_LICENSE_URL = "https://www.crunchyroll.com/license/v1/license/widevine"


def _make_dash_audio_track(mpd_url: str, locale: str, headers: dict, license_headers: dict) -> dict:
    """Build an ``other_tracks`` audio entry for DASH_Downloader."""
    return {
        "type": "audio",
        "manifest": "dash",
        "url": mpd_url,
        "language": locale,
        "headers": headers,
        "license_url": CR_LICENSE_URL,
        "license_headers": license_headers,
    }


def _subtitles_to_other_tracks(subtitles: list) -> list:
    tracks = []
    for sub in subtitles or []:
        if not isinstance(sub, dict):
            continue

        sub_url = sub.get("url")
        if not sub_url:
            continue

        track = {
            "type": "subtitle",
            "url": sub_url,
            "language": sub.get("language") or "und",
            "name": sub.get("label") or sub.get("name") or sub.get("language") or "Subtitle",
        }
        fmt = str(sub.get("format") or "").strip().lower().lstrip(".")
        if fmt:
            track["extension"] = fmt
            track["format"] = fmt

        if sub.get("closed_caption"):
            track["cc"] = True

        tracks.append(track)

    return tracks


def _merge_subtitles(base: list, extra: list) -> list:
    """Union subtitle lists by (language, closed_caption), preferring entries already in ``base``."""
    merged = list(base or [])
    seen = {(s.get("language"), bool(s.get("closed_caption"))) for s in merged if isinstance(s, dict)}

    for sub in extra or []:
        if not isinstance(sub, dict):
            continue
        key = (sub.get("language"), bool(sub.get("closed_caption")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(sub)

    return merged


def parse_select_audio_filter(select_audio: str) -> list:
    """
    Parse select_audio config format (shared FilterSpec grammar, e.g. "ita|it", "l=ita", "all", or exclusive-priority slots "1ita|2eng") to extract locale groups.
    """
    if not select_audio:
        return []

    raw = select_audio.strip()
    slots = split_audio_slots(raw)

    if slots is not None:
        groups_raw = [slots[num] for num in sorted(slots)]
    else:
        spec = FilterSpec.parse(raw, "audio")
        if spec.select_all or spec.drop:
            return []
        groups_raw = [spec.langs] if spec.langs else []

    groups = []
    for langs in groups_raw:
        raw_codes = [c.strip() for c in langs.split("|") if c.strip()]
        locales = []
        seen = set()
        for code in raw_codes:
            locale = resolve_locale(code)

            if not locale:
                console.print(f"[yellow]Warning: language code '{code}' not recognised, skipping")
                continue

            if locale not in seen:
                locales.append(locale)
                seen.add(locale)

        if locales:
            groups.append(locales)

    return groups


def _build_license_headers(base_headers: dict, content_id: str, mpd_url: str, fallback_token: str) -> dict:
    """Build Widevine license request headers."""
    query_params = parse_qs(urlparse(mpd_url).query)
    playback_guid = (query_params.get("playbackGuid") or [fallback_token])[0]

    headers = base_headers.copy()
    headers.update(
        {
            "x-cr-content-id": content_id,
            "x-cr-video-token": playback_guid,
        }
    )
    return headers


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name} \n")

    # Initialize Crunchyroll client
    client = CrunchyrollClient()
    client.clear_all_sessions()  # release anything leaked by a crashed/killed previous run

    # Define filename and path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    # Extract media ID
    url_id = select_title.get("url").split("/")[-1]
    preferred_groups = parse_select_audio_filter(config_manager.config.get("DOWNLOAD", "select_audio", default=""))

    # Build the locale -> version GUID map from the playback API
    available = client.get_available_versions(url_id)
    time.sleep(2)
    locale_to_guid = {v["audio_locale"]: v["guid"] for v in available}

    preferred_locales = next((g for g in preferred_groups if any(loc in locale_to_guid for loc in g)), [])
    if not preferred_locales and len(preferred_groups) > 1:
        console.print("[yellow]Skipping — no audio slot matched the requested select_audio filter.")
        client.close()
        return None, True, "no audio slot matched select_audio filter"

    main_id = url_id
    main_locale = None
    for locale in preferred_locales:
        if locale in locale_to_guid:
            main_id = locale_to_guid[locale]
            main_locale = locale
            break

    mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = get_playback_session(client, main_id, None)
    extra_audio_tracks = []
    for locale in preferred_locales:
        if locale == main_locale:
            continue

        guid = locale_to_guid.get(locale)
        if not guid or guid == main_id:
            if locale not in locale_to_guid:
                console.print(f"[yellow]Locale {locale} not available for this film")
            continue

        try:
            time.sleep(2)
            ex_mpd_url, ex_hdrs, _, ex_token, _ = get_playback_session(client, guid, None)
            if not ex_mpd_url:
                console.print(f"[yellow]Locale {locale} not available for this film")
                continue
            
            ex_license_hdrs = _build_license_headers(ex_hdrs, guid, ex_mpd_url, ex_token)
            extra_audio_tracks.append(_make_dash_audio_track(ex_mpd_url, locale, ex_hdrs, ex_license_hdrs))
        except Exception as e:
            console.print(f"[yellow]Error fetching audio {locale}: {e}")

    if extra_audio_tracks:
        console.print(f"[dim]Extra audio: {[v['language'] for v in extra_audio_tracks]}")

    license_headers = _build_license_headers(mpd_headers, main_id, mpd_url, token)
    other_tracks = _subtitles_to_other_tracks(mpd_list_sub)
    other_tracks.extend(extra_audio_tracks)
    client.close()

    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_headers=mpd_headers,
        license_url=CR_LICENSE_URL,
        license_headers=license_headers,
        other_tracks=other_tracks or None,
        output_path=os.path.join(movie_path, movie_name),
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, main_guid=None):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    client = scrape_serie.client
    client.clear_all_sessions()  # release anything leaked by a crashed/killed previous run
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    title_path = os_manager.get_sanitize_path(anime_folder(*path_components))
    title_name = f"{filename}.{extension_output}"

    # Get media ID and main_guid
    url_id = obj_episode.url.split("/")[-1]
    main_guid = getattr(obj_episode, "main_guid", None)

    # Parse preferred audio locale groups
    preferred_groups = parse_select_audio_filter(config_manager.config.get("DOWNLOAD", "select_audio", default=""))

    # Build the map of audio locale -> version GUID from the playback API.
    available = client.get_available_versions(url_id)
    time.sleep(2)
    locale_to_guid = {v["audio_locale"]: v["guid"] for v in available}
    api_main_guid = next((v["guid"] for v in available if "main" in v.get("roles", [])), None)

    preferred_locales = next((g for g in preferred_groups if any(loc in locale_to_guid for loc in g)), [])
    if not preferred_locales and len(preferred_groups) > 1:
        console.print("[yellow]Skipping — no audio slot matched the requested select_audio filter.")
        return None, True, "no audio slot matched select_audio filter"

    # main_guid unlocks the complete subtitle set (prefer the "main" version, else metadata)
    main_guid = main_guid or api_main_guid

    # Determine primary audio: first preferred locale that is actually available
    main_id = url_id
    main_locale = None
    for locale in preferred_locales:
        if locale in locale_to_guid:
            main_id = locale_to_guid[locale]
            main_locale = locale
            break

    mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = get_playback_session(client, main_id, main_guid)
    audio_locale = main_locale or audio_locale
    all_subtitles = _merge_subtitles(mpd_list_sub, [])
    license_headers = _build_license_headers(mpd_headers, main_id, mpd_url, token)

    # Fetch extra audio tracks for the remaining preferred locales
    extra_locales = []
    for locale in preferred_locales:
        if locale == main_locale:
            continue

        guid = locale_to_guid.get(locale)
        if not guid or guid == main_id:
            if locale not in locale_to_guid:
                console.print(f"[yellow]Locale {locale} not available for this episode")
            continue

        try:
            time.sleep(2)
            ex_mpd_url, ex_hdrs, ex_subs, ex_token, _ = get_playback_session(client, guid, None)
            if not ex_mpd_url:
                console.print(f"[yellow]Locale {locale} not available for this episode")
                continue
            
            ex_license_hdrs = _build_license_headers(ex_hdrs, guid, ex_mpd_url, ex_token)
            extra_locales.append(
                {
                    "locale": locale,
                    "mpd_url": ex_mpd_url,
                    "headers": ex_hdrs,
                    "license_headers": ex_license_hdrs,
                }
            )
            all_subtitles = _merge_subtitles(all_subtitles, ex_subs)
            time.sleep(5)  # Small delay to avoid rate limiting between calls
        except Exception as e:
            console.print(f"[yellow]Errore fetch audio {locale}: {e}")

    if not extra_locales:
        console.print(f"[dim]No extra audio (only {main_locale or audio_locale})")

    manifest_json, merged_keys = build_unified_manifest(
        main_locale=main_locale or audio_locale or main_id,
        main_mpd_url=mpd_url,
        main_mpd_headers=mpd_headers,
        main_license_headers=license_headers,
        extra_locales=extra_locales,
        subtitles=all_subtitles,
        license_url=CR_LICENSE_URL,
    )

    if not manifest_json:
        console.print("[red]Could not build a unified manifest for this episode, aborting.")
        return None, True, "Could not build unified manifest"

    chapters = get_episode_chapters(client, url_id)

    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_content=manifest_json,
        mpd_headers=mpd_headers,
        key=merged_keys,
        license_url=CR_LICENSE_URL,
        chapters=chapters or None,
        output_path=os.path.join(title_path, title_name),
    ).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
    """
    start_message()
    if not scrape_serie:
        scrape_serie = GetSerieInfo(select_season.url.split("/")[-1])
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""

        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
