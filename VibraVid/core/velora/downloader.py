# 09.04.26

import asyncio
import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.velora.bridge import run_download_plan, velora_supports_max_speed
from VibraVid.core.velora.curl_bridge import run_download_plan_curl_cffi
from VibraVid.core.velora.subtitle import download_external_tracks_with_progress
from VibraVid.setup import get_flux_path
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_proxy_url
from VibraVid.utils.vault import all_vaults

from ._decrypt_pipeline import DecryptPipelineMixin
from ._ism_postproc import IsmPostprocMixin
from ._multiperiod import MultiPeriodMixin
from ._stream_vod import VodStreamMixin
from .base import BaseMediaDownloader
from .downloader_live import LiveDownloadMixin
from .util._stream_helpers import (
    SilentDownloadBarManager,
    detect_seg_ext,
    join_interruptible,
    print_failed_segments_report,
    safe_name,
)

logger = logging.getLogger("manual")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
VERIFY_TLS = config_manager.config.get_bool("REQUESTS", "verify")
SEGMENT_DELAY_SECONDS = max(0.0, config_manager.config.get_float("DOWNLOAD", "segment_delay_seconds"))
SEGMENT_DELAY_JITTER_SECONDS = max(0.0, config_manager.config.get_float("DOWNLOAD", "segment_delay_jitter_seconds"))


class MediaDownloader(
    LiveDownloadMixin, VodStreamMixin, MultiPeriodMixin, DecryptPipelineMixin, IsmPostprocMixin, BaseMediaDownloader
):
    _max_speed_warned = False  # warn once per process when --max-speed can't be honoured

    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str,
        headers: dict | None = None,
        key: Any | None = None,
        cookies: dict | None = None,
        download_id: str | None = None,
        site_name: str | None = None,
        max_segments: int | tuple[int, int | None] | None = None,
        max_time: float | tuple[float, float | None] | None = None,
        manifest_content: str | None = None,
        manifest_protocol: str | None = None,
        manifest_refresh_fn=None,
        has_drm: bool = False,
        display_selected_only: bool = False,
    ) -> None:
        super().__init__(
            url=url,
            output_dir=output_dir,
            filename=filename,
            headers=headers,
            key=key,
            cookies=cookies,
            download_id=download_id,
            site_name=site_name,
            manifest_content=manifest_content,
            manifest_protocol=manifest_protocol,
            manifest_refresh_fn=manifest_refresh_fn,
            has_drm=has_drm,
            display_selected_only=display_selected_only,
        )
        self.max_segments = max_segments
        self.max_time = max_time

        # Cancellation
        self._stop_event: threading.Event = threading.Event()
        self._abort_event: threading.Event = threading.Event()
        self._active_loops: list[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock = threading.Lock()

        # Live-decryption tracking
        self._session_live_decrypt: bool = False

        # Failed-segment accumulator
        self._failed_segments: list = []
        self._failed_segments_lock = threading.Lock()
        self.had_failed_segments: int = 0

        # Decryption-failure accumulator: per-track records for streams that are still encrypted after decrypt
        self.decrypt_failures: list = []
        self._decrypt_failures_lock = threading.Lock()

        # KIDs whose stored key was PROVEN wrong by a key-sanity check
        self._wrong_key_kids: set[str] = set()
        self._wrong_key_kids_lock = threading.Lock()

        # Per-stream "this track is fully written to its final output_dir path" signal, set by _download_stream_generic() just before it returns.
        self._track_done_events: dict[str, threading.Event] = {}
        self._track_results: dict[str, Path | None] = {}
        self._track_done_lock = threading.Lock()

        # Set via enable_streaming_mux() by the outer downloader (VibraVid.core.downloader.*) BEFORE start_download() is called
        self._streaming_mux_output_path: str | None = None
        self._streaming_mux_chapters: list = []

        # Set via enable_relay_mux() for multi-manifest named-pipe live mux.
        self._relay_mux_writer: Callable[[bytes], None] | dict[int, Callable[[bytes], None]] | None = None

        # Set by the video stream's own _download_stream_generic() call if the fast path
        # actually completed successfully.
        self.streaming_mux_result: str | None = None
        
        # Set True by _try_start_streaming_mux_inner() if it injected the downloader's
        # queued chapters into the same ffmpeg pass -- lets the caller (BaseDownloader._merge_files)
        # skip the separate mkvmerge/ffmpeg _inject_chapters() post-processing step.
        self.streaming_mux_chapters_injected: bool = False

    def enable_streaming_mux(self, output_path: str, chapters: list | None = None) -> None:
        self._streaming_mux_output_path = output_path
        self._streaming_mux_chapters = list(chapters or [])

    def enable_relay_mux(self, writer_fn: "Callable[[bytes], None] | dict[int, Callable[[bytes], None]]") -> None:
        """Route this downloader's decoded chunks to *writer_fn* (named-pipe relay)."""
        self._relay_mux_writer = writer_fn

    def _track_done_event(self, task_key: str) -> threading.Event:
        with self._track_done_lock:
            event = self._track_done_events.get(task_key)
            if event is None:
                event = threading.Event()
                self._track_done_events[task_key] = event
            return event

    def _record_track_done(self, task_key: str, out_path: Path | None) -> None:
        with self._track_done_lock:
            self._track_results[task_key] = out_path
        self._track_done_event(task_key).set()

    def _get_track_result(self, task_key: str) -> Path | None:
        with self._track_done_lock:
            return self._track_results.get(task_key)

    @staticmethod
    def _stream_kids(stream) -> set[str]:
        """Lowercased KIDs advertised by *stream*'s manifest DRM (empty when unknown)."""
        drm = getattr(stream, "drm", None)
        if drm is None:
            return set()
        try:
            kids = drm.get_all_kids() or []
        except Exception:
            return set()
        return {str(k).lower() for k in kids if k}

    def _register_wrong_key(self, kids, license_url: str | None = None, pssh: str | None = None) -> None:
        """Mark the given KID(s) as having been proven wrong-key, and notify all connected vaults."""
        kids = {str(k).lower() for k in (kids or []) if k}
        if not kids:
            return
        
        with self._wrong_key_kids_lock:
            new = kids - self._wrong_key_kids
            self._wrong_key_kids |= kids
        
        if new:
            logger.error(f"Wrong key confirmed for KID(s) {sorted(new)} -- terminating download/decrypt for all tracks sharing them")
            self._report_wrong_key_to_vaults(new, license_url, pssh)

    def _report_wrong_key_to_vaults(self, kids: set[str], license_url: str | None, pssh: str | None) -> None:
        """Fire-and-forget: tell each connected vault about the confirmed-wrong (kid, key) pairs."""
        if not license_url:
            logger.debug("_report_wrong_key_to_vaults: no license_url in scope -- skipping vault report")
            return

        pairs = [(kid, key) for kid, key in KeysManager.normalize(self.key) if kid.lower() in kids]
        if not pairs:
            return

        def _run():
            for kid, key in pairs:
                for vault in all_vaults():
                    try:
                        if vault.is_connected:
                            vault.report_wrong_key(kid, key, license_url, pssh)
                    except Exception as e:
                        logger.debug(f"report_wrong_key failed for {kid} on {getattr(vault, 'name', vault)} (non-fatal): {e}")

        threading.Thread(target=_run, daemon=True, name="report-wrong-key").start()

    def _kids_poisoned(self, kids) -> str | None:
        """Return the first of *kids* already proven wrong-key, or None."""
        if not kids:
            return None
        with self._wrong_key_kids_lock:
            for k in kids:
                kl = str(k).lower()
                if kl in self._wrong_key_kids:
                    return kl
        return None

    def start_download(self, show_progress: bool = True) -> dict[str, Any]:
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        self._promote_hls_subtitles_to_external()
        self._prepare_labels()

        selected_media = [
            s
            for s in self.streams
            if (s.selected or getattr(s, "dv_companion", False))
            and not s.is_external
            and s.type in ("video", "audio", "subtitle")
        ]
        all_support_live = all(s.supports_live_decryption for s in selected_media) if selected_media else False
        flux_available = bool(get_flux_path())

        # Live (in-flight) decryption is automatic: it engages whenever every
        # selected stream is truly segmented (a real init/moov per the manifest).
        # The per-stream `_frag_init_probe()` in `_stream_vod.py` downgrades to the
        # post-download decrypt pass if the first init turns out not to be a valid
        # ftyp+moov, so there is no config knob to get wrong.
        if all_support_live and selected_media and flux_available and not context_tracker.skip_decrypt:
            self._session_live_decrypt = True
            logger.info("All selected streams support live decryption — using in-flight decryption.")

        ext_result: dict[str, Any] = {"ext_subs": [], "ext_auds": []}
        spawned_threads: list[threading.Thread] = []

        try:
            bar_ctx = (
                DownloadBarManager(self.download_id) if show_progress else SilentDownloadBarManager(self.download_id)
            )

            with bar_ctx as bar_manager:
                bar_manager.add_prebuilt_tasks(self._get_prebuilt_tasks())
                self._register_external_track_tasks(bar_manager)

                ext_loop = asyncio.new_event_loop()
                self._register_loop(ext_loop)
                try:
                    signal.set_wakeup_fd(-1)
                except Exception:
                    pass
                _parent_http_version = context_tracker.http_version

                def _run_externals() -> None:
                    asyncio.set_event_loop(ext_loop)
                    context_tracker.http_version = _parent_http_version
                    try:
                        subs, auds = ext_loop.run_until_complete(
                            download_external_tracks_with_progress(
                                self.headers,
                                self.external_subtitles,
                                self.external_audios,
                                self.output_dir,
                                self.filename,
                                bar_manager,
                                stop_check=self._stop_check,
                                on_track_done=self._record_track_done,
                            )
                        )
                        ext_result["ext_subs"] = subs
                        ext_result["ext_auds"] = auds

                    except Exception as exc:
                        logger.error(f"External downloads failed: {exc}")

                    finally:
                        self._unregister_loop(ext_loop)
                        ext_loop.close()

                # context_tracker is threading.local()-backed, so a freshly spawned thread
                # starts with the defaults, not the parent's values -- capture them here
                # and reapply inside each worker (same pattern as capture.py's _output_worker).
                _parent_skip_decrypt = context_tracker.skip_decrypt
                _parent_no_livemux = context_tracker.no_livemux
                _parent_force_livemux = context_tracker.force_livemux
                _parent_no_concurrent = context_tracker.no_concurrent
                _parent_log_engine_output = context_tracker.log_engine_output

                def _run_stream(s) -> None:
                    context_tracker.skip_decrypt = _parent_skip_decrypt
                    context_tracker.no_livemux = _parent_no_livemux
                    context_tracker.force_livemux = _parent_force_livemux
                    context_tracker.no_concurrent = _parent_no_concurrent
                    context_tracker.log_engine_output = _parent_log_engine_output
                    context_tracker.http_version = _parent_http_version
                    try:
                        self._download_stream(s, bar_manager)
                    except Exception as exc:
                        logger.error(f"Stream download error ({s.type}/{s.language}): {exc}", exc_info=True)

                # Live recordings can legitimately run far longer than join_interruptible's default 2h hard_timeout
                is_live_session = any(getattr(s, "is_live", False) for s in selected_media)
                media_hard_timeout = float("inf") if is_live_session else 7200.0

                if not context_tracker.no_concurrent:
                    ext_thread = threading.Thread(target=_run_externals, daemon=True)
                    spawned_threads.append(ext_thread)
                    ext_thread.start()

                    media_threads: list[threading.Thread] = []
                    for stream in selected_media:
                        t = threading.Thread(target=_run_stream, args=(stream,), daemon=True)
                        media_threads.append(t)
                        spawned_threads.append(t)
                        t.start()

                    join_interruptible(media_threads, self._stop_event, hard_timeout=media_hard_timeout)
                    bar_manager.finish_all_tasks()
                    join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                else:
                    logger.info("Sequential download: video -> audio -> subtitles -> external tracks.")
                    video_streams = [s for s in selected_media if s.type == "video"]
                    audio_streams = [s for s in selected_media if s.type == "audio"]
                    sub_streams = [s for s in selected_media if s.type == "subtitle"]

                    for stream in video_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        spawned_threads.append(t)
                        t.start()
                        join_interruptible([t], self._stop_event, hard_timeout=media_hard_timeout)

                    for stream in audio_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        spawned_threads.append(t)
                        t.start()
                        join_interruptible([t], self._stop_event, hard_timeout=media_hard_timeout)

                    for stream in sub_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        spawned_threads.append(t)
                        t.start()
                        join_interruptible([t], self._stop_event, hard_timeout=media_hard_timeout)

                    bar_manager.finish_all_tasks()

                    if not self._stop_check():
                        ext_thread = threading.Thread(target=_run_externals, daemon=True)
                        spawned_threads.append(ext_thread)
                        ext_thread.start()
                        join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                ext_subs = ext_result["ext_subs"]
                ext_auds = ext_result["ext_auds"]

        except KeyboardInterrupt:
            self._stop_event.set()
            self._cancel_all_loops()
            if self.download_id:
                download_tracker.request_stop(self.download_id)

            console.print("\n[yellow]Stopping — finishing the current segment and merging what was downloaded... (Ctrl+C again to abort without merging)")
            logger.info("KeyboardInterrupt: waiting for stream threads to finish merging before returning")

            if not self._wait_after_interrupt(spawned_threads):
                console.print("\n[red]Aborted — merge skipped.")
                logger.warning("Second KeyboardInterrupt: aborting without merging")
                return {"error": "cancelled"}

            ext_subs = ext_result.get("ext_subs", [])
            ext_auds = ext_result.get("ext_auds", [])

        if self._failed_segments:
            print_failed_segments_report(self._failed_segments)
            with self._failed_segments_lock:
                self.had_failed_segments = sum(len(failed) for _, failed in self._failed_segments)
            self._failed_segments.clear()

        self.status = self._build_status(ext_subs, ext_auds)

        # A stop (Ctrl+C or a tracker-level request, e.g. a live source going
        # offline) can still have produced a fully merged file by the time we
        # get here — only treat it as a cancellation if nothing was produced.
        # Guard on decrypt_failures too so a real decrypt failure never gets
        # swallowed as a plain "cancelled" and skips the "Decryption failed"
        # reporting in _decrypt_failure_message().
        was_stopped = self._stop_event.is_set() or bool(
            self.download_id and download_tracker.is_stopped(self.download_id)
        )
        if was_stopped and not self.decrypt_failures and not self.status.get("video") and not self.status.get("audios"):
            return {"error": "cancelled"}

        return self.status

    def _wait_after_interrupt(self, threads: list[threading.Thread], max_wait: float = 900.0, poll: float = 0.25, notice_every: float = 5.0) -> bool:
        """Let the stream threads finish after the first Ctrl+C (a big video needs time to finalize) and return True once they all have."""
        started = time.monotonic()
        next_notice = started + notice_every
        try:
            while True:
                alive = [t for t in threads if t.is_alive()]
                if not alive:
                    return True

                now = time.monotonic()
                if now - started >= max_wait:
                    console.print(f"\n[red]{len(alive)} track(s) still running after {max_wait:.0f}s: not merging, the partial files are kept.")
                    break
                if now >= next_notice:
                    console.print(f"\n[yellow]Finalizing {len(alive)} track(s)... (Ctrl+C again to abort without merging)")
                    next_notice = now + notice_every
                alive[0].join(timeout=poll)
        except KeyboardInterrupt:
            pass

        self._abort_event.set()
        self._cancel_all_loops()
        if self.download_id:
            download_tracker.request_stop(self.download_id)
        return False

    def _register_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._loops_lock:
            self._active_loops.append(loop)

    def _unregister_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._loops_lock:
            try:
                self._active_loops.remove(loop)
            except ValueError:
                pass

    def _cancel_all_loops(self) -> None:
        with self._loops_lock:
            for loop in list(self._active_loops):
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass

    def _stop_check(self) -> bool:
        return self._stop_event.is_set() or bool(self.download_id and download_tracker.is_stopped(self.download_id))

    def _run_dl(
        self,
        segs: list[dict],
        out_dir: Path,
        headers: dict,
        progress_cb,
        stream=None,
        event_cb=None,
        default_ext: str = "ts",
        stop_check=None,
    ) -> list[Path]:
        try:
            plan_task_key = self._stream_task_key(stream) if stream else "download"
            if stream and stream.type == "video":
                plan_label = self._video_labels_by_task_key.get(plan_task_key) or self._video_label

            elif stream and stream.type == "audio":
                plan_label = self._audio_labels_by_task_key.get(plan_task_key) or self._audio_labels.get(
                    (stream.language or "und").lower(), ""
                )

            elif stream and stream.type == "subtitle":
                plan_label = self._sub_labels_by_task_key.get(plan_task_key, "")
                if not plan_label:
                    lang_raw = (stream.language or "und").lower()
                    plan_label = self._sub_labels.get(lang_raw) or self._sub_labels.get(lang_raw.split("-")[0]) or ""

            else:
                plan_label = ""

            logger.debug(f"Starting download plan for {plan_task_key} with {len(segs)} segments")
            plan_label_or_key = plan_label or plan_task_key
            tasks = []
            for seg in segs:
                seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                if seg_ext == "m4s":
                    seg_ext = "mp4"

                tasks.append(
                    {
                        "task_key": plan_task_key,
                        "label": plan_label_or_key,
                        "display_label": plan_label_or_key,
                        "url": seg["url"],
                        "path": str(out_dir / f"seg_{seg['number']:05d}.{seg_ext}"),
                        "headers": seg.get("headers", {}),
                    }
                )

            http_version = getattr(context_tracker, "http_version", None) or "1.1"
            plan = {
                "project": "Velora",
                "version": 1,
                "task_key": plan_task_key,
                "label": plan_label_or_key,
                "display_label": plan_label_or_key,
                "concurrency": THREAD_COUNT,
                "retry_count": RETRY_COUNT,
                "timeout_seconds": REQUEST_TIMEOUT,
                "retry_base_delay_seconds": 1.0,
                "retry_max_delay_seconds": 4.0,
                "retry_jitter_seconds": 0.25,
                "segment_delay_seconds": SEGMENT_DELAY_SECONDS,
                "segment_delay_jitter_seconds": SEGMENT_DELAY_JITTER_SECONDS,
                "proxy_url": get_proxy_url(),
                "verify_tls": VERIFY_TLS,
                "http_version": http_version,
                "headers": headers,
                "tasks": tasks,
            }
            use_curl_cffi = config_manager.config.get_bool("DOWNLOAD", "use_curl_cffi_segments")
            max_speed_bps = int(config_manager.config.get_float("DOWNLOAD", "max_speed_mbps", default=0.0) * 1_000_000)
            if max_speed_bps > 0:
                
                # Older Velora builds reject plan fields they don't know, so only send it to one that supports it
                if use_curl_cffi or velora_supports_max_speed():
                    plan["max_speed_bytes_per_sec"] = max_speed_bps
                elif not MediaDownloader._max_speed_warned:
                    MediaDownloader._max_speed_warned = True
                    console.print("[yellow]--max-speed ignored: the installed Velora is older than 2.2.0 (update it with --binary-update). Downloading without a speed limit.")
            
            if stream is not None and not stream.estimated_size:
                stream.compute_estimated_size()
            
            known_total = int(getattr(stream, "estimated_size", 0) or 0) if stream else 0
            known_exact = bool(getattr(stream, "estimated_size_exact", False)) if stream else True
            backend = run_download_plan_curl_cffi if use_curl_cffi else run_download_plan
            results = backend(plan, progress_cb=progress_cb, event_cb=event_cb, stop_check=stop_check or self._stop_check, known_total=known_total, known_exact=known_exact)
            return [Path(item["path"]) for item in results if item.get("path")]

        except Exception as exc:
            logger.error(f"_run_dl failed: {exc}", exc_info=True)
            return []

    def _build_headers(self) -> dict:
        h = dict(self.headers)

        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        if "Referer" not in h and "referer" not in h:
            try:
                parsed = urlparse(self.url)
                h["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
            except Exception:
                pass

        h.setdefault("Accept", "*/*")
        h.setdefault("Accept-Encoding", "gzip, deflate")
        return h

    def _out_filename(self, stream, ext: str) -> str:
        if stream.type == "video":
            if getattr(stream, "dv_companion", False):
                return f"{self.filename}.dv.{ext}"
            return f"{self.filename}.{ext}"

        raw_lang = getattr(stream, "resolved_language", "") or stream.language or "und"
        lang = safe_name(raw_lang.lower())
        if stream.type == "subtitle":
            if getattr(stream, "forced", False):
                lang = f"{lang}_forced"
            elif getattr(stream, "is_sdh", False):
                lang = f"{lang}_sdh"
            elif getattr(stream, "is_cc", False):
                lang = f"{lang}_cc"

            if getattr(stream, "is_wvtt_mp4", False):
                base = f"{self.filename}.{lang}.wvtt"
            else:
                _protocols = ("dash", "hls", "mp4", "m4s", "ts", "m2ts", "")
                fmt = (stream.format or "").lower().strip()
                seg = (ext or "").lower().strip()
                sub_ext = fmt if fmt not in _protocols else (seg if seg not in _protocols else "vtt")
                base = f"{self.filename}.{lang}.{sub_ext}"

            with self._assigned_sub_lock:
                if base not in self._assigned_sub_names:
                    self._assigned_sub_names.add(base)
                    return base
                counter = 2
                while True:
                    stem, _, ext_part = base.rpartition(".")
                    candidate = f"{stem}_{counter}.{ext_part}"
                    if candidate not in self._assigned_sub_names:
                        self._assigned_sub_names.add(candidate)
                        return candidate
                    counter += 1

        audio_ext = "webm" if ext == "webm" else "m4a"
        return f"{self.filename}.{lang}.{audio_ext}"
