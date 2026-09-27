# 17.10.24

import logging
import os
import time
from collections.abc import Callable

from rich.console import Console

from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType, _DRMSystems
from VibraVid.core.manifest.stream import track_label
from VibraVid.core.muxing.helper.sub import extract_embedded_cc
from VibraVid.core.muxing.helper.video.hybrid import split_other_tracks
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.utils.media_players import MediaPlayers
from VibraVid.core.velora.downloader import MediaDownloader
from VibraVid.core.velora.util.formatting import (
    parse_max_segments as _parse_max_segments,
)
from VibraVid.core.velora.util.formatting import (
    parse_max_time as _parse_max_time,
)
from VibraVid.setup import get_prd_path, get_wvd_path, resolve_service_cdm_paths
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_headers

from .base import BaseDownloader, DownloadResult

console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
DELAY_SS = config_manager.config.get_int("DOWNLOAD", "delay_after_download")
EXTRACT_EMBEDDED_CC = config_manager.config.get_bool("DOWNLOAD", "extract_embedded_cc", default=False)


class HLS_Downloader(BaseDownloader):
    def __init__(
        self,
        m3u8_url: str | None = None,
        m3u8_content: str | None = None,
        headers: dict[str, str] | None = None,
        manifest_refresh_fn: Callable[[], str | None] | None = None,
        license_url: str | None = None,
        license_headers: dict[str, str] | None = None,
        license_certificate: str | None = None,
        license_data: dict | None = None,
        license_request_fn: Callable[[bytes, dict], bytes] | None = None,
        output_path: str | None = None,
        drm_preference=DRMType.WIDEVINE,
        key: str | None = None,
        cookies: dict[str, str] | None = None,
        max_segments: int | None = None,
        max_time=None,
        other_tracks: list | None = None,
        chapters: list | None = None,
        poster_url: str | None = None,
        sanitize_path: bool = True,
        hls_method: str | None = None,
        hls_key: bytes | None = None,
        hls_iv: bytes | None = None,
        has_drm: bool = False,
    ):
        """
        Parameters:
            - m3u8_url: M3U8 manifest URL to download.
            - m3u8_content: Content of the M3U8 manifest already downloaded (string). If provided, skips the HTTP fetch.
            - headers: HTTP headers for requests (auth, user-agent, etc).
            - manifest_refresh_fn: Optional function to call to refresh the manifest content during download. Should return the new manifest content as a string, or None to keep using the original.
            - license_url: DRM license server URL for Widevine/PlayReady.
            - license_headers: HTTP headers for DRM license requests.
            - license_certificate: Widevine certificate (base64) for license challenge.
            - license_data: Extra fields merged into the license request body (e.g. PlayReady SOAP envelope data).
            - license_request_fn: Optional callback (challenge bytes -> license bytes) for services whose license endpoint is a custom signed API call instead of a plain POST.
            - output_path: Output file path. Default: "download.{EXTENSION_OUTPUT}".
            - key: Manual decryption key (hex format) if known.
            - cookies: HTTP cookies for authenticated requests.
            - max_segments: Maximum number of segments to download (for testing). Default: None (all).
            - max_time: Maximum content duration to download, e.g. "01:00:00" or 3600 seconds. Default: None (all).
            - chapters: Chapter markers to inject into the muxed output, e.g. [{"name": str, "seconds": int}]. Default: context_tracker.chapters.
            - poster_url: Poster/still image URL to embed in the muxed output. Default: context_tracker.poster_url.
            - has_drm: Whether this stream is DRM-protected. Default False (skip the expensive per-track child-playlist
              DRM resolution, only fetching one representative playlist for live/VOD detection). Automatically forced
              to True regardless of this value if license_url/license_data/license_request_fn/key are provided.
        """
        self.m3u8_url = self._resolve_url(str(m3u8_url).strip()) if m3u8_url else None
        self.m3u8_content = m3u8_content
        self.headers = headers or get_headers()
        self.manifest_refresh_fn = manifest_refresh_fn

        self.license_url = str(license_url).strip() if license_url else None
        self.license_headers = license_headers or self.headers
        self.license_certificate = license_certificate
        self.license_data = license_data
        self.license_request_fn = license_request_fn
        self.drm_preference = drm_preference
        self.key = key
        # Auto-override: real DRM args always imply DRM, regardless of the caller-supplied flag.
        self.has_drm = has_drm or bool(license_url or license_data or license_request_fn or key)
        if self.has_drm and not has_drm:
            logger.info("HLS_Downloader: has_drm auto-forced to True (DRM args were provided)")

        self.cookies = cookies or {}
        self.max_segments = _parse_max_segments(
            max_segments if max_segments is not None else context_tracker.max_segments
        )
        self.max_time = _parse_max_time(max_time if max_time is not None else context_tracker.max_time)
        self.other_tracks = other_tracks or []
        self.chapters = chapters if chapters is not None else context_tracker.chapters
        self.poster_url = context_tracker.poster_url or poster_url or context_tracker.fallback_poster_url
        context_tracker.poster_url = self.poster_url

        # Manual override for HLS segment encryption (method/key/iv), bypassing manifest parsing. Useful for testing or non-standard streams.
        self.hls_enc_override: dict | None = None
        if hls_method or hls_key or hls_iv:
            self.hls_enc_override = {}
            if hls_method:
                self.hls_enc_override["method"] = "AES-128" if hls_method == "AES_128" else "NONE"
            if hls_key:
                self.hls_enc_override["key_bytes"] = hls_key
            if hls_iv:
                self.hls_enc_override["iv"] = hls_iv.hex()
        logger.info(f"Initialized HLS_Downloader with URL: {self.m3u8_url}, License URL: {self.license_url}, DRM Pref: {self.drm_preference}, Max Segments: {self.max_segments}, Max Time: {self.max_time}")

        wvd_override, prd_override = resolve_service_cdm_paths(context_tracker.site_name)
        self.drm_manager = DRMManager(
            wvd_override or get_wvd_path(),
            prd_override or get_prd_path(),
            config_manager.config.get_dict("DRM", "widevine", default={}),
            config_manager.config.get_dict("DRM", "playready", default={}),
            config_manager.config.get_bool("DRM", "prefer_remote_cdm"),
        )

        super().__init__(output_path, "_hls_temp", sanitize_path=sanitize_path)

    def _collect_drm_from_streams(self, streams: list) -> dict[str, list[dict]]:
        """
        Read PSSH data directly from Stream.drm (DRMInfo) on selected streams.

        Returns::

            {
              DRMType.WIDEVINE: [{'pssh': '...', 'kid': '...', 'type': 'Widevine'}, ...],
              DRMType.PLAYREADY: [{'pssh': '...', 'kid': '...', 'type': 'PlayReady'}, ...],
              DRMType.FAIRPLAY: [{'uri': '...', 'kid': '...', 'type': 'FairPlay'}, ...],
            }
        """
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}
        seen: dict[str, set] = {DRMType.WIDEVINE: set(), DRMType.PLAYREADY: set(), DRMType.FAIRPLAY: set()}
        kid_labels: dict[str, list] = {}

        for s in streams:
            if not getattr(s, "selected", False):
                continue

            drm = getattr(s, "drm", None)
            label = track_label(s)

            # Sub-parse variant if no DRM found yet and variant URL exists. Skipped entirely
            # when the stream is known to be DRM-free (self.has_drm=False) to avoid a useless fetch.
            if self.has_drm and not (drm and drm.is_encrypted()) and s.playlist_url:
                try:
                    from VibraVid.core.manifest.m3u8 import HLSParser

                    parser = HLSParser(self.m3u8_url, self.headers, has_drm=self.has_drm)
                    variant_drm, _ = parser.parse_variant(s.playlist_url)
                    if variant_drm and variant_drm.is_encrypted():
                        s.drm = variant_drm
                        drm = variant_drm
                        logger.info(f"Found DRM info in variant playlist: {s.playlist_url}")
                except Exception as exc:
                    logger.error(f"Failed to sub-parse variant {s.playlist_url} for DRM: {exc}")

            if not (drm and drm.is_encrypted()):
                continue

            stream_kids: set = set()
            stream_dts: list = []

            # Every KID this stream's manifest declared, not just the "primary" one
            kids = [k for k in (drm.get_all_kids() or []) if k and k != "N/A"]
            if not kids:
                kid_val = getattr(drm, "kid", None) or getattr(drm, "default_kid", None)
                kids = [kid_val] if kid_val and kid_val != "N/A" else ["N/A"]

            for dt in drm.get_all_drm_types():  # DRMType.WIDEVINE, DRMType.PLAYREADY, DRMType.FAIRPLAY, DRMType.UNKNOWN
                if dt not in result:
                    continue

                pssh = drm.get_pssh_for(dt)

                for kid in kids:
                    entry_pssh = pssh
                    if not entry_pssh and dt == DRMType.WIDEVINE and kid != "N/A":
                        try:
                            entry_pssh = _DRMSystems.build_widevine_pssh_from_kid(kid)
                            logger.info(f"HLS: synthesized Widevine PSSH from KID {kid}")
                        except Exception as exc:
                            logger.debug(f"HLS: Widevine PSSH synthesis failed: {exc}")

                    # Dedup on (pssh, kid): audio and video often share one PSSH box
                    # but carry different per-track KIDs — collapsing on PSSH alone
                    # drops one track's KID and the license fetch never asks for it.
                    dedup_key = (entry_pssh, (kid or "").replace("-", "").strip().lower())
                    if not entry_pssh or dedup_key in seen[dt]:
                        continue
                    seen[dt].add(dedup_key)

                    if kid and kid != "N/A" and label:
                        kid_norm = kid.replace("-", "").strip().lower()
                        labels = kid_labels.setdefault(kid_norm, [])
                        if label not in labels:
                            labels.append(label)

                    entry = {
                        "pssh" if dt != DRMType.FAIRPLAY else "uri": entry_pssh,
                        "kid": kid,
                        "type": "Widevine"
                        if dt == DRMType.WIDEVINE
                        else ("PlayReady" if dt == DRMType.PLAYREADY else "FairPlay"),
                        "label": label,
                    }

                    # Services whose license server demands the manifest's own key URI read this in their license_request_fn
                    key_uri = drm.get_key_uri(dt, entry_pssh)
                    if key_uri:
                        entry["key_uri"] = key_uri

                    result[dt].append(entry)
                    stream_kids.add(kid)
                    if dt not in stream_dts:
                        stream_dts.append(dt)

        # Merge every selected track's label onto each surviving entry so a key
        # shared across tracks is stored with the full quality it unlocks
        for entries in result.values():
            for entry in entries:
                kid_norm = (entry.get("kid") or "").replace("-", "").strip().lower()
                merged = kid_labels.get(kid_norm)
                if merged:
                    entry["label"] = " + ".join(merged)

        return result

    def _collect_drm_from_m3u8(self, raw_m3u8_path: str | None) -> dict[str, list[dict]]:
        """
        Fallback: run M3U8Parser on the saved raw manifest to find PSSH data.
        Imported lazily — if the parser is unavailable the method returns {} gracefully.
        """
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}
        try:
            from VibraVid.core.manifest.m3u8 import HLSParser as M3U8Parser

            content = None
            if raw_m3u8_path and os.path.exists(raw_m3u8_path):
                with open(raw_m3u8_path, encoding="utf-8") as f:
                    content = f.read()

            parser = M3U8Parser(self.m3u8_url, self.headers, content=content)
            drm_info = parser.get_drm_info()  # -> {'widevine': [...], 'playready': [...], 'fairplay': [...]}

            for entry in drm_info.get("widevine", []):
                result[DRMType.WIDEVINE].append(
                    {
                        "pssh": entry["pssh"],
                        "kid": entry.get("kid", "N/A"),
                        "type": "Widevine",
                        "key_uri": entry.get("key_uri"),
                    }
                )
            for entry in drm_info.get("playready", []):
                result[DRMType.PLAYREADY].append(
                    {
                        "pssh": entry["pssh"],
                        "kid": entry.get("kid", "N/A"),
                        "type": "PlayReady",
                        "key_uri": entry.get("key_uri"),
                    }
                )
            for entry in drm_info.get("fairplay", []):
                result[DRMType.FAIRPLAY].append(
                    {
                        "uri": entry["uri"],
                        "kid": entry.get("kid", "N/A"),
                        "type": "FairPlay",
                        "key_uri": entry.get("key_uri"),
                    }
                )
        except Exception as exc:
            logger.error(f"_collect_drm_from_m3u8 error: {exc}")

        return result

    def _fetch_keys(self, drm_psshs: dict[str, list[dict]]) -> list[str]:
        """Dispatch key fetch to DRMManager using the configured drm_preference"""
        keys = None

        if self.drm_preference == DRMType.WIDEVINE and drm_psshs.get(DRMType.WIDEVINE):
            try:
                keys = self.drm_manager.get_wv_keys(
                    drm_psshs[DRMType.WIDEVINE],
                    self.license_url,
                    license_data=self.license_data,
                    license_certificate=self.license_certificate,
                    headers=self.license_headers,
                    key=self.key,
                    license_request_fn=self.license_request_fn,
                )
            except Exception as exc:
                logger.error(f"Widevine key fetch failed: {exc}")

        if self.drm_preference == DRMType.PLAYREADY and drm_psshs.get(DRMType.PLAYREADY):
            try:
                keys = self.drm_manager.get_pr_keys(
                    drm_psshs[DRMType.PLAYREADY],
                    self.license_url,
                    headers=self.license_headers,
                    key=self.key,
                    license_data=self.license_data,
                    license_request_fn=self.license_request_fn,
                )
            except Exception as exc:
                logger.error(f"PlayReady key fetch failed: {exc}")

        if self.drm_preference == DRMType.FAIRPLAY and drm_psshs.get(DRMType.FAIRPLAY):
            try:
                keys = self.drm_manager.get_fp_keys(
                    drm_psshs[DRMType.FAIRPLAY],
                    self.license_url,
                    key=self.key,
                )
            except Exception as exc:
                logger.error(f"FairPlay key fetch failed: {exc}")

        if not keys and self.key:
            keys = [self.key] if isinstance(self.key, str) else list(self.key)

        return keys or []

    def _collect_embedded_cc(self, streams: list) -> list:
        """Find EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS renditions (CEA-608/708 baked into the video elementary stream, no separate playlist"""
        if not EXTRACT_EMBEDDED_CC:
            return []

        matches = []
        for s in streams:
            if getattr(s, "type", "") != "subtitle" or not getattr(s, "is_cc", False):
                continue
            if getattr(s, "playlist_url", None) or getattr(s, "segments", None):
                continue

            lang = getattr(s, "resolved_language", "") or getattr(s, "language", "") or ""
            if self.media_downloader._ext_lang_matches(lang, "subtitle"):
                matches.append(s)

        if matches:
            langs = [getattr(s, "resolved_language", "") or getattr(s, "language", "") or "und" for s in matches]
            logger.info(f"HLS: {len(matches)} embedded CLOSED-CAPTIONS stream(s) matched select_subtitle filter ({langs}) — will attempt extraction from merged video after download")
        return matches

    def _prepare_subtitle_tracks_for_merge(self, subtitle_tracks: list, video_path: str | None = None) -> list:
        """Hook for subclasses to materialize subtitle assets right before muxing. CC extraction reads straight from the raw (video-only) source."""
        embedded = getattr(self, "_embedded_cc_streams", None)
        if not embedded or not video_path or not os.path.exists(video_path):
            return subtitle_tracks

        for s in embedded:
            lang = getattr(s, "resolved_language", "") or getattr(s, "language", "") or "und"
            console.print(f"\n[cyan]Found CC in manifest for [red]{lang}[cyan], trying extraction...")
            srt_path = os.path.join(self.output_dir, f"{self.filename_base}.{lang}_cc.srt")
            result = extract_embedded_cc(video_path, srt_path)
            if result:
                console.print(f"[yellow]    Extracted embedded CC subtitle: [green]{lang}")
                subtitle_tracks.append(
                    {
                        "path": result,
                        "name": f"{lang}_cc",
                        "language": lang,
                        "size": os.path.getsize(result),
                    }
                )
            else:
                console.print(f"[yellow]    No embedded CC caption data found for '{lang}'")

        return subtitle_tracks

    def start(self) -> DownloadResult:
        """
        Execute the full HLS download pipeline.
        Returns ``(output_path, cancelled)`` — cancelled=True means abort.
        """
        precheck = self._precheck(self.m3u8_url)
        if precheck is not None:
            return precheck

        self.media_downloader = MediaDownloader(
            url=self.m3u8_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.headers,
            manifest_refresh_fn=self.manifest_refresh_fn,
            cookies=self.cookies,
            download_id=self.download_id,
            site_name=self.site_name,
            max_segments=self.max_segments,
            max_time=self.max_time,
            manifest_content=self.m3u8_content,
            manifest_protocol="hls",
            has_drm=self.has_drm,
        )
        self.media_downloader.other_tracks = self.other_tracks
        self.media_downloader.hls_enc_override = self.hls_enc_override
        other_videos, other_audios, other_subtitles = split_other_tracks(self.other_tracks)

        if other_subtitles:
            self.media_downloader.external_subtitles = other_subtitles

        if other_videos or other_audios:
            self.media_downloader.external_other_tracks = other_videos + other_audios

        if self.chapters:
            logger.info(f"Adding {len(self.chapters)} external chapter(s).")

        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing HLS ...")

        streams = self.media_downloader.parse_stream(show_table=context_tracker.should_print and not context_tracker.hide_manifest_info)

        if getattr(self.media_downloader, "no_match_skip", False):
            console.print("[yellow]Skipping — no track matched the requested video/audio/subtitle filter (-sv/-sa/-ss).")
            return DownloadResult(self.output_path, False, None)

        self._embedded_cc_streams = self._collect_embedded_cc(streams)
        if not self._embedded_cc_streams:
            # The streaming-mux fast path builds a plain ffmpeg command with no
            # per-input filtering -- embedded CEA-608/708 captions need the
            # extraction step _join_media_ffmpeg() does, so skip the fast path
            # whenever there's any to preserve.
            self._maybe_enable_streaming_mux()

        # ── DRM key fetch ─────────────────────────────────────────────────────
        raw_m3u8 = str(self.media_downloader.raw_m3u8) if self.media_downloader.raw_m3u8 else None

        # Primary: PSSH from Stream.drm (populated by HLSParser)
        drm_psshs = self._collect_drm_from_streams(streams)
        is_protected = bool(drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY) or drm_psshs.get(DRMType.FAIRPLAY))

        # Fallback: scan raw manifest via M3U8Parser
        if not is_protected and raw_m3u8:
            logger.info("No PSSH in Stream objects — falling back to M3U8Parser")
            drm_psshs = self._collect_drm_from_m3u8(raw_m3u8)
            is_protected = bool(drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY) or drm_psshs.get(DRMType.FAIRPLAY))

        # Vault/CDM resolution runs whenever DRM is detected
        if is_protected or self.key or self.license_request_fn:
            keys = self._fetch_keys(drm_psshs)

            if keys:
                self.media_downloader.set_key(keys)
            elif is_protected:
                console.print("[red]Warning: DRM detected but no decryption keys found")
        else:
            keys = []

        # ── Download ──────────────────────────────────────────────────────────
        self._log_tracks_json(streams, keys, self.m3u8_url)
        if SKIP_DOWNLOAD:
            if DELAY_SS > 0:
                console.print(f"\n[yellow]Skipping download as per configuration and sleeping {DELAY_SS} seconds...")
                time.sleep(DELAY_SS)
            return DownloadResult(self.output_path, False, None)

        try:
            self.media_players = MediaPlayers(self.output_dir)
            self.media_players.create()
        except Exception:
            pass

        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")
        print()

        status = self.media_downloader.start_download()

        # ── Guards → merge → finalize (shared tail)
        return self._finish_from_status(status)
