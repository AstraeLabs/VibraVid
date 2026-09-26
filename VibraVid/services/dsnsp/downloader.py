# 21.09.26

import base64
import logging
import os
from uuid import UUID

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, movie_folder, series_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message

from .client import get_client
from .scrapper import GetContentInfo, GetPageInfo, GetSerieInfo

msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension", default="mkv")

# Disney pubblica centinaia di varianti a risoluzione, codec e bitrate diversi: la tabella
# mostra solo le tracce che lo StreamSelector sceglie davvero, una per gruppo di filtri
# (video, lingua audio, lingua/flag sottotitoli). Cosi' la tabella e' l'anteprioma della
# selezione: quello che si vede e' esattamente quello che verra' scaricato, e resta
# coerente anche quando per una lingua l'unica traccia disponibile e' stereo.
DISPLAY_SELECTED_ONLY = True


def _drm_type() -> str:
    opts = getattr(context_tracker, "site_options", None) or {}
    return opts.get("drm_type") or "pr"



def _get_playback_info(client, media_id: str) -> dict:
    """Get playback info from the client."""
    try:
        return client.get_playback_info(media_id, drm_type=_drm_type())
    except Exception as e:
        logger.error(f"Error getting playback info: {e}")
        return {"manifest": None, "license": None, "license_headers": {}}


def _get_drm_preference(playback_info: dict) -> DRMType:
    """Get DRM preference from playback info."""
    return playback_info.get("drm_type") or DRMType.PLAYREADY


def _make_license_request_fn(playback_info: dict, client):
    """Create a license request function for PlayReady SOAP and Widevine."""
    drm_type = playback_info.get("drm_type", "")
    license_url = playback_info.get("license", "")
    license_headers = playback_info.get("license_headers", {})

    if not license_url:
        return None

    def license_request_fn(challenge: bytes, request_headers: dict) -> bytes:
        headers = {
            "Content-Type": "application/octet-stream",
            **license_headers,
        }
        if drm_type == DRMType.PLAYREADY:
            headers.update({
                "Accept": "application/xml, application/vnd.media-service+json; version=2",
                "SOAPAction": "http://schemas.microsoft.com/DRM/2007/03/protocols/AcquireLicense",
            })
            soap_body = f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><AcquireLicense xmlns="http://schemas.microsoft.com/DRM/2007/03/protocols"><Challenge>{challenge.hex()}</Challenge></AcquireLicense></s:Body></s:Envelope>'
            payload = soap_body.encode("utf-8")
        else:
            payload = challenge

        resp = client.sdk.session.post(license_url, headers=headers, data=payload)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            logger.error(f"License request failed: {resp.status_code} {license_url}")
            logger.error(f"Request headers: {headers}")
            logger.error(f"Response: {body}")
            raise RuntimeError(f"License request failed ({resp.status_code}): {body}")
        return resp.content

    return license_request_fn


def _playready_pssh_kids(pssh: str) -> set[str]:
    """KID dichiarati dentro un PSSH PlayReady, in esadecimale lowercase."""
    try:
        from pyplayready.system.pssh import PSSH

        return {kid.value.hex.lower() for header in PSSH(pssh).wrm_headers for kid in header.key_ids}
    except Exception as exc:
        logger.debug(f"DSNSP: lettura KID dal PSSH PlayReady fallita: {exc}")
        return set()


def _rebuild_playready_pssh(pssh: str, kid_hex: str) -> str | None:
    """Riscrive il PSSH PlayReady del manifest sul KID della traccia che si sta licenziando.

    Ogni PSSH PlayReady dichiara un solo KID dentro il WRM header, mentre il ladder
    Disney assegna un KID diverso a ogni traccia: chiedere le chiavi inviando il
    PSSH del manifest fa rispondere `content-key.not-found` per tutti i KID che
    il PSSH non nomina, e le tracce restano senza chiave (fallisce la decifratura).
    Il valore di `<KID>` e' un UUID serializzato little-endian in base64 e occupa
    sempre la stessa lunghezza, quindi sostituirlo in byte lascia valide tutte le
    lunghezze dichiarate nel PSSH.
    """
    if not pssh or not kid_hex:
        return None

    kid = kid_hex.replace("-", "").strip().lower()
    if not kid or kid == "n/a":
        return None

    try:
        data = base64.b64decode(pssh)
    except Exception as exc:
        logger.debug(f"DSNSP: PSSH PlayReady non decodificabile: {exc}")
        return None

    open_tag, close_tag = "<KID>".encode("utf-16-le"), "</KID>".encode("utf-16-le")
    start = data.find(open_tag)
    if start < 0:
        return None
    value_start = start + len(open_tag)
    end = data.find(close_tag, value_start)
    if end < 0:
        return None

    try:
        current = data[value_start:end].decode("utf-16-le")
        replacement = base64.b64encode(UUID(hex=kid).bytes_le).decode()
    except Exception as exc:
        logger.debug(f"DSNSP: sostituzione KID nel PSSH non riuscita: {exc}")
        return None

    if len(replacement) != len(current):
        logger.debug(f"DSNSP: KID nel PSSH di lunghezza inattesa ({len(current)}), PSSH lasciato invariato")
        return None

    return base64.b64encode(data[:value_start] + replacement.encode("utf-16-le") + data[end:]).decode()


def _pssh_list_for_track_kids(pssh_list: list[dict] | None) -> list[dict]:
    """Allinea ogni PSSH PlayReady al KID della sua entry, quando non lo dichiara già."""
    aligned: list[dict] = []
    rewritten = 0

    for item in pssh_list or []:
        if not isinstance(item, dict):
            aligned.append(item)
            continue

        pssh = item.get("pssh")
        kid = str(item.get("kid") or "").replace("-", "").strip().lower()
        declared = _playready_pssh_kids(pssh) if pssh else set()

        if pssh and kid and kid != "n/a" and declared and kid not in declared:
            rebuilt = _rebuild_playready_pssh(pssh, kid)
            if rebuilt and rebuilt != pssh:
                item = {**item, "pssh": rebuilt}
                rewritten += 1

        aligned.append(item)

    if rewritten:
        logger.info(f"DSNSP: PSSH PlayReady rigenerato su {rewritten} KID di traccia")
        console.print(f"[cyan]PlayReady: [yellow]{rewritten}[/] PSSH rigenerate sui KID delle tracce")

    return aligned


def _install_playready_pssh_alignment() -> None:
    """Registra l'allineamento dei PSSH PlayReady solo per questo servizio.

    Il DRM manager risolve `get_playready_keys` al momento della chiamata, quindi
    basta sostituirne il riferimento nel suo namespace per correggere le richieste
    di licenza senza toccare il core, che resta condiviso con gli altri servizi.
    """
    try:
        from VibraVid.core.drm import manager as drm_manager
    except Exception as exc:
        logger.debug(f"DSNSP: import DRM manager non riuscito: {exc}")
        return

    if getattr(drm_manager, "_dsnsp_pssh_alignment", False):
        return

    original = drm_manager.get_playready_keys

    def get_playready_keys_with_aligned_pssh(pssh_list, *args, **kwargs):
        return original(_pssh_list_for_track_kids(pssh_list), *args, **kwargs)

    drm_manager.get_playready_keys = get_playready_keys_with_aligned_pssh
    drm_manager._dsnsp_pssh_alignment = True
    logger.debug("DSNSP: allineamento PSSH PlayReady ai KID delle tracce attivo")


def _download_playback(playback_info: dict, output_path: str):
    manifest = playback_info["manifest"]
    license_url = playback_info.get("license", "")
    license_headers = playback_info.get("license_headers", {})
    drm_preference = _get_drm_preference(playback_info)

    client = get_client()
    license_fn = _make_license_request_fn(playback_info, client) if license_url else None

    if _drm_type() == "pr" or _get_drm_preference(playback_info) == DRMType.PLAYREADY:
        _install_playready_pssh_alignment()

    if ".m3u8" in manifest.lower():
        return HLS_Downloader(
            m3u8_url=manifest,
            output_path=output_path,
            license_url=license_url or None,
            license_headers=license_headers,
            has_drm=bool(license_url),
            drm_preference=drm_preference,
            display_selected_only=DISPLAY_SELECTED_ONLY,
        ).start()

    dash_downloader = DASH_Downloader(
        mpd_url=manifest,
        license_url=license_url,
        license_headers=license_headers,
        output_path=output_path,
        drm_preference=drm_preference,
        license_request_fn=license_fn,
    )
    return dash_downloader.start()


def download_film(select_title: Entries):
    """Download a film."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name}\n")

    client = get_client()
    content_info = GetContentInfo(client, select_title.id)

    if content_info.get_type() != "movie":
        console.print("[red]Error: Selected title is not a movie.")
        return False

    page_info = GetPageInfo(client, select_title.id)
    visuals = page_info.get_visuals()
    name = visuals.get("title", select_title.name)
    year = visuals.get("metastringParts", {}).get("releaseYearRange", {}).get("startYear")

    actions = page_info.data.get("actions", []) if page_info.data else []
    playback = next((a for a in actions if a.get("type") == "playback"), None)
    media_id = playback.get("resourceId") if playback else None

    if not media_id:
        console.print("[red]Error: Could not get media ID for playback")
        return False

    playback_info = _get_playback_info(client, media_id)
    if not playback_info.get("manifest"):
        console.print("[red]Error: Could not get manifest URL")
        return False

    path_components, filename = map_movie_path(name, year)
    movie_path = os_manager.get_sanitize_path(movie_folder(*path_components))
    movie_name = f"{filename}.{extension_output}"

    return _download_playback(playback_info, os.path.join(movie_path, movie_name))


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """Download a series."""
    start_message()
    if not scrape_serie:
        scrape_serie = GetSerieInfo(get_client(), select_season.id)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
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


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """Download a specific episode."""
    start_message()
    client = get_client()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        scrape_serie.year,
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    episode_path = os_manager.get_sanitize_path(series_folder(*path_components))
    episode_name = f"{filename}.{extension_output}"

    media_id = getattr(obj_episode, "video_id", None) or obj_episode.id

    if not media_id:
        console.print("[red]Error: Could not get media ID for episode")
        return False

    playback_info = _get_playback_info(client, media_id)
    if not playback_info.get("manifest"):
        console.print("[red]Error: Could not get manifest URL for episode")
        return False

    return _download_playback(playback_info, os.path.join(episode_path, episode_name))
