# 09.06.26

import copy
import logging
import os
import re
import threading
from types import SimpleNamespace
from typing import Any

from rich.console import Console

from VibraVid.core.decryptor.keys_manager import KeysManager
from VibraVid.core.downloader.util._drm_probe import PROBE_BYTES_FAST, DRMProbe
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType, normalize_kid
from VibraVid.core.muxing import probe_media_file
from VibraVid.core.muxing.helper.sub.convert import convert_subtitle
from VibraVid.core.muxing.helper.sub.disposition import (
    SubtitleDispositionInfo,
    build_subtitle_disposition_args,
    get_configured_disposition_language,
)
from VibraVid.core.muxing.helper.video.ts import is_mpegts_file
from VibraVid.core.muxing.streaming_mux import ChunkRelay, NamedPipeMuxer, StreamingMuxFeeder
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.ui.ui import build_table
from VibraVid.core.utils.codec import DV_CODEC_PREFIXES
from VibraVid.core.utils.language import language_variants, resolve_iso639_2, resolve_language_display_name
from VibraVid.core.utils.selector import (
    FilterSpec,
    StreamSelector,
    StreamSelectorFormatter,
    _matches_bitrate,
    _matches_codec,
    _matches_id,
    _matches_lang,
    _matches_res,
    _parse_subtitle_lang_requests,
    _subtitle_matches_request,
    _subtitle_pref_score,
    _subtitle_variant_key,
    configured_mux_dtsx,
)
from VibraVid.core.velora.downloader import MediaDownloader
from VibraVid.core.velora.util._stream_helpers import join_interruptible
from VibraVid.core.velora.util.formatting import (
    parse_max_segments as _parse_max_segments,
)
from VibraVid.core.velora.util.formatting import (
    parse_max_time as _parse_max_time,
)
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import create_client, get_headers

from .base import BaseDownloader, DownloadResult
from .mp4 import MP4_Downloader

console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
_MEDIA_TYPES = ("video", "audio", "subtitle")
_DIRECT_MEDIA_EXTS = (".mp4", ".m4v", ".m4a", ".mp3", ".aac", ".wav")
_LIVEMUX_WAIT_SECONDS = 1800.0


def _track_signature(s) -> tuple:
    """Signature used to drop cross-manifest duplicates (keep the first seen)."""
    codec = (getattr(s, "codecs", "") or "").strip().lower()
    btr = getattr(s, "bitrate", 0) or 0

    if s.type == "video":
        res = (getattr(s, "resolution", "") or "").lower() or f"{getattr(s, 'width', 0)}x{getattr(s, 'height', 0)}"
        return ("video", codec, res, btr)

    if s.type == "audio":
        lang = (getattr(s, "resolved_language", "") or getattr(s, "language", "") or "").lower()
        ch = (getattr(s, "channels", "") or "").lower()
        return ("audio", codec, lang, ch, btr)

    lang = (getattr(s, "resolved_language", "") or getattr(s, "language", "") or "").lower()
    return (
        "subtitle",
        lang,
        codec,
        bool(getattr(s, "forced", False)),
        bool(getattr(s, "is_cc", False)),
        bool(getattr(s, "is_sdh", False)),
    )


def _is_dv(s) -> bool:
    """True if the stream is a Dolby Vision video track."""
    if getattr(s, "type", "") != "video":
        return False

    if (getattr(s, "video_range", "") or "").upper() == "DV":
        return True

    codecs = (getattr(s, "codecs", "") or "").lower()
    return any(codecs.startswith(p) for p in DV_CODEC_PREFIXES)


def _normalize_lang(s) -> None:
    """Normalise language code"""
    if s.type not in ("audio", "subtitle"):
        return

    base = getattr(s, "resolved_language", "") or getattr(s, "language", "")
    if not base:
        return

    if s.type == "audio":
        s.language = base.split("-")[0].lower()
    else:
        s.language = base.lower()


class Generic_Downloader(BaseDownloader):
    _ROLE_KIND_TO_TYPE = {
        "video": "video",
        "vid": "video",
        "audio": "audio",
        "aud": "audio",
        "subtitle": "subtitle",
        "sub": "subtitle",
    }


    def __init__(
        self,
        sources: list[dict[str, Any]],
        output_path: str | None = None,
        max_segments: int | None = None,
        max_time=None,
        cookies: dict[str, str] | None = None,
        custom_filters: dict[str, str] | None = None,
        chapters: list | None = None,
        poster_url: str | None = None,
    ) -> None:
        """
        Parameters:
            - sources: list of source dicts (see class docstring).
            - output_path: final output file path. Default: "download.{ext}".
            - max_segments: cap downloaded segments per source (for testing).
            - max_time: cap downloaded duration, e.g. "01:00:00" or seconds.
            - cookies: default cookies applied to sources without their own.
            - custom_filters: optional {"video","audio","subtitle"} selector
              overrides; otherwise the values from config.json are used.
            - chapters: Chapter markers to inject into the muxed output, e.g. [{"name": str, "seconds": int}]. Default: context_tracker.chapters.
            - poster_url: Poster/still image URL to embed in the muxed output. Default: context_tracker.poster_url.
        """
        self.sources = [dict(s or {}) for s in (sources or [])]
        self._pooled_keys: list[Any] = [s.get("key") for s in self.sources if s.get("key")]
        self.cookies = cookies or {}
        self.max_segments = _parse_max_segments(
            max_segments if max_segments is not None else context_tracker.max_segments
        )
        self.max_time = _parse_max_time(max_time if max_time is not None else context_tracker.max_time)
        self.custom_filters = custom_filters or {}
        self.chapters = chapters if chapters is not None else context_tracker.chapters
        self.poster_url = context_tracker.poster_url or poster_url or context_tracker.fallback_poster_url
        context_tracker.poster_url = self.poster_url
        self._active: list[tuple[MediaDownloader, dict[str, Any]]] = []
        self._dv_stream = None
        self._no_match = False
        self.other_tracks: list = []
        self._direct_sources: list[dict[str, Any]] = []
        self._track_done_events: dict[str, threading.Event] = {}
        self._track_results: dict[str, str | None] = {}
        self._direct_streaming_mux_result: str | None = None
        self._pipe_mux_result: str | None = None

        logger.info(f"Initialized GENERIC_Downloader with {len(self.sources)} source(s), max_segments={self.max_segments}")
        super().__init__(output_path, "_generic_temp")

    def _fetch_manifest_content(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch raw manifest text (needed only when a source forces a protocol, because the type auto-detection keys off the URL extension)."""
        try:
            with create_client(headers=headers or get_headers(), timeout=20, follow_redirects=True) as c:
                resp = c.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.error(f"Failed to pre-fetch manifest {url!r}: {exc}")
            return None

    @staticmethod
    def _is_direct_media_url(url: str) -> bool:
        """True if the URL points at a plain media file (not a manifest to parse) — e.g. a raw .mp4/.m4a."""
        return url.lower().split("?")[0].endswith(_DIRECT_MEDIA_EXTS)

    def _parse_sources(self) -> list[tuple[MediaDownloader, dict[str, Any]]]:
        """Parse every source into a MediaDownloader with its streams. The source dict is kept alongside for reference (e.g. headers, protocol)."""
        parsed: list[tuple[MediaDownloader, dict[str, Any]]] = []
        for i, source in enumerate(self.sources):
            label = source.get("label") or f"src{i}"
            url = self._resolve_url(str(source.get("url") or "").strip())
            if not url:
                console.print(f"[yellow]Source '{label}' has no url, skipping.")
                continue

            out_dir = os_manager.get_sanitize_path(f"{self.output_dir}/{label}")
            os_manager.create_path(out_dir)

            if not source.get("protocol") and self._is_direct_media_url(url):
                self._direct_sources.append({"label": label, "url": url, "out_dir": out_dir, "source": source})
                continue

            protocol = source.get("protocol")
            content = source.get("manifest_content")
            if not content and protocol:
                content = self._fetch_manifest_content(url, source.get("headers") or {})

            md = MediaDownloader(
                url=url,
                output_dir=out_dir,
                filename=self.filename_base,
                headers=source.get("headers") or {},
                cookies=source.get("cookies") or self.cookies,
                download_id=self.download_id,
                site_name=self.site_name,
                max_segments=self.max_segments,
                max_time=self.max_time,
                manifest_content=content,
                manifest_protocol=protocol,
            )

            md.custom_filters = {"dv_auto": False}
            md.parse_stream(show_table=False)
            for s in md.streams:
                s._src_label = label
                _normalize_lang(s)

            parsed.append((md, source))
        return parsed

    def _preresolve_direct_source_keys(self) -> None:
        """Probe every direct source's KID up front and resolve+print all of them as ONE
        consolidated key block, instead of each concurrent thread probing and printing its own"""
        probe = DRMProbe()
        mgr = DRMManager()
        resolved_keys: list[str] = []
        vault_tagged_keys: list[str] = []
        vault_source_name: str | None = None
        drm_label = None
        pssh_val = None
        required_kids: set[str] = set()

        for entry in self._direct_sources:
            source = entry["source"]

            try:
                client = create_client(headers=source.get("headers") or {})
                try:
                    raw = probe.fetch(entry["url"], source.get("headers") or {}, client, size=PROBE_BYTES_FAST)
                finally:
                    client.close()
                encrypted, scheme, is_widevine, kid, pssh_b64 = probe.inspect(raw) if raw else (False, None, False, None, None)
            except Exception as exc:
                logger.debug(f"Pre-resolve probe failed for '{entry['label']}' (non-fatal): {exc}")
                continue

            if not encrypted:
                source["key"] = None
                continue

            try:
                result = mgr.resolve_flat_key(kid, pssh_b64, self._pooled_keys, drm_type=scheme or "mp4")
            except Exception as exc:
                logger.debug(f"Pre-resolve key resolution failed for '{entry['label']}' (non-fatal): {exc}")
                continue

            if not result:
                continue

            resolved_key, source_label = result
            source["key"] = resolved_key
            required_kids.add(normalize_kid(kid))
            if resolved_key not in resolved_keys:
                resolved_keys.append(resolved_key)
            if source_label and source_label != "manual":
                if resolved_key not in vault_tagged_keys:
                    vault_tagged_keys.append(resolved_key)
                vault_source_name = vault_source_name or source_label
            if drm_label is None:
                drm_label = "Widevine" if is_widevine else (scheme or "unknown DRM")
                pssh_val = pssh_b64

        if resolved_keys:
            mgr._display_keys(
                resolved_keys,
                vault_tagged_keys,
                drm_label,
                pssh_val,
                vault_source_name,
                header=True,
                default_label="manual",
                required_kids=required_kids,
            )

    def _track_done_event(self, key: str) -> threading.Event:
        event = self._track_done_events.get(key)
        if event is None:
            event = threading.Event()
            self._track_done_events[key] = event
        return event

    def _mark_track_done(self, key: str, result_path: str | None) -> None:
        self._track_results[key] = result_path
        self._track_done_event(key).set()

    def _get_track_result(self, key: str) -> str | None:
        return self._track_results.get(key)

    @staticmethod
    def _is_dv_role(role: str) -> bool:
        return role.partition(":")[2].strip().lower() == "dv"

    def _download_one_direct_source(
        self, entry: dict[str, Any], bar_mgr: DownloadBarManager, relay: "ChunkRelay | None" = None
    ) -> tuple[bool, bool]:
        """Run one direct-media entry's MP4_Downloader on the shared bar. Returns (ok, need_stop).

        *relay* is only ever passed for the source identified as the streaming-mux fast
        path's video track (see _maybe_launch_streaming_mux_direct) -- it receives every
        confirmed-clear byte as it's written to disk, live."""
        source = entry["source"]
        label = entry["label"]
        url = entry["url"]

        role = str(source.get("role") or source.get("type") or "video").strip().lower()
        kind = role.split(":")[0]
        ext = os.path.splitext(url.split("?")[0])[1] or ".mp4"
        out_path = os.path.join(entry["out_dir"], f"{self.filename_base}{ext}")

        try:
            path, need_stop, error = MP4_Downloader(
                url=url,
                path=out_path,
                headers=source.get("headers") or {},
                download_id=self.download_id,
                site_name=self.site_name,
                label=label,
                # source["key"] is normally already pinned to a single resolved pair by
                # _preresolve_direct_source_keys by the time this runs; fall back to the
                # full cross-source pool (not just this source's own declared key) so a
                # misattributed key still resolves if that pre-pass didn't run/match.
                key=source.get("key") or self._pooled_keys,
                check_content_type=True,
                bar_mgr=bar_mgr,
                suppress_key_log=True,
                expected_language=source.get("language") or source.get("lang"),
                expected_forced=(source.get("tag") or role.partition(":")[2]).strip().lower() == "forced",
                on_clear_chunk=relay.feed if relay else None,
                on_clear_abandon=relay.close if relay else None,
            )
        except Exception as exc:
            logger.error(f"Direct source '{label}' crashed: {exc}", exc_info=True)
            console.print(f"[red]Direct source '{label}' failed: {exc}")
            return False, False

        if need_stop:
            return False, True

        if not path or not os.path.exists(path):
            logger.error(f"Direct source '{label}' failed to download: {error}")
            console.print(f"[red]Direct source '{label}' failed: {error}")
            return False, False

        entry["result"] = {
            "path": path,
            "kind": kind,
            "role": role,
            "language": source.get("language") or source.get("lang") or "und",
            "size": os.path.getsize(path),
        }
        return True, False

    def _streaming_mux_direct_eligible(self) -> dict[str, Any] | None:
        """Return the eligible video entry for the direct-mp4 streaming-mux fast path, or None."""
        if context_tracker.no_livemux:
            return None
        
        if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() != "ffmpeg":
            return None
        
        if not str(self.output_path).lower().endswith(".mkv"):
            return None
        
        if self._dv_stream is not None or self._active:
            return None  # manifest-based DV companion or any manifest track present -- stay on the safe post-download path
        
        try:
            if not get_ffmpeg_path():
                return None
        except Exception:
            return None

        video_entries = [
            e
            for e in self._direct_sources
            if str(e["source"].get("role") or e["source"].get("type") or "video").strip().lower().split(":")[0]
            in ("video", "vid")
        ]
        if len(video_entries) != 1:
            return None
        video_entry = video_entries[0]
        role = str(video_entry["source"].get("role") or video_entry["source"].get("type") or "video").strip().lower()
        if self._is_dv_role(role):
            return None
        return video_entry

    def _launch_streaming_mux_direct(self, video_entry: dict[str, Any]) -> tuple[ChunkRelay, threading.Thread]:
        """Spawn the background thread that waits for every sibling direct source to
        finish, builds the ffmpeg command and starts StreamingMuxFeeder, then attaches the
        relay so the video source (already downloading) can feed it."""
        relay = ChunkRelay()
        sibling_entries = [e for e in self._direct_sources if e is not video_entry]

        def _worker() -> None:
            for entry in sibling_entries:
                if not self._track_done_event(entry["label"]).wait(timeout=_LIVEMUX_WAIT_SECONDS):
                    logger.info(f"streaming_mux: timed out waiting for '{entry['label']}' -- falling back to the normal mux")
                    relay.close()
                    return
                if self._get_track_result(entry["label"]) is None:
                    logger.info(f"streaming_mux: sibling source '{entry['label']}' failed -- falling back to the normal mux")
                    relay.close()
                    return

            try:
                cmd, output_path = self._build_streaming_mux_cmd_direct(video_entry, sibling_entries)
            except Exception as exc:
                logger.warning(f"streaming_mux: failed to build ffmpeg command ({exc!r}) -- falling back to the normal mux", exc_info=True)
                relay.close()
                return

            feeder = StreamingMuxFeeder(cmd)
            try:
                feeder.start()
            except Exception as exc:
                logger.warning(f"streaming_mux: failed to start ffmpeg: {exc}")
                relay.close()
                return

            if not relay.attach(feeder.feed):
                feeder.abort()
                return
            relay.on_close(feeder.abort)

            logger.info(f"streaming_mux: started early cross-track mux -> {output_path}")

            if not self._track_done_event(video_entry["label"]).wait(timeout=_LIVEMUX_WAIT_SECONDS):
                logger.info("streaming_mux: timed out waiting for the video source -- falling back to the normal mux")
                feeder.abort()
                return
            if self._get_track_result(video_entry["label"]) is None:
                logger.info("streaming_mux: video source failed -- falling back to the normal mux")
                feeder.abort()
                return

            result = feeder.finish()
            if result.ok:
                self._direct_streaming_mux_result = output_path
                logger.info(f"streaming_mux: fast-path mux finished -> {output_path}")
            else:
                logger.warning(f"streaming_mux: ffmpeg failed ({result.error}) -- falling back to the normal mux. stderr tail: {result.stderr_tail[-500:]}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return relay, t

    def _build_streaming_mux_cmd_direct(
        self, video_entry: dict[str, Any], sibling_entries: list[dict[str, Any]]
    ) -> tuple[list[str], str]:
        """Build the ffmpeg command for the direct-mp4 streaming-mux fast path: video piped
        via stdin, every finished sibling audio/subtitle source as a plain -i input."""
        ffmpeg_path = get_ffmpeg_path()
        output_path = os_manager.get_sanitize_path(self.output_path)

        audio_paths: list[str] = []
        audio_langs: list[str] = []
        subtitle_paths: list[str] = []
        subtitle_infos: list[SubtitleDispositionInfo] = []
        subtitle_langs: list[str] = []

        force_subtitle = config_manager.config.get("PROCESS", "force_subtitle")
        for entry in sibling_entries:
            result = entry.get("result")
            source = entry["source"]
            role = str(source.get("role") or source.get("type") or "").strip().lower()
            kind = role.split(":")[0]
            path = result["path"]
            lang = result.get("language") or "und"

            if kind in ("audio", "aud"):
                audio_paths.append(path)
                audio_langs.append(lang)
            elif kind in ("subtitle", "sub"):
                converted = convert_subtitle(path, force_subtitle)
                if not converted:
                    logger.warning(f"streaming_mux: subtitle conversion failed for '{entry['label']}' -- dropping this track from the fast-path mux")
                    continue
                subtitle_paths.append(converted)
                tag = (source.get("tag") or role.partition(":")[2]).strip().lower()
                subtitle_infos.append(SubtitleDispositionInfo(language=lang, forced=tag == "forced"))
                subtitle_langs.append(lang)

        cmd = [ffmpeg_path, "-y", "-f", "mp4", "-i", "-"]
        for p in audio_paths:
            if is_mpegts_file(p):
                cmd += ["-f", "mpegts"]
            cmd += ["-i", p]
        for p in subtitle_paths:
            cmd += ["-i", p]

        cmd += ["-map", "0:v:0"]
        video_codecs = str(video_entry["source"].get("codecs") or "").lower()
        if any(p in video_codecs for p in ("hev", "hvc", "dvh", "dvhe")):
            cmd += ["-tag:v", "hvc1"]

        for i in range(len(audio_paths)):
            cmd += ["-map", f"{i + 1}:a"]
        sub_input_base = 1 + len(audio_paths)
        for i in range(len(subtitle_paths)):
            cmd += ["-map", f"{sub_input_base + i}:s"]

        for i, lang in enumerate(audio_langs):
            iso_lang = resolve_iso639_2(lang)
            title = resolve_language_display_name(lang)
            cmd += [f"-metadata:s:a:{i}", f"title={title}"]
            cmd += [f"-metadata:s:a:{i}", f"language={iso_lang}"]
            cmd += [f"-metadata:s:a:{i}", f"handler_name={title}"]
            cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]

        for i, lang in enumerate(subtitle_langs):
            iso_lang = resolve_iso639_2(lang)
            title = resolve_language_display_name(lang)
            cmd += [f"-metadata:s:s:{i}", f"title={title}"]
            cmd += [f"-metadata:s:s:{i}", f"language={iso_lang}"]
            cmd += [f"-metadata:s:s:{i}", f"handler_name={title}"]

        cmd += build_subtitle_disposition_args(subtitle_infos, get_configured_disposition_language())

        cmd += ["-c", "copy"]
        if subtitle_paths:
            cmd += ["-c:s", "srt"]
        cmd += [output_path]

        return cmd, output_path

    def _download_direct_sources(self) -> bool:
        """Download every self._direct_sources entry (plain .mp4/.m4a/... files) concurrently
        on one shared progress bar, the same way _run_downloads does for manifest-based
        sources. Returns False if cancelled (Ctrl+C)."""
        if not self._direct_sources:
            return True

        self._preresolve_direct_source_keys()

        video_entry = self._streaming_mux_direct_eligible()
        relay, mux_thread = (
            self._launch_streaming_mux_direct(video_entry) if video_entry is not None else (None, None)
        )

        stop_event = threading.Event()
        results: dict[int, tuple[bool, bool]] = {}

        def _run(i: int, entry: dict[str, Any], bm: DownloadBarManager) -> None:
            this_relay = relay if entry is video_entry else None
            ok, need_stop = self._download_one_direct_source(entry, bm, this_relay)
            results[i] = (ok, need_stop)
            self._mark_track_done(entry["label"], entry.get("result", {}).get("path") if ok else None)

        bar = DownloadBarManager(self.download_id)
        threads: list[threading.Thread] = []
        try:
            with bar as bm:
                for i, entry in enumerate(self._direct_sources):
                    t = threading.Thread(target=_run, args=(i, entry, bm), daemon=True)
                    threads.append(t)
                    t.start()

                join_interruptible(threads, stop_event)
                bm.finish_all_tasks()
        except KeyboardInterrupt:
            logger.warning("KeyboardInterrupt — stopping all direct-media sources")
            stop_event.set()
            if relay is not None:
                relay.close()
            join_interruptible(threads, threading.Event(), hard_timeout=15.0)
            return False

        if mux_thread is not None:
            # All direct sources are done, so the mux worker's own waits resolve almost
            # immediately from here -- this just waits for it to finish writing the
            # muxed file (or aborting) before _collect_status()/_merge_files() run.
            mux_thread.join(timeout=_LIVEMUX_WAIT_SECONDS)

        if any(need_stop for _ok, need_stop in results.values()):
            return False

        return True
    
    def _apply_explicit_roles(
        self,
        parsed: list[tuple[MediaDownloader, dict[str, Any]]],
        video_filter: str = "",
        audio_filter: str = "",
        subtitle_filter: str = "",
    ) -> tuple[list, list[tuple[MediaDownloader, dict[str, Any]]]]:
        """Apply per-source explicit ``role`` tags and split them off from auto-selection.

            {"url": ..., "key": ..., "role": "video"}   # attribute-less video manifest
            {"url": ..., "key": ..., "role": "audio", "language": "en"}
            {"url": ..., "key": ..., "role": "subtitle", "language": "en"}

        A role tag other than a bare kind (e.g. "video:hdr10") is just a
        cosmetic ``video_range`` label -- Dolby Vision routing is handled by
        ``select_video="hybrid"`` across the whole pool, not by per-source
        roles (see ``_select``).

        Explicit roles still honour the global ``select_video``/``select_audio``/
        ``select_subtitle`` filters (and a per-source ``language``): the filter
        narrows the candidate pool before the best-bitrate pick, so a manifest
        listing ``de`` before ``it``/``en`` can no longer win on ``max()`` ties
        when the user asked for ``ita|eng``.

        Returns ``(role_streams, auto_parsed)`` where ``auto_parsed`` are the sources left to the normal pool/dedup/StreamSelector path.
        """
        role_streams: list = []
        auto_parsed: list[tuple[MediaDownloader, dict[str, Any]]] = []
        strict = bool(context_tracker.skip_no_match)
        keep_dv = self._dv_companion_possible(video_filter)

        for md, src in parsed:
            role = str(src.get("role") or src.get("type") or "").strip().lower()
            if not role:
                auto_parsed.append((md, src))
                continue

            kind, _, tag = role.partition(":")
            kind, tag = kind.strip(), tag.strip()

            cands = [s for s in md.streams if not getattr(s, "is_external", False)]
            if not cands:
                logger.warning(f"Source role '{role}' has no parsable stream — skipping")
                continue

            # A role manifest often contains OTHER stream types too
            expected_type = self._ROLE_KIND_TO_TYPE.get(kind)
            if expected_type:
                typed_cands = [s for s in cands if getattr(s, "type", "") == expected_type]
                if typed_cands:
                    cands = typed_cands
                else:
                    logger.warning(f"Source role '{role}': manifest has no {expected_type} stream, using full pool as fallback")

            src_lang = str(src.get("language") or src.get("lang") or "").strip()
            if src_lang and expected_type in ("audio", "subtitle"):
                lang_cands = [s for s in cands if _matches_lang(s, src_lang)]
                if lang_cands:
                    cands = lang_cands
                else:
                    logger.warning(f"Source role '{role}': no stream matches source language={src_lang!r}, using full pool as fallback")

            narrowed: list | None = None
            filter_desc = ""
            if expected_type == "video":
                narrowed, filter_desc = self._narrow_explicit_video_cands(cands, video_filter)
            elif expected_type == "audio":
                narrowed, filter_desc = self._narrow_explicit_audio_cands(cands, audio_filter)
            elif expected_type == "subtitle":
                narrowed, filter_desc = self._narrow_explicit_subtitle_cands(cands, subtitle_filter)

            if narrowed is not None:
                if narrowed:
                    cands = narrowed
                elif strict:
                    logger.warning(f"Source role '{role}': no stream matches {filter_desc} (strict_no_match) — skipping this source")
                    auto_parsed.append((md, src))
                    continue
                else:
                    avail = sorted({(getattr(s, "resolved_language", "") or getattr(s, "language", "") or "und") for s in cands})
                    logger.warning(f"Source role '{role}': no stream matches {filter_desc} (available: {','.join(avail)}), falling back to best bitrate")

            lang_filter = audio_filter if expected_type == "audio" else (subtitle_filter if expected_type == "subtitle" else "")
            if expected_type == "audio" and self._explicit_lang_pool_matched(narrowed, audio_filter, "audio", src_lang):
                picks = self._best_per_lang_explicit(cands, audio_filter)
            elif expected_type == "subtitle" and self._explicit_lang_pool_matched(narrowed, subtitle_filter, "subtitle", src_lang):
                picks = self._best_per_variant_explicit(cands)
            else:
                picks = [self._pick_explicit_role_stream(cands, expected_type, lang_filter)]

            # Only the picked stream(s), plus any other stream of the SAME type
            # (to avoid a second, competing auto-pick of e.g. a second video
            # rendition from this same source), are removed from later
            # auto-selection.
            picked_ids = {id(s) for s in picks}
            for s in md.streams:
                if id(s) in picked_ids:
                    s._role_claimed = True
                elif expected_type and getattr(s, "type", "") == expected_type and not (keep_dv and _is_dv(s)):
                    s._role_claimed = True

            lang = src.get("language") or src.get("lang")
            name = src.get("name")

            for stream in picks:
                if kind in ("video", "vid"):
                    stream.type = "video"
                    if tag:
                        stream.video_range = tag.upper()  # HDR10, DV, SDR, ... (cosmetic label only)

                elif kind in ("audio", "aud"):
                    stream.type = "audio"
                    if lang:
                        stream.language = lang

                elif kind in ("subtitle", "sub"):
                    stream.type = "subtitle"
                    if lang:
                        stream.language = lang

                    # Also read the source's "tag" field (e.g. "forced")
                    src_tag = (src.get("tag") or tag or "").strip().lower()
                    if src_tag == "forced":
                        stream.forced = True
                    elif src_tag:
                        logger.debug(f"Subtitle tag '{src_tag}' not recognized — ignoring")

                else:
                    logger.warning(f"Unknown source role '{role}' — treating as video")
                    stream.type = "video"

                if name:
                    stream.name = name
                stream._src_label = src.get("label") or kind
                _normalize_lang(stream)

            role_streams.extend(picks)
            picked_desc = ",".join(f"{getattr(s, 'id', '')}({getattr(s, 'language', '')})" for s in picks)
            logger.info(f"Explicit role '{role}' -> {picks[0].type} [{picked_desc}] {f' filter={filter_desc}' if filter_desc else ''}")

            # This source's non-claimed streams (other types, or DV video
            # variants) still flow into normal pool-based auto-selection.
            auto_parsed.append((md, src))

        return role_streams, auto_parsed

    @staticmethod
    def _pick_explicit_role_stream(cands: list, expected_type: str | None, lang_filter: str = ""):
        """Best-bitrate pick; on bitrate ties prefer the earliest language token in the filter."""
        tokens = [t for t in re.split(r"[|\s]+", lang_filter or "") if t.strip()] if expected_type in ("audio", "subtitle") else []
        if not tokens:
            return max(cands, key=lambda s: getattr(s, "bitrate", 0) or 0)

        def _pref(s) -> int:
            for i, tok in enumerate(tokens):
                try:
                    if _matches_lang(s, tok):
                        return i
                except Exception:
                    continue
            return len(tokens)

        return max(cands, key=lambda s: (getattr(s, "bitrate", 0) or 0, -_pref(s)))

    @staticmethod
    def _explicit_lang_pool_matched(narrowed: list | None, lang_filter: str, stream_type: str, src_lang: str) -> bool:
        """True when the candidate pool was restricted by a language constraint."""
        if narrowed:
            spec = FilterSpec.parse(lang_filter or ("all" if stream_type == "subtitle" else "best"), stream_type)
            if spec.langs:
                return True
        
        if src_lang and narrowed is None:
            # Per-source "language" already restricted the pool (single lang).
            return True
        return False

    @staticmethod
    def _best_per_lang_explicit(cands: list, audio_filter: str = "") -> list:
        """Best rendition per language, ties broken by filter-token order (ita before eng)."""
        tokens = [t for t in re.split(r"[|\s]+", audio_filter or "") if t.strip()]

        def _pref(s) -> int:
            for i, tok in enumerate(tokens):
                try:
                    if _matches_lang(s, tok):
                        return i
                except Exception:
                    continue
            return len(tokens)

        ordered = sorted(cands, key=lambda s: (getattr(s, "bitrate", 0) or 0, -_pref(s)), reverse=True)
        seen: set = set()
        picks: list = []
        for s in ordered:
            key = (getattr(s, "language", "") or "und").lower()
            if key not in seen:
                seen.add(key)
                picks.append(s)
        return picks or list(cands[:1])

    @staticmethod
    def _best_per_variant_explicit(cands: list) -> list:
        """Best rendition per subtitle variant (plain/forced/sdh/cc), like StreamSelector."""
        groups: dict = {}
        for s in cands:
            groups.setdefault(_subtitle_variant_key(s), []).append(s)
        return [max(pool, key=_subtitle_pref_score) for pool in groups.values()] or list(cands[:1])

    @staticmethod
    def _narrow_explicit_video_cands(cands: list, video_filter: str = "") -> tuple[list | None, str]:
        """Narrow explicit video candidates by the full select_video spec (res/codec/id/bitrate)."""
        spec = FilterSpec.parse(video_filter or "best", "video")
        if spec.drop or spec.select_all:
            return None, ""
        
        if not (spec.id or spec.res or spec.codec or spec.bitrate_min is not None or spec.bitrate_max is not None):
            return None, ""

        pool = list(cands)
        if spec.id:
            pool = [s for s in pool if _matches_id(s, spec.id)]
            if not pool:
                return [], f"video id={spec.id!r}"
            
        if spec.res:
            narrowed = [s for s in pool if _matches_res(s, spec.res)]
            if narrowed:
                pool = narrowed
            else:
                return [], f"video res={spec.res!r}"
            
        if spec.codec:
            narrowed = [s for s in pool if _matches_codec(s, spec.codec)]
            if narrowed:
                pool = narrowed
            else:
                return [], f"video codec={spec.codec!r}"
            
        if spec.bitrate_min is not None or spec.bitrate_max is not None:
            narrowed = [s for s in pool if _matches_bitrate(s, spec.bitrate_min, spec.bitrate_max)]
            if narrowed:
                pool = narrowed
            else:
                return [], f"video bitrate=[{spec.bitrate_min},{spec.bitrate_max}]"
        
        desc = f"video filter={video_filter!r}"
        return pool, desc

    @staticmethod
    def _narrow_explicit_audio_cands(cands: list, audio_filter: str = "") -> tuple[list | None, str]:
        """Narrow explicit audio candidates by select_audio (langs/codec/id)."""
        spec = FilterSpec.parse(audio_filter or "best", "audio")
        if spec.drop or spec.select_all:
            return None, ""
        
        if not (spec.id or spec.langs or spec.codec):
            return None, ""

        pool = list(cands)
        if spec.id:
            pool = [s for s in pool if _matches_id(s, spec.id)]
            if not pool:
                return [], f"audio id={spec.id!r}"
            
        if spec.langs:
            narrowed = [s for s in pool if _matches_lang(s, spec.langs)]
            if narrowed:
                pool = narrowed
            else:
                return [], f"audio lang={spec.langs!r}"
            
        if spec.codec:
            narrowed = [s for s in pool if _matches_codec(s, spec.codec)]
            if narrowed:
                pool = narrowed
            else:
                return [], f"audio codec={spec.codec!r}"
            
        desc = f"audio filter={audio_filter!r}"
        return pool, desc

    @staticmethod
    def _narrow_explicit_subtitle_cands(cands: list, subtitle_filter: str = "") -> tuple[list | None, str]:
        """Narrow explicit subtitle candidates by select_subtitle (langs + forced/cc/sdh flags)."""
        spec = FilterSpec.parse(subtitle_filter or "all", "subtitle")
        if spec.drop or spec.select_all:
            return None, ""
        if not (spec.id or spec.langs):
            return None, ""

        pool = list(cands)
        if spec.id:
            pool = [s for s in pool if _matches_id(s, spec.id)]
            if not pool:
                return [], f"subtitle id={spec.id!r}"
            
        if spec.langs:
            requests = _parse_subtitle_lang_requests(spec.langs)
            if requests:
                narrowed = [
                    s
                    for s in pool
                    if any(_subtitle_matches_request(s, base, flags) for base, flags in requests)
                ]
            else:
                narrowed = [s for s in pool if _matches_lang(s, spec.langs)]
            if narrowed:
                pool = narrowed
            else:
                return [], f"subtitle lang={spec.langs!r}"
            
        desc = f"subtitle filter={subtitle_filter!r}"
        return pool, desc

    def _select(self, parsed: list[tuple[MediaDownloader, dict[str, Any]]]) -> list:
        f = self.custom_filters
        v = f.get("video") or config_manager.config.get("DOWNLOAD", "select_video")
        a = f.get("audio") or config_manager.config.get("DOWNLOAD", "select_audio")
        sub = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")

        # Sources with an explicit role bypass attribute-based dedup/selection,
        # but _apply_explicit_roles still narrows each source by the same filters.
        role_streams, parsed = self._apply_explicit_roles(parsed, v, a, sub)

        # A type already resolved by an explicit role (e.g. "audio") takes
        # precedence -- any other source's own stream of that same type is
        # excluded from the pool too, so it can't be auto-picked as a
        # competing/duplicate second audio (or video, or subtitle) track.
        claimed_types = {getattr(s, "type", "") for s in role_streams}
        keep_dv = self._dv_companion_possible(v)

        # Merge + dedup (keep first occurrence in source order).
        pool: list = []
        seen: set = set()
        for md, _ in parsed:
            for s in md.streams:
                if getattr(s, "is_external", False) or getattr(s, "_role_claimed", False):
                    continue
                if getattr(s, "type", "") in claimed_types and not (keep_dv and _is_dv(s)):
                    continue
                sig = _track_signature(s)
                if sig in seen:
                    continue
                seen.add(sig)
                pool.append(s)

        # Reset selection across ALL sources, then select once over the pool.
        for md, _ in parsed:
            for s in md.streams:
                s.selected = False

        # Explicit-role picks are final regardless of the pool pass below
        # (they were excluded from `pool` above precisely so nothing can
        # override them) -- restore their `.selected` flag after the reset.
        for s in role_streams:
            s.selected = True

        selector = self._build_selector(v, a, sub)
        selector.apply(pool)
        self._no_match = selector.no_match

        # If a DV companion was selected (select_video="hybrid" or CODEC.dv_auto),
        # keep a reference to it for special handling in the download/muxing phases.
        self._dv_stream = next((s for s in pool if getattr(s, "dv_companion", False)), None)
        if self._dv_stream is not None:
            self._dv_stream.selected = True
            logger.info(f"hybrid: companion selected -> {self._dv_stream}")

        return role_streams + [s for s in pool if s.selected]

    def _dv_companion_possible(self, video_filter: str) -> bool:
        """True when a Dolby Vision stream may still be needed as RPU companion: ``select_video="hybrid"`` or ``CODEC.dv_auto`` (a per-run override wins)."""
        if str(video_filter or "").strip().lower() == "hybrid":
            return True
        return bool(self.custom_filters.get("dv_auto", config_manager.config.get_bool("CODEC", "dv_auto")))

    def _build_selector(self, video: str, audio: str, subtitle: str) -> StreamSelector:
        f = self.custom_filters
        return StreamSelector(
            video,
            audio,
            subtitle,
            formatter=StreamSelectorFormatter(),
            prefer_h265=bool(f.get("prefer_h265")),
            prefer_hdr10=bool(f.get("prefer_hdr10")),
            prefer_drm=bool(f.get("prefer_drm")),
            require_drm=bool(f.get("require_drm")),
            minimum_video_height=int(f.get("minimum_video_height") or 0),
            strict_no_match=context_tracker.skip_no_match,
            dv_auto=bool(f.get("dv_auto", config_manager.config.get_bool("CODEC", "dv_auto"))),
            mux_dtsx=bool(f.get("mux_dtsx", configured_mux_dtsx())),
            drop_clear_av=bool(f.get("drop_clear_av")),
            dv_top_tier_tolerance=f.get("dv_top_tier_tolerance"),
        )

    def _setup_dv_companion(self) -> None:
        """Re download the manifest of the DV companion in a dedicated MediaDownloader, to isolate it from the main video stream and avoid filename collisions on disk (both have the same "{filename}.{ext}")."""
        if self._dv_stream is None:
            return

        owner = next(((md, src) for md, src in self._active if self._dv_stream in md.streams), None)
        if owner is None:
            self._dv_stream = None
            return

        md, source = owner
        self._dv_stream.selected = False
        target = copy.copy(self._dv_stream)
        target.selected = True

        dv_dir = os_manager.get_sanitize_path(f"{self.output_dir}/_dv")
        os_manager.create_path(dv_dir)

        dv_md = MediaDownloader(
            url=md.url,
            output_dir=dv_dir,
            filename=self.filename_base,
            headers=source.get("headers") or {},
            cookies=source.get("cookies") or self.cookies,
            download_id=self.download_id,
            site_name=self.site_name,
            max_segments=self.max_segments,
            max_time=self.max_time,
        )
        dv_md.manifest_type = md.manifest_type
        dv_md.streams = [target]

        target._src_label = "dv"
        self._dv_stream = target
        self._active.append((dv_md, source))
        logger.info(f"hybrid: companion isolated in dedicated downloader -> {target}")

    def _preresolve_manifest_keys(self) -> None:
        """Probe every manifest source's selected streams for KIDs, then resolve+print all of them as ONE consolidated key block, instead of each concurrent thread probing and printing its own."""
        pssh_by_kid: dict[str, str | None] = {}
        widevine_pssh = None
        drm_type = None
        for md, _source in self._active:
            for s in md.streams:
                if not s.selected or s.is_external:
                    continue
                drm = getattr(s, "drm", None)
                if drm is None or not drm.is_encrypted():
                    continue
                for kid in drm.get_all_kids():
                    pssh_by_kid.setdefault(normalize_kid(kid), drm.pssh)
                if widevine_pssh is None:
                    widevine_pssh = drm.get_pssh_for(DRMType.WIDEVINE)
                if drm_type is None:
                    drm_type = drm.drm_type

        if not pssh_by_kid:
            return

        mgr = DRMManager()
        norm_keys = KeysManager.normalize(self._pooled_keys) if self._pooled_keys else []
        covered_kids = {kid for kid, _ in norm_keys}
        resolved_keys = [f"{kid}:{key}" for kid, key in norm_keys if kid in pssh_by_kid]

        # Save any manual keys to the vaults, so they can be reused in future runs.
        for kid, key in norm_keys:
            if kid not in pssh_by_kid:
                continue
            try:
                mgr._store_keys([f"{kid}:{key}"], drm_type or "mp4", "generic", pssh_by_kid[kid] or widevine_pssh, source=None)
            except Exception as exc:
                logger.debug(f"Could not save the manual key for KID {kid} to the vaults (non-fatal): {exc}")

        vault_tagged_keys: list[str] = []
        vault_source_name: str | None = None
        for kid, kid_pssh in pssh_by_kid.items():
            if kid in covered_kids:
                continue
            try:
                result = mgr.resolve_flat_key(kid, kid_pssh, None, drm_type=drm_type or "mp4")
            except Exception as exc:
                logger.debug(f"Manifest vault key resolution failed for KID {kid} (non-fatal): {exc}")
                continue
            if not result:
                continue

            resolved_key, source_label = result
            resolved_keys.append(resolved_key)
            self._pooled_keys.append(resolved_key)
            if source_label and source_label != "manual":
                vault_tagged_keys.append(resolved_key)
                vault_source_name = vault_source_name or source_label

        if not resolved_keys:
            return

        pssh_val = widevine_pssh or next((v for v in pssh_by_kid.values() if v), None)
        drm_label = "Widevine" if widevine_pssh else (drm_type or "unknown DRM")
        mgr._display_keys(
            resolved_keys,
            vault_tagged_keys,
            drm_label,
            pssh_val,
            vault_source_name,
            header=True,
            default_label="manual",
            required_kids=set(pssh_by_kid.keys()),
        )

    def _stop_all(self) -> None:
        if self.download_id:
            download_tracker.request_stop(self.download_id)
        for md, _ in self._active:
            md._stop_event.set()
            md._cancel_all_loops()

    def _manifest_streaming_mux_eligible(self) -> bool:
        """True if conditions are met to attempt multi-manifest named-pipe live mux."""
        if context_tracker.no_livemux:
            return False
        
        if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() != "ffmpeg":
            return False
        
        if not str(self.output_path).lower().endswith(".mkv"):
            return False
        
        if len(self._active) < 2 or self._direct_sources or self._dv_stream is not None:
            return False
        
        try:
            if not get_ffmpeg_path():
                return False
        except Exception:
            return False
        
        has_video_manifest = False
        has_audio_manifest = False
        for md, _ in self._active:
            sel = [s for s in md.streams if s.selected and not s.is_external and s.type in _MEDIA_TYPES]
            if not sel:
                return False
            
            sel_types = {s.type for s in sel}
            if "video" in sel_types and "audio" in sel_types:
                # Manifest carries both — belongs to the single-manifest path, not pipes.
                return False
            
            if "video" in sel_types:
                has_video_manifest = True

            if "audio" in sel_types:
                has_audio_manifest = True

        return has_video_manifest and has_audio_manifest

    def _launch_streaming_mux_manifest(self) -> "NamedPipeMuxer | None":
        """Set up a NamedPipeMuxer for all active manifest sources and wire each MediaDownloader.

        Returns the started muxer, or None if setup failed (caller falls back to normal mux).
        """
        try:
            return self._launch_streaming_mux_manifest_inner()
        except Exception as exc:
            logger.warning(f"streaming_mux[pipes]: setup error ({exc!r}) — falling back to normal mux", exc_info=True)
            return None

    def _start_pipe_ffmpeg_when_audio_ready(self, muxer: "NamedPipeMuxer") -> None:
        audio_paths: list[str] = []
        for md, task_key in muxer.audio_watch:  # type: ignore[attr-defined]
            if not md._track_done_event(task_key).wait(timeout=1800.0):
                logger.warning(f"streaming_mux[pipes]: audio {task_key!r} timed out — falling back to normal mux")
                muxer.abort()
                return
            
            path = md._get_track_result(task_key)
            if path is None:
                logger.warning(f"streaming_mux[pipes]: audio {task_key!r} failed — falling back to normal mux")
                muxer.abort()
                return
            
            audio_paths.append(str(path))
        sub_paths: list[tuple[int, str]] = []
        for idx, (md, task_key) in enumerate(muxer.sub_watch):  # type: ignore[attr-defined]
            if not md._track_done_event(task_key).wait(timeout=1800.0):
                logger.warning(f"streaming_mux[pipes]: subtitle {task_key!r} timed out — leaving it out of live mux")
                continue

            path = md._get_track_result(task_key)
            if path is None:
                logger.warning(f"streaming_mux[pipes]: subtitle {task_key!r} failed — leaving it out of live mux")
                continue

            sub_paths.append((idx, str(path)))
        muxer.start_ffmpeg(muxer.build_cmd(audio_paths, sub_paths))  # type: ignore[attr-defined]

    def _launch_streaming_mux_manifest_inner(self) -> "NamedPipeMuxer | None":
        from VibraVid.core.utils.language import resolve_iso639_2, resolve_language_display_name

        ffmpeg_path = get_ffmpeg_path()
        output_path = os_manager.get_sanitize_path(self.output_path)

        # Only the video is piped. Audio is small and finishes downloading to disk
        # quickly; ffmpeg reads it as a plain file, so it never competes with the
        # video pipe for reads (multiple pipe inputs deadlock on Windows).
        video_md: MediaDownloader | None = None
        video_stream = None
        audio_entries: list[tuple[MediaDownloader, object]] = []
        sub_entries: list[tuple[MediaDownloader, object]] = []

        for md, _ in self._active:
            for s in md.streams:
                if not s.selected or getattr(s, "is_external", False):
                    continue
                if s.type == "video" and video_stream is None:
                    video_md, video_stream = md, s
                elif s.type == "audio":
                    audio_entries.append((md, s))
                elif s.type == "subtitle":
                    sub_entries.append((md, s))

        if video_stream is None or video_md is None or not audio_entries:
            logger.info("streaming_mux[pipes]: no clear video+audio split — skipping")
            return None

        muxer = NamedPipeMuxer(n_pipes=1)
        video_pipe = muxer.pipe_paths[0]
        video_md.enable_relay_mux(muxer.get_writer(0))  # type: ignore[arg-type]

        audio_watch: list[tuple[MediaDownloader, str]] = [
            (md, md._stream_task_key(s)) for md, s in audio_entries
        ]
        sub_watch: list[tuple[MediaDownloader, str]] = [
            (md, md._stream_task_key(s)) for md, s in sub_entries
        ]
        audio_meta = [s for _, s in audio_entries]
        sub_meta = [s for _, s in sub_entries]
        video_codecs = (getattr(video_stream, "codecs", "") or "").lower()

        def _lang_meta(kind: str, idx: int, s_t) -> list[str]:
            lang = getattr(s_t, "resolved_language", "") or getattr(s_t, "language", "") or "und"
            title = resolve_language_display_name(lang)
            iso = resolve_iso639_2(lang)
            return [
                f"-metadata:s:{kind}:{idx}", f"title={title}",
                f"-metadata:s:{kind}:{idx}", f"language={iso}",
                f"-metadata:s:{kind}:{idx}", f"handler_name={title}",
            ]

        def _build_cmd(audio_paths: list[str], sub_paths: list[tuple[int, str]]) -> list[str]:
            cmd = [ffmpeg_path, "-y", "-f", "mp4", "-i", video_pipe]
            for p in audio_paths + [p for _, p in sub_paths]:
                cmd += ["-i", p]

            cmd += ["-map", "0:v:0"]
            for j in range(len(audio_paths)):
                cmd += ["-map", f"{j + 1}:a"]

            for k in range(len(sub_paths)):
                cmd += ["-map", f"{len(audio_paths) + k + 1}:s"]

            if any(p in video_codecs for p in ("hev", "hvc", "dvh", "dvhe")):
                cmd += ["-tag:v", "hvc1"]

            for j, s_a in enumerate(audio_meta[: len(audio_paths)]):
                cmd += _lang_meta("a", j, s_a)
                cmd += [f"-disposition:a:{j}", "default" if j == 0 else "0"]

            for k, (sub_idx, _) in enumerate(sub_paths):
                cmd += _lang_meta("s", k, sub_meta[sub_idx])
                cmd += [f"-disposition:s:{k}", "0"]
            
            cmd += ["-c", "copy", output_path]
            return cmd

        muxer.audio_watch = audio_watch     # type: ignore[attr-defined]
        muxer.sub_watch = sub_watch         # type: ignore[attr-defined]
        muxer.build_cmd = _build_cmd        # type: ignore[attr-defined]
        muxer.prepare()
        logger.info(f"streaming_mux[pipes]: prepared video pipe, waiting for {len(audio_watch)} audio track(s) -> {output_path}")
        return muxer

    def _run_downloads(self) -> bool:
        """Download every selected stream of every source concurrently on ONE shared progress bar. Returns False if cancelled (Ctrl+C)."""
        self._preresolve_manifest_keys()
        pipe_muxer: NamedPipeMuxer | None = (
            self._launch_streaming_mux_manifest() if self._manifest_streaming_mux_eligible() else None
        )

        if pipe_muxer is not None:
            threading.Thread(
                target=self._start_pipe_ffmpeg_when_audio_ready,
                args=(pipe_muxer,),
                daemon=True,
                name="pipe-mux-ffmpeg-starter",
            ).start()

        for md, source in self._active:
            md.set_key(self._pooled_keys)
            sel = [s for s in md.streams if s.selected and not s.is_external and s.type in _MEDIA_TYPES]

            # Warn early if a source has encrypted tracks but no way to decrypt them.
            # Without this the file silently merges still-encrypted (the audio-without-key case).
            def _is_encrypted(s) -> bool:
                drm = getattr(s, "drm", None)
                try:
                    return bool(drm and drm.is_encrypted())
                except Exception:
                    return False

            encrypted_sel = [s for s in sel if _is_encrypted(s)]
            label = source.get("label") or (str(source.get("url") or "?")[:60])

            if encrypted_sel and not self._pooled_keys and not source.get("license_url"):
                kinds = ", ".join(sorted({s.type for s in encrypted_sel}))
                console.print(
                    f"[bold red][!] WARNING[/bold red] Source '[yellow]{label}[/yellow]': "
                    f"{len(encrypted_sel)} encrypted track(s) ([cyan]{kinds}[/cyan]) but no "
                    f"[bold]key[/bold]/[bold]license_url[/bold] provided - these tracks will stay encrypted."
                )
                logger.error(f"Generic source '{label}': encrypted {kinds} stream(s) without key/license — will remain encrypted")

            # Warn early per-track when keys ARE provided (anywhere in the pool) but none of
            # them match this specific track's KID
            elif encrypted_sel and self._pooled_keys and not source.get("license_url"):
                provided_kids = {kid.lower() for kid, _ in KeysManager.normalize(self._pooled_keys)}
                for s in encrypted_sel:
                    track_kids = {k.lower() for k in (s.drm.get_all_kids() if s.drm else [])}
                    if track_kids and provided_kids.isdisjoint(track_kids):
                        track_label = f"{s.type} {s.resolution or s.language or ''}".strip()
                        console.print(f"[bold red][!] WARNING[/bold red] Source '[yellow]{label}[/yellow]': track [yellow]{track_label}[/yellow] needs KID(s) [magenta]{', '.join(track_kids)}[/magenta] ")
                        logger.error(f"Generic source '{label}': track {track_label} KID(s) {track_kids} not covered by provided keys")

            md._session_live_decrypt = (
                bool(sel)
                and not context_tracker.skip_decrypt
                and all(getattr(s, "supports_live_decryption", False) for s in sel)
            )
            md._prepare_labels()

        def _safe_download(md, stream, bm) -> None:
            try:
                md._download_stream(stream, bm)
            except Exception as exc:
                logger.error(f"Stream download error ({stream.type}/{getattr(stream, 'language', '')}): {exc}", exc_info=True)

        bar = DownloadBarManager(self.download_id)
        stop_event = threading.Event()
        threads: list[threading.Thread] = []
        try:
            with bar as bm:
                for md, _ in self._active:
                    bm.add_prebuilt_tasks(md._get_prebuilt_tasks())

                for md, _ in self._active:
                    for s in md.streams:
                        if s.selected and not s.is_external and s.type in _MEDIA_TYPES:
                            t = threading.Thread(target=_safe_download, args=(md, s, bm), daemon=True)
                            threads.append(t)
                            t.start()

                join_interruptible(threads, stop_event)
                bm.finish_all_tasks()

        except KeyboardInterrupt:
            logger.warning("KeyboardInterrupt — stopping all hybrid sources")
            self._stop_all()
            stop_event.set()
            if pipe_muxer is not None:
                pipe_muxer.abort()
            join_interruptible(threads, threading.Event(), hard_timeout=15.0)
            return False

        if any(md._stop_check() for md, _ in self._active):
            if pipe_muxer is not None:
                pipe_muxer.abort()
            return False

        if pipe_muxer is not None:
            for i in range(len(pipe_muxer.pipe_paths)):
                pipe_muxer.close_writer(i)
                
            mux_result = pipe_muxer.finish()
            if mux_result.ok:
                self._pipe_mux_result = os_manager.get_sanitize_path(self.output_path)
                logger.info(f"streaming_mux[pipes]: finished -> {self._pipe_mux_result}")
            else:
                logger.warning(f"streaming_mux[pipes]: ffmpeg failed ({mux_result.error}) — falling back to normal mux")
                if mux_result.stderr_tail:
                    logger.warning(f"streaming_mux[pipes]: stderr tail:\n{mux_result.stderr_tail[-500:]}")

        return True

    def _dv_entry(self, video_track: dict[str, Any]) -> dict[str, Any]:
        """Wrap the downloaded Dolby Vision video file as an 'other video' track consumable by ``build_hybrid_output`` (mkvmerge)."""
        path = video_track["path"]
        probe = probe_media_file(path) or {}
        entry = {
            "path": path,
            "url": "",
            "type": "video:dv",
            "kind": "video",
            "tag": "dv",
            "language": "und",
            "name": "Dolby Vision",
            "size": video_track.get("size", 0),
            "probe": probe,
        }
        entry.update(probe)
        return entry

    def _collect_status(self) -> dict[str, Any]:
        """Assemble a combined status dict (video/audios/subtitles) for muxing."""
        status: dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": [],
            "external_audios": [],
            "external_subtitles": [],
            "other_tracks": [],
            "other_tracks_downloaded": [],
        }

        for md, _ in self._active:
            md_status = md._build_status([], [])

            sel_audio_langs = [
                (getattr(s, "resolved_language", "") or getattr(s, "language", "") or "und")
                for s in md.streams
                if s.selected and s.type == "audio"
            ]

            md_video = md_status.get("video")
            if md_video:
                is_dv = self._dv_stream is not None and self._dv_stream in md.streams

                if is_dv:
                    status["other_tracks_downloaded"].append(self._dv_entry(md_video))
                elif status["video"] is None:
                    status["video"] = md_video

            for i, a in enumerate(md_status.get("audios", []) or []):
                lang = sel_audio_langs[i] if i < len(sel_audio_langs) else (a.get("name") or "und")
                status["audios"].append(
                    {**a, "name": a.get("name") or lang, "language": lang, **language_variants(lang)}
                )

            for sub in md_status.get("subtitles", []) or []:
                if sub.get("path"):
                    status["subtitles"].append(sub)

        for entry in self._direct_sources:
            result = entry.get("result")
            if not result:
                continue

            kind = result["kind"]
            lang = result["language"]
            is_dv = result.get("role", "").partition(":")[2].strip().lower() == "dv"

            if kind in ("video", "vid") and is_dv:
                status["other_tracks_downloaded"].append(self._dv_entry(result))
            elif kind in ("video", "vid"):
                if status["video"] is None:
                    status["video"] = {"path": result["path"], "size": result["size"]}
            elif kind in ("audio", "aud"):
                status["audios"].append(
                    {
                        "path": result["path"],
                        "name": lang,
                        "language": lang,
                        "size": result["size"],
                        **language_variants(lang),
                    }
                )
            elif kind in ("subtitle", "sub"):
                status["subtitles"].append(
                    {
                        "path": result["path"],
                        "name": lang,
                        "language": lang,
                        "size": result["size"],
                    }
                )
            else:
                logger.warning(f"Direct source '{entry['label']}' has unknown kind '{kind}' — treating as video")
                if status["video"] is None:
                    status["video"] = {"path": result["path"], "size": result["size"]}

        return status

    def start(self) -> DownloadResult:
        try:
            return self._start()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupt received — stopping all sources...")
            logger.warning("KeyboardInterrupt during hybrid pipeline")
            self._stop_all()
            return self._fail("cancelled")

    def _start(self) -> DownloadResult:
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return DownloadResult(self.output_path, False, None)

        os_manager.create_path(self.output_dir)

        if self.chapters:
            logger.info(f"Adding {len(self.chapters)} external chapter(s).")

        # ── 1) Parse every source (manifest-based sources here; plain .mp4/.m4a/... sources are split off into self._direct_sources instead)
        parsed = self._parse_sources()
        if not parsed and not self._direct_sources:
            return DownloadResult(None, True, "no sources parsed")

        # ── 2-3) Merge + dedup + single selection (only meaningful for manifest sources)
        selected = self._select(parsed) if parsed else []

        all_streams = [s for md, _ in parsed for s in md.streams if not s.is_external]
        if all_streams:
            console.print(build_table(all_streams))

        if not selected and not self._direct_sources:
            console.print("[yellow][HYBRID] No track selected.")
            return DownloadResult(None, True, "no tracks selected")

        if self._no_match:
            console.print("[yellow]Skipping — no track matched the requested video/audio/subtitle filter (-sv/-sa/-ss).")
            return DownloadResult(self.output_path, False, None)

        self._active = [
            (md, src)
            for md, src in parsed
            if any(s.selected and not s.is_external and s.type in _MEDIA_TYPES for s in md.streams)
        ]
        self._setup_dv_companion()
        self._active = [
            (md, src)
            for md, src in self._active
            if any(s.selected and not s.is_external and s.type in _MEDIA_TYPES for s in md.streams)
        ]

        # ── 4) Concurrent download (clean Ctrl+C)
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        if self._direct_sources and not self._download_direct_sources():
            return self._fail("cancelled")

        if not self._run_downloads():
            return self._fail("cancelled")

        # ── 5) Mux
        status = self._collect_status()
        if self._no_media_downloaded(status):
            logger.error("No media downloaded")
            return self._fail("No media downloaded")

        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        self.media_downloader = SimpleNamespace(
            decrypt_failures=[f for md, _src in self._active for f in getattr(md, "decrypt_failures", None) or []],
            streaming_mux_result=self._pipe_mux_result or self._direct_streaming_mux_result,
            streaming_mux_chapters_injected=False,
        )

        final_file = self._merge_files(status)
        if not final_file:
            return self._fail(self.error or "Merge failed")

        self._finalize(final_file=final_file)
        return DownloadResult(self.output_path, False, self.error or None)
