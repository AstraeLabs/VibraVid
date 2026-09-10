# 05.01.26

import logging
import os
import shutil
import time
from collections.abc import Callable

from rich.console import Console

from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType, _DRMSystems
from VibraVid.core.manifest.custom import is_custom_manifest
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import track_label
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.ui.ui import build_table
from VibraVid.core.utils.language import resolve_iso639_1
from VibraVid.core.utils.media_players import MediaPlayers
from VibraVid.core.velora.downloader import MediaDownloader
from VibraVid.core.velora.util.formatting import (
    parse_max_segments as _parse_max_segments,
)
from VibraVid.core.velora.util.formatting import (
    parse_max_time as _parse_max_time,
)
from VibraVid.setup import get_prd_path, get_wvd_path, resolve_service_cdm_paths
from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import create_client, get_headers

from .base import BaseDownloader, DownloadResult
from .util._drm_probe import PROBE_BYTES_FAST, DRMProbe

console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
AUDIO_FILTER = config_manager.config.get("DOWNLOAD", "select_audio")
SUBTITLE_FILTER = config_manager.config.get("DOWNLOAD", "select_subtitle")
DELAY_SS = config_manager.config.get_int("DOWNLOAD", "delay_after_download")


def _stream_drm_label(s) -> str:
    """Build a human-readable track label for DRM reporting."""
    return track_label(s)


def _filter_subtitles(sub_list: list, filter_str: str) -> list:
    """
    Filter subtitle list based on the filter string. The filter string can be:
    """
    if not sub_list:
        return []
    if not filter_str or filter_str.lower() in ("false",):
        return []
    if filter_str.lower() == "all":
        return sub_list

    wanted_locales = set()
    for token in filter_str.replace("|", ",").split(","):
        token = token.strip()
        if not token:
            continue
        
        token = token.lower()
        wanted_locales.add(token)
        iso2 = resolve_iso639_1(token)
        if iso2:
            wanted_locales.add(iso2)

    if not wanted_locales:
        return sub_list

    filtered = []
    for s in sub_list:
        lang = (s.get("language") or "").strip().lower()
        if not lang:
            continue
        lang_iso2 = resolve_iso639_1(lang)

        # Check exact match first (raw code, or normalized ISO 639-1, e.g. 'ita' -> 'it')
        if lang in wanted_locales or (lang_iso2 and lang_iso2 in wanted_locales):
            filtered.append(s)
            continue

        # Check prefix match (e.g., 'it' matches 'it-it')
        for token in wanted_locales:
            if lang.startswith(token + "-") or lang == token:
                filtered.append(s)
                break

    return filtered


def _other_track_kind(track_type: str) -> str:
    raw = (track_type or "").strip().lower()
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    if raw in ("sub", "subtitle", "subtitles"):
        return "subtitle"
    if raw in ("aud", "audio"):
        return "audio"
    if raw in ("vid", "video"):
        return "video"
    return raw


def _other_track_tag(track_type: str) -> str:
    raw = (track_type or "").strip().lower()
    if ":" in raw:
        return raw.split(":", 1)[1]
    return ""


def _is_dash_audio_track(track: dict) -> bool:
    if _other_track_kind(track.get("type", "")) != "audio":
        return False

    manifest_type = (track.get("manifest") or track.get("manifest_type") or "").strip().lower()
    if manifest_type == "dash":
        return True

    url = str(track.get("url") or "")
    path_no_query = url.split("?", 1)[0].lower()
    if path_no_query.endswith(".mpd"):
        return True

    if track.get("license_url") or track.get("license_headers"):
        return True

    return False


def _to_external_subtitle_track(track: dict) -> dict:
    normalized = dict(track or {})

    fmt = str(normalized.get("extension") or normalized.get("format") or "").strip().lower().lstrip(".")
    if fmt:
        normalized.setdefault("extension", fmt)
        normalized.setdefault("type", fmt)

    if "closed_caption" in normalized and "cc" not in normalized:
        normalized["cc"] = bool(normalized.get("closed_caption"))

    if normalized.get("label") and not normalized.get("name"):
        normalized["name"] = normalized.get("label")

    normalized.setdefault("language", "und")
    return normalized


class DASH_Downloader(BaseDownloader):
    def __init__(
        self,
        mpd_url: str | None = None,
        mpd_content: str | None = None,
        mpd_headers: dict[str, str] | None = None,
        manifest_refresh_fn: Callable[[], str | None] | None = None,
        license_url: str | None = None,
        license_headers: dict[str, str] | None = None,
        license_certificate: str | None = None,
        license_data: str | None = None,
        output_path: str | None = None,
        drm_preference=DRMType.WIDEVINE,
        key: str | None = None,
        cookies: dict[str, str] | None = None,
        max_segments: int | None = None,
        max_time=None,
        other_tracks: list | None = None,
        license_request_fn: Callable[[bytes, dict], bytes] | None = None,
        chapters: list | None = None,
        poster_url: str | None = None,
        sanitize_path: bool = True,
    ):
        """
        Parameters:
            - mpd_url: DASH MPD manifest URL.
            - mpd_content: Content of the MPD manifest already downloaded (string). If provided, skips the HTTP fetch.
            - mpd_headers: HTTP headers for MPD requests.
            - manifest_refresh_fn: Optional function to call to refresh the manifest content during download. Should return the new manifest content as a string, or None to keep using the original.
            - license_url: DRM license server URL for Widevine/PlayReady.
            - license_request_fn: Optional callback (challenge bytes -> license bytes) for services whose license endpoint is a custom signed API call instead of a plain POST.
            - license_headers: HTTP headers for DRM license requests.
            - license_certificate: Widevine certificate (base64) for license challenge.
            - license_data: PlayReady license data for SOAP envelope.
            - output_path: Output file path. Default: "download.{EXTENSION_OUTPUT}".
            - key: Manual decryption key (hex format) if known.
            - cookies: HTTP cookies for authenticated requests.
            - max_segments: Maximum number of segments to download (for testing). Default: None (all).
            - max_time: Maximum content duration to download, e.g. "01:00:00" or 3600 seconds. Default: None (all).
            - chapters: Chapter markers to inject into the muxed output, e.g. [{"name": str, "seconds": int}]. Default: context_tracker.chapters.
            - poster_url: Poster/still image URL to embed in the muxed output. Default: context_tracker.poster_url.
        """
        self.chapters = chapters if chapters is not None else context_tracker.chapters
        self.poster_url = context_tracker.poster_url or poster_url or context_tracker.fallback_poster_url
        context_tracker.poster_url = self.poster_url
        context_tracker.poster_url = self.poster_url
        self.mpd_url = self._resolve_url(str(mpd_url).strip()) if mpd_url else None
        self.mpd_content = mpd_content
        self.mpd_headers = mpd_headers or get_headers()
        self.manifest_refresh_fn = manifest_refresh_fn
        self.other_tracks = [dict(track or {}) for track in (other_tracks or [])]

        self._subtitle_tracks: list[dict] = []
        self._dash_audio_tracks: list[dict] = []
        self._merge_other_tracks: list[dict] = []

        for track in self.other_tracks:
            kind = _other_track_kind(track.get("type", ""))
            if kind == "subtitle":
                self._subtitle_tracks.append(track)
                continue
            if kind == "audio" and _is_dash_audio_track(track):
                self._dash_audio_tracks.append(track)
                continue
            self._merge_other_tracks.append(track)

        self.license_url = str(license_url).strip() if license_url else None
        self.license_request_fn = license_request_fn
        self.license_headers = license_headers
        self.license_certificate = license_certificate
        self.license_data = license_data
        logger.info(f"DASH Downloader initialized with MPD URL: {self.mpd_url}, License URL: {self.license_url}, DRM Preference: {drm_preference}, Key provided: {'yes' if key else 'no'}")

        self.drm_preference = drm_preference
        self.key = key
        self.cookies = cookies or {}
        self.max_segments = _parse_max_segments(
            max_segments if max_segments is not None else context_tracker.max_segments
        )
        self.max_time = _parse_max_time(max_time if max_time is not None else context_tracker.max_time)
        wvd_override, prd_override = resolve_service_cdm_paths(context_tracker.site_name)
        self.drm_manager = DRMManager(
            wvd_override or get_wvd_path(),
            prd_override or get_prd_path(),
            config_manager.config.get_dict("DRM", "widevine", default={}),
            config_manager.config.get_dict("DRM", "playready", default={}),
            config_manager.config.get_bool("DRM", "prefer_remote_cdm"),
        )

        super().__init__(output_path, "_dash_temp", sanitize_path=sanitize_path)

        self.decryption_keys = []
        self.media_downloader = None
        self.custom_filters: dict | None = None
        self.display_min_video_height: int | None = None
        self.display_only_drm_video = False
        self.display_only_drm_audio = False
        self._probe = DRMProbe()

    def _collect_drm_from_streams(self, streams: list, check_selected: bool = True) -> dict[str, list[dict]]:
        """
        Read PSSH data directly from Stream.drm (DRMInfo) on selected streams.

        Args:
            streams: List of Stream objects
            check_selected: If True, only collect from streams with selected=True.
                        If False, collect from all streams with DRM (used for fallback).

        Returns:
            {
                DRMType.WIDEVINE: [{'pssh': ..., 'kid': ..., 'type': 'Widevine', 'label': ...}, ...],
                DRMType.PLAYREADY: [{'pssh': ..., 'kid': ..., 'type': 'PlayReady', 'label': ...}, ...],
            }
        """
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: []}
        seen: dict[str, set] = {DRMType.WIDEVINE: set(), DRMType.PLAYREADY: set()}
        kid_labels: dict[str, list] = {}

        for s in streams:
            drm = getattr(s, "drm", None)
            is_encrypted = drm and drm.is_encrypted()
            is_selected = getattr(s, "selected", False)

            # If check_selected=True, require selected=True AND encrypted
            # If check_selected=False, just require encrypted (for fallback from MPD)
            if check_selected:
                if not (is_selected and is_encrypted):
                    continue
            else:
                if not is_encrypted:
                    continue

            label = _stream_drm_label(s)
            collected_kids: set = set()
            collected_dts: list = []

            for dt in drm.get_all_drm_types():  # DRMType.WIDEVINE, DRMType.PLAYREADY, DRMType.FAIRPLAY, DRMType.UNKNOWN
                if dt not in result:
                    continue

                kids = []
                if hasattr(drm, "get_all_kids"):
                    kids = [k for k in drm.get_all_kids() if k and k != "N/A"]
                else:
                    for kid_attr in ("kid", "default_kid"):
                        kid = getattr(drm, kid_attr, None)
                        if kid and kid != "N/A" and kid not in kids:
                            kids.append(kid)

                if not kids:
                    kids = ["N/A"]

                psshs = drm.get_all_pssh_for(dt) or ([drm.get_pssh_for(dt)] if drm.get_pssh_for(dt) else [])
                if not psshs and dt == DRMType.WIDEVINE:
                    synth_kids = [k for k in kids if k and k != "N/A"]
                    for kid_val in synth_kids:
                        try:
                            synth = _DRMSystems.build_widevine_pssh_from_kid(kid_val)
                            if synth not in psshs:
                                psshs.append(synth)
                            logger.info(f"DASH: synthesized Widevine PSSH from KID {kid_val}")
                        except Exception as exc:
                            logger.debug(f"DASH: Widevine PSSH synthesis failed for {kid_val}: {exc}")

                if not psshs:
                    logger.warning("No PSSH found for this stream's DRM, skipping...")
                    continue

                dt_added = False
                for pssh in psshs:
                    for kid in kids:
                        if kid and kid != "N/A" and label:
                            kid_norm = kid.replace("-", "").strip().lower()
                            labels = kid_labels.setdefault(kid_norm, [])
                            if label not in labels:
                                labels.append(label)

                        dedup_key = (pssh, kid)
                        if dedup_key in seen[dt]:
                            continue

                        seen[dt].add(dedup_key)
                        collected_kids.add(kid)
                        if not dt_added:
                            collected_dts.append(str(dt))
                            dt_added = True

                        result[dt].append(
                            {
                                "pssh": pssh,
                                "kid": kid,
                                "type": "Widevine" if dt == DRMType.WIDEVINE else "PlayReady",
                                "label": label,
                            }
                        )

        # Merge every selected track's label onto each surviving entry so a key
        # shared across tracks is stored with the full quality it unlocks
        # (e.g. "video 2160p + audio IT" instead of only the first track's label).
        for entries in result.values():
            for entry in entries:
                kid_norm = (entry.get("kid") or "").replace("-", "").strip().lower()
                merged = kid_labels.get(kid_norm)
                if merged:
                    entry["label"] = " + ".join(merged)

        return result

    def _collect_drm_from_mpd(self, raw_mpd_path: str | None) -> dict[str, list[dict]]:
        """Fallback: scan the saved raw .mpd via DashParser to extract PSSH."""
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: []}
        try:
            logger.info(f"_collect_drm_from_mpd: Attempting fallback DRM extraction from raw_mpd_path={raw_mpd_path}")
            if raw_mpd_path and os.path.exists(raw_mpd_path):
                with open(raw_mpd_path, encoding="utf-8") as f:
                    content = f.read()
                parser = DashParser(self.mpd_url, headers=self.mpd_headers, content=content)
            else:
                parser = DashParser(self.mpd_url, headers=self.mpd_headers)

            # content= only stages raw_content/_injected - fetch_manifest() is what actually parses it into _root
            if not parser.fetch_manifest():
                return result

            streams = parser.parse_streams()
            logger.info(f"_collect_drm_from_mpd: Re-parsed MPD returned {len(streams)} streams")

            # Fallback collection: don't check selected status (streams are freshly parsed)
            result = self._collect_drm_from_streams(streams, check_selected=False)

            wv_count = len(result.get(DRMType.WIDEVINE, []))
            pr_count = len(result.get(DRMType.PLAYREADY, []))
            logger.info(f"_collect_drm_from_mpd: Collected {wv_count} WV PSSH + {pr_count} PR PSSH")

        except Exception as exc:
            logger.info(f"_collect_drm_from_mpd error: {exc}")

        return result

    def _collect_drm_from_init_segments(self, streams: list) -> dict[str, list[dict]]:
        """Last resort: probe the init segments of selected streams to extract KID/PSSH."""
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: []}

        candidates = [s for s in streams if getattr(s, "selected", False) and s.type in ("video", "audio")]
        if not candidates:
            return result

        with create_client(headers=self.mpd_headers) as client:
            for s in candidates:
                init_seg = next((seg for seg in s.segments if seg.seg_type == "init"), None)
                if not init_seg:
                    continue

                encrypted, _scheme, _is_widevine, kid, pssh_b64 = self._probe.probe(
                    init_seg.url, self.mpd_headers, client, size=PROBE_BYTES_FAST
                )
                if not encrypted or not kid:
                    continue

                if not pssh_b64:
                    try:
                        pssh_b64 = _DRMSystems.build_widevine_pssh_from_kid(kid)
                        logger.info(f"DASH: synthesized Widevine PSSH from init-segment KID {kid}")
                    except Exception as exc:
                        logger.debug(f"DASH: Widevine PSSH synthesis failed for {kid}: {exc}")
                        continue

                s.drm.set_pssh(pssh_b64, drm_type_hint=DRMType.WIDEVINE)
                s.drm.set_kid(kid)

                label = _stream_drm_label(s)
                result[DRMType.WIDEVINE].append({"pssh": pssh_b64, "kid": kid, "type": "Widevine", "label": label})
                logger.info(f"DASH DRM recovered from init segment: {s.id or 'unnamed'} | type={s.type} | KID={kid}")

        return result

    def _warn_drm_mismatch(self, drm_psshs: dict[str, list[dict]]) -> None:
        """
        Print a warning if the manifest contains only the DRM type that is NOT
        the requested drm_preference (and nothing for the preferred type).
        """
        has_wv = bool(drm_psshs.get(DRMType.WIDEVINE))
        has_pr = bool(drm_psshs.get(DRMType.PLAYREADY))

        if self.drm_preference == DRMType.WIDEVINE and not has_wv and has_pr:
            logger.warning("DRM mismatch: preference=widevine but only PlayReady PSSH found.")
        elif self.drm_preference == DRMType.PLAYREADY and not has_pr and has_wv:
            logger.warning("DRM mismatch: preference=playready but only Widevine PSSH found.")

    def _fetch_keys(self, drm_psshs: dict[str, list[dict]]) -> list[str]:
        """Dispatch key fetch to DRMManager using the configured drm_preference.

        When the manifest only carries the *other* DRM type (see
        ``_warn_drm_mismatch``), fall back to that type instead of skipping key
        resolution entirely -- otherwise a manually-provided key never reaches
        DRMManager and is used "blind"
        """
        keys = None
        effective_pref = self.drm_preference
        if not drm_psshs.get(effective_pref):
            other = DRMType.PLAYREADY if effective_pref == DRMType.WIDEVINE else DRMType.WIDEVINE
            if drm_psshs.get(other):
                effective_pref = other

        if effective_pref == DRMType.WIDEVINE and drm_psshs.get(DRMType.WIDEVINE):
            keys = self.drm_manager.get_wv_keys(
                drm_psshs[DRMType.WIDEVINE],
                self.license_url,
                self.license_data,
                self.license_certificate,
                self.license_headers,
                self.key,
                license_request_fn=self.license_request_fn,
            )

        if effective_pref == DRMType.PLAYREADY and drm_psshs.get(DRMType.PLAYREADY):
            keys = self.drm_manager.get_pr_keys(
                drm_psshs[DRMType.PLAYREADY],
                self.license_url,
                self.license_headers,
                self.key,
                self.license_data,
                license_request_fn=self.license_request_fn,
            )

        # Final fallback: use a manually provided key
        if not keys and self.key:
            keys = [self.key] if isinstance(self.key, str) else list(self.key)

        return keys or []

    def _fetch_keys_for_audio_mpd(
        self,
        audio_url: str,
        audio_headers: dict,
        raw_mpd_path: str | None,
        streams: list,
        license_url: str | None = None,
        license_hdrs: dict | None = None,
    ) -> list[str]:
        """Fetch DRM keys for an extra-audio MPD. Primary: Stream.drm; fallback: DashParser."""
        drm_psshs = self._collect_drm_from_streams(streams)

        if not drm_psshs[DRMType.WIDEVINE] and not drm_psshs[DRMType.PLAYREADY]:
            try:
                if raw_mpd_path and os.path.exists(raw_mpd_path):
                    with open(raw_mpd_path, encoding="utf-8") as f:
                        content = f.read()
                    parser = DashParser(audio_url, headers=audio_headers, content=content)
                else:
                    parser = DashParser(audio_url, headers=audio_headers)
                    parser.fetch_manifest()

                extra_streams = parser.parse_streams()
                extra_drm = self._collect_drm_from_streams(extra_streams)

                for e in extra_drm.get(DRMType.WIDEVINE, []):
                    drm_psshs[DRMType.WIDEVINE].append(e)
                for e in extra_drm.get(DRMType.PLAYREADY, []):
                    drm_psshs[DRMType.PLAYREADY].append(e)

            except Exception as exc:
                logger.error(f"Audio DashParser fallback: {exc}")

        if not drm_psshs[DRMType.WIDEVINE] and not drm_psshs[DRMType.PLAYREADY]:
            return []

        self._warn_drm_mismatch(drm_psshs)
        eff_url = license_url or self.license_url
        eff_hdrs = license_hdrs or self.license_headers

        keys = None
        effective_pref = self.drm_preference
        if not drm_psshs.get(effective_pref):
            other = DRMType.PLAYREADY if effective_pref == DRMType.WIDEVINE else DRMType.WIDEVINE
            if drm_psshs.get(other):
                effective_pref = other

        if effective_pref == DRMType.WIDEVINE and drm_psshs.get(DRMType.WIDEVINE):
            keys = self.drm_manager.get_wv_keys(
                drm_psshs[DRMType.WIDEVINE],
                eff_url,
                license_certificate=self.license_certificate,
                headers=eff_hdrs,
                key=self.key,
                license_request_fn=self.license_request_fn,
            )

        elif effective_pref == DRMType.PLAYREADY and drm_psshs.get(DRMType.PLAYREADY):
            keys = self.drm_manager.get_pr_keys(
                drm_psshs[DRMType.PLAYREADY],
                eff_url,
                headers=eff_hdrs,
                key=self.key,
                license_data=self.license_data,
                license_request_fn=self.license_request_fn,
            )

        return keys or []

    def _download_extra_audios(self) -> tuple[list[dict], list[dict]]:
        """Download extra DASH audio tracks from ``other_tracks`` audio entries."""
        external_audios: list[dict] = []
        external_subtitles: list[dict] = []

        for audio_spec in self._dash_audio_tracks:
            audio_url = audio_spec.get("url")
            audio_language = audio_spec.get("language") or _other_track_tag(audio_spec.get("type", "")) or "und"
            audio_headers = audio_spec.get("headers") or self.mpd_headers
            audio_license_url = audio_spec.get("license_url")
            audio_license_headers = audio_spec.get("license_headers")

            if not audio_url:
                console.print(f"[yellow]Skipping extra audio '{audio_language}': missing url")
                continue

            audio_temp_dir = os.path.join(self.output_dir, f"audio_{audio_language}_temp")
            os_manager.create_path(audio_temp_dir)

            try:
                audio_dl = MediaDownloader(
                    url=audio_url,
                    output_dir=audio_temp_dir,
                    filename=self.filename_base,
                    headers=audio_headers,
                    cookies=self.cookies,
                    download_id=None,
                    site_name=self.site_name,
                    max_segments=self.max_segments,
                )
                audio_dl.custom_filters = {
                    "video": "false",
                    "audio": "best",
                    "subtitle": (self.custom_filters or {}).get("subtitle") or SUBTITLE_FILTER,
                }

                if self.download_id:
                    download_tracker.update_status(self.download_id, f"Parsing audio {audio_language}...")
                console.print(f"\n[dim]Parsing DASH for audio {audio_language} ...")
                audio_streams = audio_dl.parse_stream(show_table=False)

                _, raw_mpd_str, _ = audio_dl.get_metadata()
                raw_mpd = raw_mpd_str if raw_mpd_str and raw_mpd_str != "None" else None

                audio_keys = self._fetch_keys_for_audio_mpd(
                    audio_url,
                    audio_headers,
                    raw_mpd,
                    audio_streams,
                    license_url=audio_license_url,
                    license_hdrs=audio_license_headers,
                )

                if not audio_keys:
                    console.print(f"[yellow]No keys for audio {audio_language}, skipping...")
                    continue

                audio_dl.set_key(audio_keys)

                if self.download_id:
                    download_tracker.update_status(self.download_id, f"Downloading audio {audio_language}...")
                console.print(f"\n[dim]Downloading audio {audio_language}...")
                audio_status = audio_dl.start_download()

                if audio_status.get("error"):
                    console.print(f"[yellow]Error audio {audio_language}: {audio_status['error']}")
                    continue

                # Surface a still-encrypted extra audio through the main downloader.
                if getattr(audio_dl, "decrypt_failures", None) and self.media_downloader is not None:
                    self.media_downloader.decrypt_failures.extend(audio_dl.decrypt_failures)

                for af in audio_status.get("audios", []):
                    fpath = af.get("path")
                    if fpath and os.path.exists(fpath):
                        ext = os.path.splitext(fpath)[1]
                        final_path = os.path.join(self.output_dir, f"{self.filename_base}.{audio_language}{ext}")
                        try:
                            shutil.move(fpath, final_path)
                            external_audios.append(
                                {
                                    "file": os.path.basename(final_path),
                                    "language": audio_language,
                                    "path": final_path,
                                }
                            )
                        except Exception as e:
                            console.print(f"[yellow]Could not move audio {audio_language}: {e}")

                for sf in audio_status.get("subtitles", []):
                    fpath = sf.get("path")
                    if fpath and os.path.exists(fpath):
                        ext = os.path.splitext(fpath)[1]
                        sub_lang = sf.get("language") or sf.get("name") or audio_language
                        final_sub = os.path.join(self.output_dir, f"{self.filename_base}.{sub_lang}{ext}")
                        try:
                            shutil.move(fpath, final_sub)
                            external_subtitles.append(
                                {
                                    "path": final_sub,
                                    "language": sub_lang,
                                    "name": sub_lang,
                                    "size": os.path.getsize(final_sub),
                                }
                            )
                        except Exception as e:
                            console.print(f"[yellow]Could not move subtitle {sub_lang}: {e}")

            except Exception as e:
                console.print(f"[yellow]Warning on extra audio {audio_language}: {e}")
                logger.exception(f"Extra audio download failed for {audio_language}")
            finally:
                shutil.rmtree(audio_temp_dir, ignore_errors=True)

        return external_audios, external_subtitles

    def start(self) -> DownloadResult:
        """
        Execute the full DASH download pipeline.
        Returns ``(output_path, cancelled)`` — cancelled=True means abort.
        """
        precheck = self._precheck(self.mpd_url)
        if precheck is not None:
            return precheck

        try:
            self.media_players = MediaPlayers(self.output_dir)
            self.media_players.create()
        except Exception:
            pass

        self.media_downloader = MediaDownloader(
            url=self.mpd_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.mpd_headers,
            manifest_refresh_fn=self.manifest_refresh_fn,
            cookies=self.cookies,
            download_id=self.download_id,
            site_name=self.site_name,
            max_segments=self.max_segments,
            max_time=self.max_time,
            manifest_content=self.mpd_content,
            manifest_protocol="custom" if is_custom_manifest(self.mpd_content or "") else "dash",
        )
        self.media_downloader.other_tracks = self._merge_other_tracks
        self.media_downloader.license_url = self.license_url
        self.media_downloader.drm_type = self.drm_preference
        if self.custom_filters:
            self.media_downloader.custom_filters = self.custom_filters

        eff_sub_filter = (self.custom_filters or {}).get("subtitle") or SUBTITLE_FILTER
        if self._subtitle_tracks and eff_sub_filter != "false":
            normalized_subs = [_to_external_subtitle_track(track) for track in self._subtitle_tracks]
            filtered_subs = _filter_subtitles(normalized_subs, eff_sub_filter)
            if filtered_subs:
                logger.info(f"Adding {len(filtered_subs)} external subtitle(s) (filtered from {len(self._subtitle_tracks)}).")
                self.media_downloader.external_subtitles.extend(filtered_subs)
            else:
                console.print(f"[dim]No subtitles matched filter '{eff_sub_filter}' in {len(self._subtitle_tracks)}.")

        if self._dash_audio_tracks and AUDIO_FILTER != "false":
            logger.info(f"Adding {len(self._dash_audio_tracks)} external audio(s) (filtered from {len(self._dash_audio_tracks)}).")

        if self.chapters:
            logger.info(f"Adding {len(self.chapters)} external chapter(s).")

        # ── Parse
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing DASH ...")

        # Parse without showing table so we can annotate the DV companion first
        streams = self.media_downloader.parse_stream(show_table=False)

        # StreamSelector marks the DV companion with dv_companion=True when &dv is in the filter
        _dv_companion_stream = next(
            (s for s in streams if getattr(s, "dv_companion", False)),
            None,
        )
        if _dv_companion_stream is not None:
            dv_quality = getattr(_dv_companion_stream, "dv_companion_quality", "worst") or "worst"
            self._merge_other_tracks.append({"type": "video:dv", "url": self.mpd_url, "quality": dv_quality})
            self.media_downloader.other_tracks = list(self._merge_other_tracks)
            logger.info(f"&dv: DV companion found, added to other_tracks (quality={dv_quality!r})")

        # Show table: temporarily mark DV companion as selected so it appears highlighted
        if context_tracker.should_print and not context_tracker.hide_manifest_info and streams:
            _was_selected = None
            if _dv_companion_stream is not None:
                _was_selected = _dv_companion_stream.selected
                _dv_companion_stream.selected = True

            display_streams = streams
            if self.display_min_video_height is not None:
                def _display_height(stream) -> int:
                    height = getattr(stream, "height", 0) or 0
                    if height:
                        return int(height)
                    resolution = getattr(stream, "resolution", "") or ""
                    try:
                        return int(resolution.split("x")[-1])
                    except (TypeError, ValueError):
                        return 0

                display_streams = [
                    stream
                    for stream in display_streams
                    if getattr(stream, "type", "") != "video"
                    or _display_height(stream) >= self.display_min_video_height
                ]
            if self.display_only_drm_video:
                display_streams = [
                    stream
                    for stream in display_streams
                    if getattr(stream, "type", "") != "video"
                    or bool(getattr(stream, "drm", None) and stream.drm.is_encrypted())
                ]
            if self.display_only_drm_audio:
                display_streams = [
                    stream
                    for stream in display_streams
                    if getattr(stream, "type", "") != "audio"
                    or bool(getattr(stream, "drm", None) and stream.drm.is_encrypted())
                ]

            console.print(build_table(display_streams))
            if _dv_companion_stream is not None and _was_selected is not None:
                _dv_companion_stream.selected = _was_selected

        _, raw_mpd_str, _ = self.media_downloader.get_metadata()
        raw_mpd = raw_mpd_str if raw_mpd_str and raw_mpd_str != "None" else None

        # ── DRM
        drm_psshs = self._collect_drm_from_streams(streams)
        is_protected = bool(drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY))

        if not is_protected and raw_mpd:
            logger.info("No PSSH in Stream objects — falling back to MPDParser")
            drm_psshs = self._collect_drm_from_mpd(raw_mpd)
            is_protected = bool(drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY))

        # Last resort: a <ContentProtection> tag can be present with no pssh/default_KID anywhere
        if not is_protected:
            logger.info("Still no PSSH after Stream/MPD checks — probing init segments as last resort")
            drm_psshs = self._collect_drm_from_init_segments(streams)
            is_protected = bool(drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY))

        # Raw-key / Clear-key manifests
        if not is_protected and self.key:
            streams_encrypted = any(getattr(s, "drm", None) is not None and s.drm.is_encrypted() for s in streams)
            if streams_encrypted:
                self.decryption_keys = [self.key] if isinstance(self.key, str) else list(self.key)
                logger.info(f"DASH: encrypted streams without WV/PR PSSH — using {len(self.decryption_keys)} manual key(s)")

        if is_protected:
            self._warn_drm_mismatch(drm_psshs)
            if not self.license_url and not self.key:
                logger.error("Content is DRM-protected but no license_url or manual key provided")
            if self.download_id:
                download_tracker.update_status(self.download_id, "Fetching keys ...")

            self.decryption_keys = self._fetch_keys(drm_psshs)

            if not self.decryption_keys:
                self.error = "Failed to fetch decryption keys"
                if self.download_id:
                    download_tracker.complete_download(self.download_id, success=False, error=self.error)
                return DownloadResult(None, True, self.error)

        # ── Download
        self._log_tracks_json(streams, self.decryption_keys, self.mpd_url)
        if SKIP_DOWNLOAD:
            if DELAY_SS > 0:
                console.print(f"\n[yellow]Skipping download as per configuration and sleeping {DELAY_SS} seconds...")
                time.sleep(DELAY_SS)
            return DownloadResult(self.output_path, False, None)

        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")
        print()

        self.media_downloader.set_key(self.decryption_keys)
        status = self.media_downloader.start_download()

        status_check = self._check_download_status(status)
        if status_check is not None:
            return status_check

        # ── Extra audio MPD
        if self._dash_audio_tracks and AUDIO_FILTER != "false":
            if self.download_id:
                download_tracker.update_status(
                    self.download_id, f"Downloading {len(self._dash_audio_tracks)} extra audio track(s)..."
                )
            extra_audios, extra_subs = self._download_extra_audios()
            status["external_audios"] = extra_audios
            if extra_subs:
                existing = {s.get("path") for s in status.get("subtitles", [])}
                for sub in extra_subs:
                    if sub.get("path") not in existing:
                        status["subtitles"].append(sub)
                        existing.add(sub.get("path"))

        # ── Merge / finalize (shared tail)
        return self._merge_and_finalize(status)
