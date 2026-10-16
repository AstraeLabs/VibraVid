# 14.10.26
# By @Syncs-Luca98

import os

from rich.console import Console

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.downloader.base import DownloadResult
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, movie_folder, print_download_header, print_episode_header, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, start_message

from . import client as api
from .scrapper import GetSerieInfo

console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")
_SDH_TYPES = {"sdh", "hearing_impaired", "hard_of_hearing", "hi"}
_CC_TYPES = {"cc", "closed_captions", "closed_caption"}


def _options() -> dict:
    return context_tracker.site_options or {}


def _resolve_stream(content_id: str, content_type: str, market: str | None = None) -> tuple[dict, str]:
    opts = _options()
    audio = api.audio_language(market, None, opts.get("audio_lang"))
    quality = (opts.get("quality") or "FHD").upper()
    device = (opts.get("device") or "lgui40").lower()
    if device not in api.DEVICES:
        device = "lgui40"

    kind = opts.get("kind") or "avod"
    if content_type in ("movies", "episodes") and kind == "unknown":
        kind = "avod"

    if kind == "avod":
        return api.get_avod_streaming(content_id, content_type, audio, quality, device, market=market), kind
    return api.get_me_streaming(content_id, content_type, audio, quality, device, market=market), kind


def _subtitle_tracks(stream_info: dict) -> list[dict]:
    tracks = []
    all_subs = stream_info.get("all_subtitles") or {}
    # all_subtitles can be dict {lang: [subs]} or list
    if isinstance(all_subs, dict):
        for lang, subs in all_subs.items():
            for sub in subs:
                if not isinstance(sub, dict):
                    continue
                url = sub.get("url")
                if not url:
                    continue

                fmt = str(sub.get("format") or "vtt").strip("[]' ").lower() or "vtt"
                locale = (sub.get("locale") or sub.get("language") or lang or "und").lower()
                kind = str(sub.get("subtitle_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
                forced = kind == "forced" or bool(sub.get("forced"))
                sdh = kind in _SDH_TYPES
                cc = kind in _CC_TYPES
                flags = [name for name, on in (("forced", forced), ("sdh", sdh), ("cc", cc)) if on]
                label = str(sub.get("language") or locale).upper()
                tracks.append(
                    {
                        "type": "subtitle",
                        "url": url,
                        "language": "-".join([locale, *flags]),
                        "format": fmt,
                        "extension": fmt,
                        "name": " ".join([label, *flags]),
                        "forced": forced,
                        "sdh": sdh,
                        "cc": cc,
                    }
                )
    
    elif isinstance(all_subs, list):
        for sub in all_subs:
            url = sub.get("url")
            if not url:
                continue

            fmt = str(sub.get("format") or "vtt").strip("[]' ").lower() or "vtt"
            locale = (sub.get("locale") or sub.get("language") or "und").lower()
            kind = str(sub.get("subtitle_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
            forced = kind == "forced" or bool(sub.get("forced"))
            sdh = kind in _SDH_TYPES
            cc = kind in _CC_TYPES
            flags = [name for name, on in (("forced", forced), ("sdh", sdh), ("cc", cc)) if on]
            label = str(sub.get("language") or locale).upper()
            tracks.append(
                {
                    "type": "subtitle",
                    "url": url,
                    "language": "-".join([locale, *flags]),
                    "format": fmt,
                    "extension": fmt,
                    "name": " ".join([label, *flags]),
                    "forced": forced,
                    "sdh": sdh,
                    "cc": cc,
                }
            )
    return tracks


def _make_chili_license_request_fn(client, streaming_id: str):
    def license_request_fn(challenge: bytes, item: dict):
        drm_type = item.get("type", "").lower() if isinstance(item, dict) else ""
        is_playready = drm_type == "playready"

        # Chili's license endpoint is on the flux-capacitor API with sessionId.
        # Use the streaming session ID stored on the client if available.
        session_id = getattr(client, "_streaming_session_id", None)
        market = getattr(client, "market", "it")
        if session_id:
            drm_path = "playready" if is_playready else "widevine"
            license_url = f"https://{market}.chili.com/flux-capacitor/api/v1/licensing/{drm_path}?sessionId={session_id}"
        else:
            # Fallback: try la_url from PSSH, then hardcoded URL
            license_url = item.get("la_url") if isinstance(item, dict) else None
            if is_playready and not license_url:
                try:
                    from pyplayready.system.pssh import PSSH as _PSSH
                    pssh_b64 = item.get("pssh", "")
                    if pssh_b64:
                        pssh_obj = _PSSH(pssh_b64)
                        if pssh_obj.wrm_headers:
                            license_url = pssh_obj.wrm_headers[0].la_url
                except Exception:
                    pass
            if license_url:
                license_url = license_url.replace("playready.chili.tv", "playready.chili.com")
            if not license_url:
                license_url = api.PLAYREADY_LICENSE_URL_AVOD if is_playready else api.WIDEVINE_LICENSE_URL_AVOD

        if is_playready:
            # PlayReady: challenge is a SOAP envelope (keep XML declaration)
            challenge_str = challenge.decode("utf-8") if isinstance(challenge, bytes) else challenge

            # Try to get Bearer token for Disney+ style access, but sessionId is the
            # primary auth for the flux-capacitor license endpoint
            try:
                bearer_token = client._get_gatekeeper_token()
            except Exception:
                bearer_token = None

            headers = {
                "Origin": api.ORIGIN,
                "Referer": api.REFERER,
                "User-Agent": api.get_userAgent(),
                "Content-Type": "text/xml; charset=utf-8",
                "soapaction": '"http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense"',
            }
            if bearer_token:
                headers["Authorization"] = f"Bearer {bearer_token}"

            resp = client._session.post(
                license_url,
                data=challenge_str.encode("utf-8"),
                headers=headers,
                timeout=60,
                allow_redirects=False,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Chili PlayReady license failed: {resp.status_code} - {resp.text[:500]}"
                )
            return resp.text
        else:
            # Widevine: challenge is protobuf
            try:
                bearer_token = client._get_gatekeeper_token()
            except Exception:
                bearer_token = None

            headers = {
                "Origin": api.ORIGIN,
                "Referer": api.REFERER,
                "User-Agent": api.get_userAgent(),
                "Content-Type": "application/octet-stream",
            }
            if bearer_token:
                headers["Authorization"] = f"Bearer {bearer_token}"

            resp = client._session.post(
                license_url,
                data=challenge,
                headers=headers,
                timeout=60,
                allow_redirects=False,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Chili Widevine license failed: {resp.status_code} - {resp.content[:500]}"
                )
            return resp.content

    return license_request_fn


def _dash_download(
    mpd_url: str,
    license_url: str | None,
    streaming_id: str,
    output_path: str,
    subs: list,
    drm_type: str | None = None,
    cookies: dict | None = None,
    client: object | None = None,
):
    if not license_url:
        console.print(
            "[red]CHILI returned the DASH manifest but no DRM license URL; refusing to guess a license endpoint."
        )
        return DownloadResult(None, True, "CHILI did not return a DRM license URL")

    # Detect DRM type from stream info, prefer Widevine if available
    drm_info = (drm_type or "widevine").lower() if drm_type else "widevine"
    is_playready = drm_info == "playready"
    drm = DRMType.PLAYREADY if is_playready else DRMType.WIDEVINE

    # Create license request function that uses client session with cookies
    license_request_fn = None
    if client is not None:
        license_request_fn = _make_chili_license_request_fn(client, streaming_id)

    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_headers=api.mpd_headers(),
        output_path=output_path,
        license_url=license_url,
        license_request_fn=license_request_fn,
        drm_preference=drm,
        other_tracks=subs or None,
    ).start()


def _movie_detail(movie_id: str, market: str | None) -> dict:
    try:
        return api.get_movie_detail(movie_id, market)
    except Exception as e:
        console.print(f"[yellow]Detail lookup failed for '{movie_id}': {e} — trying AVOD.")
        return {}


def download_film(select_title: Entries) -> DownloadResult:
    start_message()
    print_download_header(select_title)

    content_id = getattr(select_title, "id", None) or getattr(select_title, "slug", None)
    if not content_id:
        console.print("[red]Error: missing Chili content id.")
        return DownloadResult(None, True)

    market = getattr(select_title, "market", None)
    detail = _movie_detail(content_id, market)
    kind = getattr(select_title, "kind", None) or (api.purchase_kind(detail) if detail else "avod")
    if kind != "avod":
        console.print(f"[cyan]Title kind: {kind} — using authenticated /me streaming.")

    try:
        if kind == "avod":
            opts = _options()
            stream = api.get_avod_streaming(
                content_id,
                "movies",
                api.audio_language(market, detail, opts.get("audio_lang")),
                (opts.get("quality") or "FHD").upper(),
                (opts.get("device") or "lgui40").lower(),
                market=market,
            )
        else:
            stream = api.get_me_streaming(content_id, "movies", market=market)
    except Exception as e:
        console.print(f"[red]Error getting stream: {e}")
        return DownloadResult(None, True)

    path_components, filename = map_movie_path(select_title.name, getattr(select_title, "year", None))
    output_path = os.path.join(movie_folder(*path_components), f"{filename}.{extension_output}")
    client = api._get_client(market)
    return _dash_download(
        stream["url"],
        stream.get("license_url"),
        api.streaming_uuid(stream),
        output_path,
        _subtitle_tracks(stream),
        stream.get("drm"),
        client=client,
    )


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    start_message()
    print_episode_header(scrape_serie.series_name, obj_episode.name, index_season_selected, index_episode_selected)

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    output_path = os.path.join(series_folder(*path_components), f"{filename}.{extension_output}")
    kind = getattr(scrape_serie, "kind", "avod")
    market = getattr(scrape_serie, "market", None)
    client = api._get_client(market)
    try:
        if kind == "avod":
            opts = _options()
            audio = (
                api.audio_language(market, None, opts.get("audio_lang"))
                if opts.get("audio_lang")
                else (getattr(scrape_serie, "audio", None) or api.audio_language(market))
            )
            stream = api.get_avod_streaming(
                obj_episode.id,
                "episodes",
                audio,
                (opts.get("quality") or "FHD").upper(),
                (opts.get("device") or "lgui40").lower(),
                market=market,
            )
        else:
            stream = api.get_me_streaming(obj_episode.id, "episodes", market=market)
    except Exception as e:
        console.print(f"[red]Error getting stream: {e}")
        return DownloadResult(None, True)

    return _dash_download(
        stream["url"],
        stream.get("license_url"),
        api.streaming_uuid(stream),
        output_path,
        _subtitle_tracks(stream),
        stream.get("drm"),
        client=client,
    )


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(
            getattr(select_season, "id", None) or select_season.slug,
            select_season.name,
            getattr(select_season, "year", None),
            getattr(select_season, "market", None),
        )
        scrape_serie.getNumberSeason()
        scrape_serie.load_stream_info()
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
