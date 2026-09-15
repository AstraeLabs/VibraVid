# 01.04.25

import gzip
import logging
import os
import queue
import re
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rich.text import Text

from VibraVid.core.decryptor import Decryptor
from VibraVid.core.manifest.stream import track_label
from VibraVid.core.muxing.helper.sub.convert import convert_subtitle, extract_vtt_from_wvtt_mp4
from VibraVid.core.muxing.helper.video import binary_merge_segments, concat_demux_merge_segments
from VibraVid.core.muxing.helper.video.ts import is_mpegts_file
from VibraVid.core.muxing.streaming_mux import StreamingMuxFeeder
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import resolve_iso639_2, resolve_language_display_name
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

from ..decryptor._segment_crypto import decrypt_aes128_file
from .curl_bridge import _fetch_one
from .util._cenc_init import strip_cenc_signaling
from .util._stream_helpers import (
    collect_failed_segments,
    describe_key_for_log,
    detect_seg_ext,
    extract_redirect_url,
    is_valid_frag_init,
    looks_like_bare_fragment,
    merged_segment_ext,
    parse_range_header,
    repair_init_segment,
)
from .util._subtitle_segments import merge_vtt_files
from .util.formatting import (
    estimate_total_size as _estimate_total_size,
)
from .util.formatting import (
    fmt_dur as _fmt_dur,
)
from .util.formatting import (
    format_size as _fmt_size,
)
from .util.formatting import (
    format_speed as _fmt_speed,
)
from .util.formatting import (
    normalize_path_key,
)

logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
MAX_TOKEN_REFRESH_ROUNDS = config_manager.config.get_int("DOWNLOAD", "max_token_refresh_rounds")
TOKEN_REFRESH_BACKOFF_SECONDS = config_manager.config.get_float(
    "DOWNLOAD", "token_refresh_backoff_seconds", default=2.0
)
TOKEN_REFRESH_STALL_ROUNDS = max(1, config_manager.config.get_int("DOWNLOAD", "token_refresh_stall_rounds", default=3))
STREAMING_MUX_MIN_WAIT_SECONDS = 60.0
STREAMING_MUX_MAX_WAIT_SECONDS = 900.0 
STREAMING_MUX_SECONDS_PER_SEGMENT = 0.2
STREAMING_MUX_MIN_THROUGHPUT_BPS = 1_048_576
_LIVE_MERGE_BUFSIZE = 2 * 1024 * 1024


def _estimate_livemux_wait_seconds(stream: Any) -> float:
    """How long to wait for *stream* to finish downloading before giving up on the live-mux fast path, estimated from its segment count and/or estimated byte size instead of one fixed timeout """
    segs = len(getattr(stream, "segments", None) or [])
    from_segs = segs * STREAMING_MUX_SECONDS_PER_SEGMENT

    try:
        size = stream.compute_estimated_size()
    except Exception:
        size = 0
    from_size = (size / STREAMING_MUX_MIN_THROUGHPUT_BPS) if size else 0.0

    return min(STREAMING_MUX_MAX_WAIT_SECONDS, max(STREAMING_MUX_MIN_WAIT_SECONDS, from_segs, from_size))


def _reads_as_self_initializing_mp4(path: Path) -> bool:
    """True if *path* starts with its own `ftyp` box — a complete, independently decryptable MP4 document — rather than a bare `moof`/`mdat` fragment meant to share a separate init segment."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return len(head) == 8 and head[4:8] == b"ftyp"


def _skip_post_decrypt() -> bool:
    """Debug switch (--no-decrypt CLI flag): leave segments encrypted, no decrypt at all."""
    return context_tracker.skip_decrypt


def _streaming_mux_enabled() -> bool:
    """Fast path is on by default; --no-livemux CLI flag disables it for this run."""
    return not context_tracker.no_livemux


class _LiveMerger:
    def __init__(self, out_path: Path, expected_order: list, on_chunk: Callable[[bytes], None] | None = None):
        """
        Args:
            - out_path: Path to the final merged output file.
            - expected_order: List of keys in the order they should be merged.
            - on_chunk: Optional callback invoked with each chunk of data written to the output file.
        """
        self._expected = expected_order
        self._cursor = 0
        self._pending: dict[Any, Path] = {}
        self._lock = threading.Lock()
        self._fh = open(out_path, "wb")
        self._failed = False
        self._on_chunk = on_chunk

    def submit(self, key: Any, path: Path) -> None:
        with self._lock:
            if self._failed:
                return
            self._pending[key] = path
            while self._cursor < len(self._expected) and self._expected[self._cursor] in self._pending:
                ready_key = self._expected[self._cursor]
                ready_path = self._pending.pop(ready_key)
                try:
                    with open(ready_path, "rb") as src:
                        if self._on_chunk is None:
                            shutil.copyfileobj(src, self._fh, _LIVE_MERGE_BUFSIZE)
                        else:
                            while True:
                                chunk = src.read(_LIVE_MERGE_BUFSIZE)
                                if not chunk:
                                    break
                                self._fh.write(chunk)
                                self._on_chunk(chunk)
                except Exception as exc:
                    self._failed = True
                    logger.warning(f"[live_merge] failed appending segment {ready_key!r} ({ready_path.name}): {exc}")
                    return
                self._cursor += 1

    def attach_feeder(self, feed_fn: Callable[[bytes], None]) -> None:
        """Retroactively wire a streaming-mux feeder into an already-running live merge"""
        with self._lock:
            if self._on_chunk is not None or self._failed:
                return
            self._fh.flush()
            with open(self._fh.name, "rb") as src:
                while True:
                    chunk = src.read(_LIVE_MERGE_BUFSIZE)
                    if not chunk:
                        break
                    feed_fn(chunk)
            self._on_chunk = feed_fn

    @property
    def complete(self) -> bool:
        with self._lock:
            return not self._failed and self._cursor == len(self._expected)

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._failed

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class DecryptPipelineMixin:
    @staticmethod
    def _decrypt_track_label(stream) -> str:
        """Short human label for a track, used in decrypt-failure reporting."""
        return track_label(stream)

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small increments so a stop request lands without waiting out the full backoff."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_check():
                return
            time.sleep(min(0.5, deadline - time.monotonic()))

    def _try_start_streaming_mux(
        self, video_ext: str, video_stream: Any, video_duration_cap: float | None = None
    ) -> "StreamingMuxFeeder | None":
        """Attempt to start the streaming-mux fast path. Returns a StreamingMuxFeeder if successful, or None if not eligible or an error occurred."""
        try:
            return self._try_start_streaming_mux_inner(video_ext, video_stream, video_duration_cap)
        except Exception as exc:
            logger.warning(f"streaming_mux: unexpected error setting up the fast path ({exc!r}) -- falling back to the normal mux", exc_info=True)
            return None

    def _launch_streaming_mux_async(
        self,
        video_ext: str,
        video_stream: Any,
        video_duration_cap: float | None,
        merger: "_LiveMerger",
        feeder_box: list,
    ) -> threading.Thread:
        """Runs _try_start_streaming_mux() (eligibility checks + the wait for other tracks + ffmpeg launch)"""
        def _worker() -> None:
            feeder = self._try_start_streaming_mux(video_ext, video_stream, video_duration_cap)
            if feeder is not None:
                feeder_box[0] = feeder
                merger.attach_feeder(feeder.feed)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    def _try_start_streaming_mux_inner(
        self, video_ext: str, video_stream: Any, video_duration_cap: float | None = None
    ) -> "StreamingMuxFeeder | None":
        if not _streaming_mux_enabled():
            logger.info("streaming_mux: disabled for this run (--no-livemux) -- falling back to the normal mux")
            return None
        
        output_path = getattr(self, "_streaming_mux_output_path", None)
        if not output_path or not str(output_path).lower().endswith(".mkv"):
            logger.info(f"streaming_mux: not eligible (output_path={output_path!r} is not .mkv, or enable_streaming_mux() was never called) -- falling back to the normal mux")
            return None
        
        if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() != "ffmpeg":
            logger.info("streaming_mux: not eligible (PROCESS.engine != ffmpeg) -- falling back to the normal mux")
            return None
        
        if getattr(self, "other_tracks", None):
            logger.info("streaming_mux: not eligible (other_tracks/hybrid output present) -- falling back to the normal mux")
            return None  # hybrid output (dolby vision, other video renditions, ...) has its own path

        # External tracks (e.g. HLS text subtitles promoted by
        # _promote_hls_subtitles_to_external(), called earlier in start_download()) are
        # fetched by a separate thread (ext_thread / _run_externals()), not the per-stream
        # decrypt workers this fast path otherwise waits on.
        external_subs = [t for t in (getattr(self, "external_subtitles", None) or []) if t.get("_selected", True)]
        external_auds = [t for t in (getattr(self, "external_audios", None) or []) if t.get("_selected", True)]
        if (external_subs or external_auds) and context_tracker.no_concurrent:
            logger.info("streaming_mux: not eligible (external tracks present and --no-concurrent is set) -- falling back to the normal mux")
            return None

        try:
            ffmpeg_path = get_ffmpeg_path()
        except Exception:
            ffmpeg_path = None
        if not ffmpeg_path:
            logger.info("streaming_mux: not eligible (ffmpeg binary not found) -- falling back to the normal mux")
            return None

        other_streams = [
            s
            for s in getattr(self, "streams", []) or []
            if getattr(s, "selected", False) and not getattr(s, "is_external", False) and s.type in ("audio", "subtitle")
        ]

        ready_paths: dict[str, Path] = {}
        for other in other_streams:
            key = self._stream_task_key(other)
            timeout = _estimate_livemux_wait_seconds(other)
            if not self._track_done_event(key).wait(timeout=timeout):
                logger.info(f"streaming_mux: timed out waiting for {key!r} after {timeout:.0f}s -- falling back to the normal mux")
                return None

            resolved = self._get_track_result(key)
            if resolved is None:
                logger.info(f"streaming_mux: {key!r} failed to download -- falling back to the normal mux")
                return None
            ready_paths[key] = resolved

        for ext_track in external_subs + external_auds:
            key = ext_track["_task_key"]
            timeout = STREAMING_MUX_MIN_WAIT_SECONDS  # external tracks (e.g. HLS text subs) are light -- fixed floor is enough
            if not self._track_done_event(key).wait(timeout=timeout):
                logger.info(f"streaming_mux: timed out waiting for external track {key!r} after {timeout:.0f}s -- falling back to the normal mux")
                return None
            
            resolved = self._get_track_result(key)
            if resolved is None:
                logger.info(f"streaming_mux: external track {key!r} failed to download -- falling back to the normal mux")
                return None
            ready_paths[key] = resolved

        audio_streams = [s for s in other_streams if s.type == "audio"]
        subtitle_streams = [s for s in other_streams if s.type == "subtitle"]
        audio_paths = [ready_paths[self._stream_task_key(s)] for s in audio_streams] + [
            ready_paths[t["_task_key"]] for t in external_auds
        ]
        _subtitle_candidates = [(s, ready_paths[self._stream_task_key(s)]) for s in subtitle_streams] + [
            (t, ready_paths[t["_task_key"]]) for t in external_subs
        ]

        # ffmpeg builds in this session have no native TTML decoder, so a raw .ttml fed
        # into the fast-path pipe (unlike join_media(), which always pre-converts via the
        # same convert_subtitle() below)
        force_subtitle = config_manager.config.get("PROCESS", "force_subtitle")
        subtitle_paths: list[Path] = []
        subtitle_entries: list[Any] = []
        for _entry, _path in _subtitle_candidates:
            if getattr(_entry, "is_wvtt_mp4", False):
                _converted = extract_vtt_from_wvtt_mp4(str(_path))
                if not _converted:
                    logger.warning(f"streaming_mux: wvtt extraction failed for {_path} -- dropping this track from the fast-path mux")
                    continue
            else:
                # ISM chunks its TTML subtitle into one <tt> block per manifest
                # chunk with its own near-zero-based timestamps -- when the
                # manifest's per-chunk timeline was captured (see
                # ism.py::_apply_chunk_timeline), pass the exact absolute chunk
                # offsets through so the converter doesn't have to estimate them.
                _segs = getattr(_entry, "segments", None) or []
                _chunk_offsets: list[float] | None = None
                if _segs and all(getattr(s, "duration", 0.0) for s in _segs):
                    _chunk_offsets = []
                    _running = 0.0
                    for _seg in _segs:
                        _chunk_offsets.append(_running)
                        _running += _seg.duration
                _converted = convert_subtitle(str(_path), force_subtitle, chunk_offsets=_chunk_offsets)
                if not _converted:
                    logger.warning(f"streaming_mux: subtitle conversion failed for {_path} -- dropping this track from the fast-path mux")
                    continue
            subtitle_paths.append(Path(_converted))
            subtitle_entries.append(_entry)

        video_fmt = "mp4" if video_ext in ("mp4", "m4s", "m4a") else "mpegts"
        cmd = [ffmpeg_path, "-y"]
        if video_fmt == "mpegts":
            cmd += ["-fflags", "+genpts+igndts+discardcorrupt", "-avoid_negative_ts", "make_zero"]
            cmd += ["-analyzeduration", "100M", "-probesize", "100M"]

        cmd += ["-f", video_fmt, "-i", "-"]
        for p in audio_paths:
            if is_mpegts_file(str(p)):
                cmd += ["-f", "mpegts"]
            cmd += ["-i", str(p)]
        
        for p in subtitle_paths:
            cmd += ["-i", str(p)]

        cmd += ["-map", "0:v:0"]
        video_codecs = (getattr(video_stream, "codecs", "") or "").lower()
        if any(p in video_codecs for p in ("hev", "hvc", "dvh", "dvhe")):
            cmd += ["-tag:v", "hvc1"]

        for i in range(len(audio_paths)):
            cmd += ["-map", f"{i + 1}:a"]
        
        sub_input_base = 1 + len(audio_paths)
        for i in range(len(subtitle_paths)):
            cmd += ["-map", f"{sub_input_base + i}:s"]

        def _audio_title_and_iso_lang(entry: Any) -> tuple[str, str]:
            if isinstance(entry, dict):
                lang_source = entry.get("language") or entry.get("name") or "und"
                title = entry.get("name") or resolve_language_display_name(lang_source)
            else:
                lang_source = entry.resolved_language or entry.language or "und"
                title = getattr(entry, "name", "") or resolve_language_display_name(lang_source)
            return title, resolve_iso639_2(lang_source)

        def _subtitle_title_and_iso_lang(entry: Any) -> tuple[str, str]:
            """Subtitle title is a bit more complex: if the track is marked forced, SDH, or CC, we append that to the title. If the track has no name, we use the language display name as the base title (same fallback as audio)."""
            if isinstance(entry, dict):
                lang_source = entry.get("language") or entry.get("name") or "und"
                raw_name = entry.get("name") or ""
                forced, sdh, cc = bool(entry.get("forced")), bool(entry.get("sdh")), bool(entry.get("cc"))
            else:
                lang_source = entry.resolved_language or entry.language or "und"
                raw_name = getattr(entry, "name", "") or ""
                forced = bool(getattr(entry, "forced", False))
                sdh = bool(getattr(entry, "is_sdh", False))
                cc = bool(getattr(entry, "is_cc", False))

            base = re.sub(r"\s*[(\[][^)\]]*[)\]]\s*", " ", raw_name).strip() or resolve_language_display_name(lang_source)
            flags = []
            if forced:
                flags.append("[Forced]")
            if sdh:
                flags.append("[SDH]")
            elif cc:
                flags.append("[CC]")
            title = f"{base} {' '.join(flags)}".strip() if flags else base
            return title, resolve_iso639_2(lang_source)

        audio_infos = [_audio_title_and_iso_lang(s) for s in audio_streams] + [
            _audio_title_and_iso_lang(t) for t in external_auds
        ]
        subtitle_infos = [_subtitle_title_and_iso_lang(e) for e in subtitle_entries]

        for i, (title, lang) in enumerate(audio_infos):
            cmd += [f"-metadata:s:a:{i}", f"title={title}"]
            cmd += [f"-metadata:s:a:{i}", f"language={lang}"]
            cmd += [f"-metadata:s:a:{i}", f"handler_name={title}"]
            cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]

        for i, (title, lang) in enumerate(subtitle_infos):
            cmd += [f"-metadata:s:s:{i}", f"title={title}"]
            cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
            cmd += [f"-metadata:s:s:{i}", f"handler_name={title}"]

        cmd += ["-c", "copy"]
        if subtitle_paths:
            cmd += ["-c:s", "srt"]
        if video_duration_cap:
            cmd += ["-t", f"{video_duration_cap:.3f}"]
        cmd += [str(output_path)]

        feeder = StreamingMuxFeeder(cmd)
        try:
            feeder.start()
        except Exception as exc:
            logger.warning(f"streaming_mux: failed to start ffmpeg: {exc}")
            return None

        logger.info(f"streaming_mux: started early cross-track mux -> {output_path}")
        return feeder

    def _finish_streaming_mux(self, feeder: "StreamingMuxFeeder", live_merge_ok: bool) -> None:
        """Finalize the streaming-mux fast path, if it was started. If *live_merge_ok* is False, abort the fast path and fall back to the normal mux."""
        if not live_merge_ok:
            feeder.abort()
            feeder.finish()
            logger.warning("streaming_mux: the video track's own live merge did not complete cleanly -- discarding the fast-path output, falling back to the normal mux")
            return

        result = feeder.finish()
        if not result.ok:
            logger.warning(f"streaming_mux: ffmpeg fast-path mux failed ({result.error}) -- falling back to the normal mux")
            if result.stderr_tail:
                logger.warning(f"streaming_mux: ffmpeg stderr tail:\n{result.stderr_tail}")
            return

        output_path = getattr(self, "_streaming_mux_output_path", None)
        if not output_path or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            logger.warning("streaming_mux: fast-path output missing or empty -- falling back to the normal mux")
            return

        self.streaming_mux_result = output_path
        logger.info(f"streaming_mux: fast-path cross-track mux succeeded -> {output_path}")

    def _download_stream_generic(
        self,
        dl_segs: list[dict],
        stream,
        protocol: str,
        default_ext: str,
        bar_manager: DownloadBarManager,
        live_decryption: bool = False,
        seg_url_refresh_fn=None,
    ) -> None:
        task_key = self._stream_task_key(stream)
        if stream.type == "video":
            _plain = self._video_labels_by_task_key.get(task_key) or self._video_label
            _progress_label = f"[bold cyan]Vid[/bold cyan] {_plain}" if _plain else ""
        elif stream.type == "audio":
            _plain = self._audio_labels_by_task_key.get(task_key) or self._audio_labels.get(
                (stream.language or "und").lower(), ""
            )
            _progress_label = f"[bold cyan]Aud[/bold cyan] {_plain}" if _plain else ""
        elif stream.type == "subtitle":
            _plain = self._sub_labels_by_task_key.get(task_key, "")
            _progress_label = f"[bold cyan]Sub[/bold cyan] {_plain}" if _plain else ""
        else:
            _progress_label = ""
        total = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, protocol)
        all_headers = self._build_headers()
        protocol_lower = protocol.lower()

        # Checked once per track (the first media segment, not the dedicated init segment) to fail fast on a wrong key
        _first_media_seg_number = min(
            (s["number"] for s in dl_segs if s.get("seg_type") != "init"),
            default=None,
        )

        key_cache: dict[str, bytes] = {}
        segment_meta_by_path = {}
        for seg in dl_segs:
            seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
            if seg_ext == "m4s":
                seg_ext = "mp4"
            seg_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
            segment_meta_by_path[normalize_path_key(str(seg_path))] = seg

        # No dedicated init segment on the wire: every segment is
        # self-initializing (its own ftyp+moov), so there's no separate init to wait for
        _no_dedicated_init = protocol_lower in ("dash", "hls") and not any(s.get("seg_type") == "init" for s in dl_segs)

        # Full ordering key sequence for _LiveMerger. ISM's init is never a real
        # dl_seg (it's synthesized from the first fragment, see
        # _build_ism_init_from_fragment) so it isn't in dl_segs at all -- give
        # it key -1, sorting before every real segment number (>=0).
        _expected_live_order = sorted(s["number"] for s in dl_segs) + ([-1] if protocol_lower == "ism" else [])
        _expected_live_order.sort()

        _total_duration: float = sum(s.get("duration", 0.0) for s in dl_segs if s.get("seg_type") != "init")
        _media_segs_only: list[dict] = [s for s in dl_segs if s.get("seg_type") != "init"]
        _seg_dur_cumulative: list[float] = []
        _acc = 0.0
        for _s in _media_segs_only:
            _acc += _s.get("duration", 0.0)
            _seg_dur_cumulative.append(_acc)

        decrypt_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        decrypt_errors: list[str] = []
        seg_errors: list[str] = []

        # Set once a decrypt error is recognised as permanent for this whole
        # track (e.g. no key for its KID) rather than transient (one corrupt
        # segment) -- checked at the top of _decrypt_worker's loop so the
        # remaining queued segments are skipped instead of each producing an
        # identical flux failure + log line.
        decrypt_aborted: dict[str, str | None] = {"reason": None}
        decrypt_threads: list[threading.Thread] = []
        _live_merger_box: list[_LiveMerger | None] = [None]     # set once live decrypt is confirmed active, see needs_*_live below
        key_cache_lock = threading.Lock()
        dash_init_box: list[Path | None] = [None]
        dash_init_lock = threading.Lock()

        # ISM has no separate init segment on the wire (unlike DASH) -- every
        # fragment carries the same track_ID, kid, codec etc, so *any* fragment
        # (not necessarily #1, since worker threads download concurrently) can
        # be used to synthesize a real ftyp+moov init the moment it arrives
        ism_init_paths: list[tuple[Path, Path] | None] = [None]  # (protected, clean) -- see _build_ism_init_from_fragment
        ism_init_lock = threading.Lock()

        def _replace_segment_file(source_path: Path, target_path: Path, reason: str) -> None:
            last_exc: Exception | None = None
            for attempt in range(1, 9):
                try:
                    if target_path.exists():
                        try:
                            target_path.unlink()
                        except Exception:
                            pass

                    source_path.replace(target_path)
                    return
                except OSError as exc:
                    last_exc = exc
                    if attempt >= 8:
                        raise

                    if getattr(exc, "winerror", None) not in (5, 32) and not isinstance(exc, PermissionError):
                        raise

                    logger.debug(f"{reason} replace retry {attempt}/8 for {source_path.name} -> {target_path.name}: {exc}")
                    time.sleep(0.05 * attempt)

            if last_exc:
                raise last_exc

        _first_bytes_logged = False

        def _progress(
            done: int, total_: int, total_bytes: int, speed_bps: float, speed_label: str | None = None
        ) -> None:
            nonlocal _first_bytes_logged
            if not _first_bytes_logged and total_bytes > 0:
                _first_bytes_logged = True
                logger.info(
                    f"{protocol.upper()} first bytes received | id={stream.id!r} | type={stream.type} | {task_key}"
                )
            
            pct = int((done / total_) * 100) if total_ else 0
            estimated_total = _estimate_total_size(total_bytes, done, total_) if done > 0 else total_bytes
            size_display = (
                f"{_fmt_size(total_bytes)}/{_fmt_size(estimated_total)}"
                if done < total_
                else f"{_fmt_size(total_bytes)}/{_fmt_size(total_bytes)}"
            )
            duration_display = ""

            if _total_duration > 0:
                media_done = max(0, done - (1 if any(s.get("seg_type") == "init" for s in dl_segs) else 0))
                elapsed_dur = (
                    _seg_dur_cumulative[media_done - 1]
                    if media_done > 0 and media_done <= len(_seg_dur_cumulative)
                    else 0.0
                )
                duration_display = f"{_fmt_dur(elapsed_dur)}/{_fmt_dur(_total_duration)}"

            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "label": _progress_label or task_key,
                    "display_label": _progress_label or task_key,
                    "pct": pct,
                    "segments": f"{done}/{total_}",
                    "size": size_display,
                    "speed": speed_label if speed_label is not None else _fmt_speed(speed_bps),
                    "duration": duration_display,
                }
            )

        def _decrypt_hls_segment(fp: Path, seg: dict[str, Any]) -> None:
            enc = seg.get("enc") or {}
            method = str(enc.get("method") or "NONE").upper()
            if method != "AES-128":
                return

            key_data = enc.get("key_bytes")
            if key_data is None:
                key_url = enc.get("key_url")
                if not key_url:
                    raise RuntimeError(f"Missing AES-128 key URL for {fp.name}")

                key_data = key_cache.get(key_url)
                if key_data is None:
                    with key_cache_lock:
                        key_data = key_cache.get(key_url)
                        if key_data is None:
                            with create_client(
                                headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True
                            ) as c:
                                r = c.get(key_url)
                                r.raise_for_status()
                                key_data = r.content

                            if len(key_data) != 16:
                                logger.warning(f"HLS AES-128 key length is {len(key_data)} bytes for {key_url}")

                            key_cache[key_url] = key_data
                            logger.info(f"HLS AES-128 key fetched {enc.get('iv')}:{key_data.hex()}")

            logger.debug(f"AES-128 LIVE decrypt path={fp} with key={describe_key_for_log(key_data)}")
            tmp_path = fp.with_suffix(fp.suffix + ".dec")
            decrypt_aes128_file(fp, tmp_path, key_data, enc.get("iv"), int(seg.get("number", 0) or 0))
            _replace_segment_file(tmp_path, fp, "HLS AES-128")

            logger.debug(f"HLS AES-128 decrypted -> {fp.name}")
            if _live_merger_box[0] is not None:
                _live_merger_box[0].submit(seg.get("number"), fp)

        def _fetch_exact_range(url: str, seg_headers: dict, byte_range: tuple[int, int]) -> bytes:
            """GET *url* with a Range header for the inclusive *byte_range*
            (reusing this segment's own headers on top of the stream's base
            headers), and return exactly that many bytes -- slicing
            client-side if the server, or an intermediate redirect hop,
            ignores the Range header and sends the whole resource back
            instead of honoring it with a 206."""
            start, end = byte_range
            req_headers = dict(all_headers)
            req_headers.update(seg_headers)
            req_headers["Range"] = f"bytes={start}-{end}"
            with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                resp = c.get(url)
                resp.raise_for_status()
                body = resp.content
            expected_len = end - start + 1
            if resp.status_code != 206 and len(body) > expected_len:
                body = body[start : end + 1]
            return body

        def _repair_media_segment(fp: Path, seg: dict[str, Any]) -> None:
            """If *fp*'s downloaded bytes don't look like the bare fragment
            --fragments-info expects for a byte-range DASH/HLS media segment,
            try to recover the correct byte range before handing it to flux."""
            seg_headers = seg.get("headers") or {}
            byte_range = parse_range_header(seg_headers.get("Range"))
            if byte_range is None:
                return

            start, end = byte_range
            expected_len = end - start + 1
            try:
                data = fp.read_bytes()
            except OSError:
                return
            if len(data) == expected_len and looks_like_bare_fragment(data):
                return

            logger.debug(f"{protocol.upper()} media segment {fp.name} looks wrong (got {len(data)} bytes, expected {expected_len}) -- attempting repair")
            repaired: bytes | None = None
            try:
                redirect_url = extract_redirect_url(data)
                if redirect_url:
                    logger.debug(f"{protocol.upper()} media segment {fp.name}: retrying via extracted URL: {redirect_url}")
                    repaired = _fetch_exact_range(redirect_url, seg_headers, byte_range)

                if repaired is None or len(repaired) != expected_len or not looks_like_bare_fragment(repaired):
                    logger.debug(f"{protocol.upper()} media segment {fp.name}: retrying original URL with explicit Range")
                    repaired = _fetch_exact_range(seg["url"], seg_headers, byte_range)
            except Exception as exc:
                raise RuntimeError(f"{protocol.upper()} media segment {fp.name} repair failed: {exc}") from exc

            if len(repaired) != expected_len or not looks_like_bare_fragment(repaired):
                raise RuntimeError(f"{protocol.upper()} media segment {fp.name} still wrong after repair (got {len(repaired)} bytes, expected {expected_len})")

            fp.write_bytes(repaired)
            logger.info(f"{protocol.upper()} media segment repaired -> {fp.name}")

        def _refetch_media_segment(fp: Path, seg: dict[str, Any]) -> None:
            """Re-download *fp*'s bytes fresh from the network. Used when a
            segment passes every structural check (right shape, right size)
            yet still fails to decrypt -- e.g. a CDN response cut short
            mid-transfer without the HTTP layer itself reporting an error,
            corrupting only the trailing mdat/senc data."""
            seg_headers = seg.get("headers") or {}
            byte_range = parse_range_header(seg_headers.get("Range"))
            if byte_range is not None:
                data = _fetch_exact_range(seg["url"], seg_headers, byte_range)
            else:
                req_headers = dict(all_headers)
                req_headers.update(seg_headers)
                with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                    resp = c.get(seg["url"])
                    resp.raise_for_status()
                    data = resp.content
            fp.write_bytes(data)

        def _decrypt_dash_segment(
            fp: Path, seg: dict[str, Any], dash_decryptor: Decryptor, init_path: Path | None
        ) -> None:
            """Shared by DASH and HLS (EXT-X-MAP CENC/SAMPLE-AES) live decrypt --
            both share a bare moof+mdat fragment + separate real init segment
            model, so one function covers both. init_path=None (HLS with no
            EXT-X-MAP, every segment self-initializing) makes
            decrypt_segment_live fall through to flux's normal (non
            --fragments-info) decrypt, which reads the segment's own moov."""
            if seg.get("seg_type") == "init":
                logger.info(f"{protocol.upper()} init segment ready -> {fp.name}")
                return

            _repair_media_segment(fp, seg)

            dec_tmp = fp.with_suffix(fp.suffix + ".dec")
            init_path_str = str(init_path) if init_path and init_path.exists() else None
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"CENC LIVE decrypt path={fp} init={init_path_str or 'None'} with key={describe_key_for_log(self.key)}")

            ok, message, _data = dash_decryptor.decrypt_segment_live(
                encrypted_path=str(fp),
                decrypted_path=str(dec_tmp),
                raw_keys=self.key,
                init_path=init_path_str,
                key_sanity=(seg.get("number") == _first_media_seg_number),
            )

            if not ok:
                # The bytes on disk decrypt-fail even though the download itself
                # reported success (e.g. a CDN response cut short mid-transfer
                # without the HTTP layer noticing) -- re-fetch this one segment
                # fresh and retry exactly once before giving up on it.
                logger.warning(f"{protocol.upper()} live decrypt failed for {fp.name}, re-downloading and retrying once: {message}")
                try:
                    _refetch_media_segment(fp, seg)
                    ok, message, _data = dash_decryptor.decrypt_segment_live(
                        encrypted_path=str(fp),
                        decrypted_path=str(dec_tmp),
                        raw_keys=self.key,
                        init_path=init_path_str,
                        key_sanity=False,
                    )
                except Exception as exc:
                    message = f"{message} (retry fetch failed: {exc})"

            if not ok or not dec_tmp.exists():
                # Still bad after the retry -- drop this one segment (a small
                # A/V gap) instead of aborting the whole track: the CDN glitch
                # is almost certainly isolated to this segment, and failing the
                # entire video/audio track over one bad fragment is far worse
                # than a brief skip.
                logger.warning(f"{protocol.upper()} live decrypt still failing for {fp.name} after retry -- dropping this segment: {message if not ok else 'no output produced'}")
                fp.unlink(missing_ok=True)
                dec_tmp.unlink(missing_ok=True)
                return

            _replace_segment_file(dec_tmp, fp, f"{protocol.upper()} live")
            logger.debug(f"{protocol.upper()} live decrypted -> {fp.name}")
            if _live_merger_box[0] is not None:
                _live_merger_box[0].submit(seg.get("number"), fp)

        dash_decryptor = Decryptor() if protocol_lower in ("dash", "hls") and live_decryption and self.key else None
        dash_pending: list[tuple] = []  # (fp, seg) media segments seen before the init segment
        ism_decryptor = Decryptor() if protocol_lower == "ism" and live_decryption and self.key else None

        def _build_ism_init_from_fragment(first_fp: Path) -> tuple[Path, Path]:
            """Synthesize both ftyp+moov variants (correct track_ID read from *first_fp*) and return (protected_init_path, clean_init_path)."""
            track_id = self._read_fragment_track_id(first_fp.read_bytes())
            kid_hex = getattr(stream.drm, "kid", None) if stream.drm else None
            kid_hex = kid_hex or "00000000000000000000000000000000"
            protected_data = self._build_ism_init(stream, kid_hex, track_id=track_id)
            clean_data = self._build_ism_init(stream, kid_hex, track_id=track_id, encrypted=False)

            protected_path = stream_dir / "_ism_live_init_protected.mp4"
            protected_path.write_bytes(protected_data)
            clean_path = stream_dir / f"seg_-000001{first_fp.suffix}"
            clean_path.write_bytes(clean_data)

            logger.debug(f"ISM live init synthesized from {first_fp.name} (track_id={track_id or 1}) -> {protected_path.name} + {clean_path.name}")
            return protected_path, clean_path

        def _ism_clear_segment(fp: Path, seg: dict[str, Any]) -> None:
            """Clear (DRM-less) ISM: nothing to decrypt, but still needs the
            same tfhd.sample_description_index fix every fragment needs to
            match the single-entry init we synthesize (independent of
            encryption -- see _normalize_ism_fragment_sdi)."""
            fp.write_bytes(self._normalize_ism_fragment_sdi(fp.read_bytes()))
            if _live_merger_box[0] is not None:
                _live_merger_box[0].submit(seg.get("number"), fp)

        def _decrypt_ism_segment(fp: Path, seg: dict[str, Any], ism_decryptor: Decryptor, init_path: Path) -> None:
            fp.write_bytes(self._normalize_ism_fragment_sdi(fp.read_bytes()))

            dec_tmp = fp.with_suffix(fp.suffix + ".dec")
            ok, message, _data = ism_decryptor.decrypt_segment_live(
                encrypted_path=str(fp),
                decrypted_path=str(dec_tmp),
                raw_keys=self.key,
                init_path=str(init_path),
                key_sanity=(seg.get("number") == _first_media_seg_number),
            )

            if not ok:
                raise RuntimeError(f"ISM live decrypt failed for {fp.name}: {message}")

            if not dec_tmp.exists():
                raise RuntimeError(f"ISM live decrypt produced no output for {fp.name}")

            _replace_segment_file(dec_tmp, fp, "ISM live")
            logger.debug(f"ISM live decrypted -> {fp.name}")
            if _live_merger_box[0] is not None:
                _live_merger_box[0].submit(seg.get("number"), fp)

        def _decrypt_worker() -> None:
            while True:
                item = decrypt_queue.get()
                if item is None:
                    break
                try:
                    if decrypt_aborted["reason"] is not None:
                        continue
                    if item.get("skipped"):
                        continue
                    path_value = item.get("path")
                    if not path_value:
                        continue
                    fp = Path(path_value)
                    if not fp.exists() or fp.stat().st_size <= 0:
                        continue
                    seg = segment_meta_by_path.get(normalize_path_key(str(fp)))
                    if not seg:
                        logger.debug(f"Segment completion without metadata match: {fp}")
                        continue

                    if protocol_lower == "hls":
                        _hls_method = str((seg.get("enc") or {}).get("method") or "NONE").upper()
                        if _hls_method == "AES-128":
                            _decrypt_hls_segment(fp, seg)
                            continue
                        if _hls_method == "NONE" and needs_hls_clear_merge:
                            # Fully unencrypted HLS TS: no decrypt, no init
                            # concept at all (every TS segment is a complete,
                            # independently concatenable unit via its own
                            # repeated PAT/PMT) -- just order+append it live.
                            if _live_merger_box[0] is not None:
                                _live_merger_box[0].submit(seg.get("number"), fp)
                            continue

                        if not needs_hls_live:
                            # Unencrypted, or SAMPLE-AES without live support available
                            # (e.g. no keys resolved yet) -- leave for the batch
                            # whole-file decrypt after merge, same as before this
                            # branch existed.
                            continue

                        if seg.get("seg_type") != "init" and not _hls_method.startswith("SAMPLE-AES"):
                            # The dedicated init segment's own "enc" is always
                            # hardcoded to method=NONE (it isn't itself sample
                            # data) -- it still has to fall through below to get
                            # cached, or no media segment would ever find an
                            # init to decrypt against. Only filter out a genuine
                            # non-SAMPLE-AES *media* segment here.
                            continue

                        # SAMPLE-AES with live decrypt available falls through to the
                        # shared DASH/HLS CENC block below (EXT-X-MAP and
                        # self-initializing segments both handled there).

                    if protocol_lower == "dash" and needs_dash_clear_merge:
                        # Unencrypted DASH (e.g. a subtitle track that has no
                        # ContentProtection of its own even though self.key is
                        # set for the video/audio tracks): nothing to decrypt,
                        # but still worth ordering+appending each segment into
                        # the final output live as it downloads instead of a
                        # separate merge pass afterward -- same _LiveMerger,
                        # just no decrypt step. Must be checked before the
                        # live-decrypt branch below: flux's --fragments-info
                        # only understands AVC/HEVC/AV1/VP9/AAC/Opus init
                        # segments, so routing an unencrypted text/TTML track
                        # there fails immediately with "unexpected box".
                        if _live_merger_box[0] is not None:
                            _live_merger_box[0].submit(seg.get("number"), fp)

                    elif (protocol_lower == "dash" and needs_dash_live) or (protocol_lower == "hls" and needs_hls_live):
                        if _no_dedicated_init:
                            # No EXT-X-MAP (HLS) / no init seg_type at all (DASH
                            # SegmentList etc): every segment carries its own
                            # ftyp+moov, so there's no separate init to cache/wait for --
                            # init_path=None makes decrypt_segment_live fall through
                            # to flux's normal (non --fragments-info) decrypt, which
                            # reads the segment's own moov and -- being a full normal
                            # decrypt, not a --fragments-info one -- already produces
                            # clean (non-CENC-signaling) output on its own, same as
                            # the existing whole-file batch decrypt path does.
                            _decrypt_dash_segment(fp, seg, dash_decryptor, None)
                        
                        elif seg.get("seg_type") == "init":
                            flush: list[tuple] = []
                            cached_now = False
                            with dash_init_lock:
                                if dash_init_box[0] is None:
                                    dash_init_box[0] = fp
                                    cached_now = True
                                    logger.debug(f"{protocol.upper()} init segment cached -> {fp.name}")
                                    flush = dash_pending[:]
                                    dash_pending.clear()

                            if cached_now:
                                # Unlike ISM's self-built init, this is the real init
                                # segment downloaded from the CDN -- it still carries
                                # genuine sinf/tenc CENC signaling that flux's
                                # --fragments-info leaves untouched (it only ever
                                # decrypts fragment sample data). Keep a protected copy
                                # for flux to keep reading, and rewrite fp itself (the
                                # one that ends up in the final merge via `paths`) to a
                                # clean, non-CENC-signaling copy -- otherwise players/probes
                                # would flag the merged output as "still encrypted" despite
                                # every fragment already being plaintext.

                                try:
                                    protected_bytes = fp.read_bytes()

                                    # Some CDNs answer the init-segment request with
                                    # 200 OK + a small JS/JSON body embedding the real
                                    # signed URL instead of an HTTP redirect.
                                    redirect_url = repair_init_segment(protected_bytes)
                                    if redirect_url:
                                        logger.debug(f"{protocol.upper()} init segment wasn't a real MP4, retrying via extracted URL: {redirect_url}")
                                        seg_headers = seg.get("headers") or {}
                                        byte_range = parse_range_header(seg_headers.get("Range"))
                                        
                                        if byte_range is not None:
                                            # Reuse the same byte range as the original
                                            # request -- and slice client-side if the
                                            # extracted URL's host ignores the Range
                                            # header, instead of silently accepting a
                                            # whole (possibly multi-GB) file as "the
                                            # init segment" just because it happens to
                                            # start with a valid ftyp+moov too.
                                            protected_bytes = _fetch_exact_range(redirect_url, seg_headers, byte_range)
                                        else:
                                            req_headers = dict(all_headers)
                                            req_headers.update(seg_headers)
                                            with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                                                resp = c.get(redirect_url)
                                                resp.raise_for_status()
                                                protected_bytes = resp.content

                                        if not is_valid_frag_init(protected_bytes):
                                            raise RuntimeError(f"{protocol.upper()} init segment still not a valid ftyp+moov after following extracted redirect URL")
                                        fp.write_bytes(protected_bytes)
                                        logger.info(f"{protocol.upper()} init segment repaired via extracted redirect URL -> {fp.name}")

                                    protected_copy = fp.with_name(f"_frag_init_protected{fp.suffix}")
                                    protected_copy.write_bytes(protected_bytes)
                                    with dash_init_lock:
                                        dash_init_box[0] = protected_copy
                                    fp.write_bytes(strip_cenc_signaling(protected_bytes))
                                    logger.debug(f"{protocol.upper()} init CENC signaling stripped for merge -> {fp.name} (flux keeps reading {protected_copy.name})")
                                except Exception as exc:
                                    logger.warning(f"{protocol.upper()} init CENC-signaling strip failed, keeping original (players may flag residual boxes): {exc}")

                                if _live_merger_box[0] is not None:
                                    _live_merger_box[0].submit(seg.get("number"), fp)
                            for pending_fp, pending_seg in flush:
                                _decrypt_dash_segment(pending_fp, pending_seg, dash_decryptor, dash_init_box[0])
                        else:
                            with dash_init_lock:
                                init_path = dash_init_box[0]
                                if init_path is None:
                                    dash_pending.append((fp, seg))
                            if init_path is not None:
                                _decrypt_dash_segment(fp, seg, dash_decryptor, init_path)

                    elif protocol_lower == "ism" and needs_ism_clear_merge and not self.key:
                        # Clear ISM: still need the synthesized init (ISM
                        # fragments are never self-initializing, DRM or not),
                        # but no decrypt step -- just the sdi fix + live merge.
                        built_now = False
                        with ism_init_lock:
                            if ism_init_paths[0] is None:
                                ism_init_paths[0] = _build_ism_init_from_fragment(fp)
                                built_now = True
                            _protected_init_path, clean_init_path = ism_init_paths[0]

                        if built_now and _live_merger_box[0] is not None:
                            _live_merger_box[0].submit(-1, clean_init_path)

                        _ism_clear_segment(fp, seg)

                    elif protocol_lower == "ism" and live_decryption and self.key:
                        # No dedicated init segment exists on the wire -- the first
                        # fragment any worker thread happens to grab doubles as
                        # both the source of the synthesized init AND a normal
                        # fragment to decrypt (every fragment carries the same
                        # track_ID/kid/codec, so there's nothing to wait for).
                        built_now = False
                        with ism_init_lock:
                            if ism_init_paths[0] is None:
                                ism_init_paths[0] = _build_ism_init_from_fragment(fp)
                                built_now = True
                            protected_init_path, clean_init_path = ism_init_paths[0]

                        if built_now and _live_merger_box[0] is not None:
                            _live_merger_box[0].submit(-1, clean_init_path)

                        _decrypt_ism_segment(fp, seg, ism_decryptor, protected_init_path)

                except Exception as exc:
                    decrypt_errors.append(str(exc))
                    exc_str = str(exc)
                    is_permanent = "no content key for the track's default_kid" in exc_str.lower()
                    if is_permanent and decrypt_aborted["reason"] is None:
                        decrypt_aborted["reason"] = exc_str
                        logger.error(f"Segment decrypt error ({protocol_lower}/{task_key}): {exc}")
                    elif not is_permanent:
                        logger.error(f"Segment decrypt error ({protocol_lower}/{task_key}): {exc}")
                    decrypt_queue.task_done()

        needs_hls_decrypt = protocol_lower == "hls" and any(
            str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128" for seg in dl_segs
        )
        _stream_is_encrypted = stream.drm is not None and stream.drm.is_encrypted()
        needs_dash_live = protocol_lower == "dash" and live_decryption and bool(self.key) and _stream_is_encrypted
        needs_ism_live = protocol_lower == "ism" and live_decryption and bool(self.key) and _stream_is_encrypted

        # HLS's DRM state lives per-segment (#EXT-X-KEY METHOD), not on
        # stream.drm the way DASH/ISM's manifest-level DRM does -- check dl_segs
        # directly, same as needs_hls_decrypt (AES-128) already does.
        needs_hls_live = (
            protocol_lower == "hls"
            and live_decryption
            and bool(self.key)
            and any(str((seg.get("enc") or {}).get("method") or "NONE").upper().startswith("SAMPLE-AES") for seg in dl_segs)
        )

        # Unencrypted DASH/ISM: nothing to decrypt, but still worth routing
        # through the worker pool purely so _LiveMerger can order+append each
        # segment live instead of a separate merge pass at the end.
        needs_dash_clear_merge = (
            protocol_lower == "dash"
            and live_decryption
            and not _stream_is_encrypted
        )
        needs_ism_clear_merge = (
            protocol_lower == "ism"
            and live_decryption
            and not _stream_is_encrypted
            and not self.key
        )
        # Fully unencrypted HLS TS: no #EXT-X-KEY at all on any segment.
        needs_hls_clear_merge = (
            protocol_lower == "hls"
            and live_decryption
            and all(str((seg.get("enc") or {}).get("method") or "NONE").upper() == "NONE" for seg in dl_segs)
        )

        streaming_feeder = None
        mux_setup_thread: threading.Thread | None = None
        _mux_join_timeout = STREAMING_MUX_MIN_WAIT_SECONDS + 5
        feeder_box: list = [None]
        _live_out_path: Path | None = None
        if (
            needs_hls_decrypt
            or needs_dash_live
            or needs_ism_live
            or needs_hls_live
            or needs_dash_clear_merge
            or needs_ism_clear_merge
            or needs_hls_clear_merge
        ):
            # Live decrypt (dash/ism/hls) all funnel through one shared
            # Decryptor() -> one _FluxDaemon, whose .decrypt() holds a lock
            # across the write+readline round-trip -- so only one job is
            # ever actually in flight regardless of pool size. Extra threads
            # here don't add decrypt throughput, just queue contention;
            # DECRYPT_WORKER_COUNT only matters for the non-live batch path.
            worker_count = 1
            live_kind = (
                "live DASH" if needs_dash_live
                else "live ISM" if needs_ism_live
                else "live HLS SAMPLE-AES" if needs_hls_live
                else "live-merge-only DASH (clear)" if needs_dash_clear_merge
                else "live-merge-only ISM (clear)" if needs_ism_clear_merge
                else "live-merge-only HLS (clear)" if needs_hls_clear_merge
                else "AES-128"
            )
            logger.debug(f"{protocol.upper()} decrypt worker pool started ({worker_count}x, {live_kind})")

            # Stream each segment straight into the final output as soon as
            # it's ready (decrypted, or just downloaded for clear content)
            # and it's its turn, instead of a separate binary-merge pass
            # over the whole track once every download is done -- see
            # _LiveMerger. Falls back to the normal binary_merge_segments()
            # path below if anything's missing by the time downloads
            # finish (failed segment, unexpected ordering key, etc).
            _live_sample_url = next(
                (s["url"] for s in dl_segs if s.get("seg_type") != "init"), dl_segs[0]["url"] if dl_segs else ""
            )
            _live_ext = merged_segment_ext(_live_sample_url, default=default_ext)
            _live_out_path = self.output_dir / self._out_filename(stream, _live_ext)
            _live_merger_box[0] = _LiveMerger(_live_out_path, _expected_live_order, on_chunk=None)

            if stream.type == "video":
                _video_duration_cap = None
                if self.max_segments or self.max_time:
                    _dl_video_dur = sum(s.get("duration", 0.0) for s in dl_segs if s.get("seg_type") != "init")
                    if _dl_video_dur > 0:
                        _video_duration_cap = _dl_video_dur
                
                # Runs the wait-for-other-tracks + ffmpeg launch in the background so it
                # never blocks this video stream's own segment download below (started
                # right after this call, unconditionally) -- see _launch_streaming_mux_async().
                mux_setup_thread = self._launch_streaming_mux_async(
                    _live_ext, stream, _video_duration_cap, _live_merger_box[0], feeder_box
                )

                # The background thread waits on each non-video track sequentially (see
                # _try_start_streaming_mux_inner), each with its own dynamic timeout --
                # so the worst case for this join is the SUM of those timeouts, not one
                # fixed value.
                _other_for_wait = [
                    s
                    for s in getattr(self, "streams", []) or []
                    if getattr(s, "selected", False) and not getattr(s, "is_external", False) and s.type in ("audio", "subtitle")
                ]
                _mux_join_timeout = sum(_estimate_livemux_wait_seconds(s) for s in _other_for_wait) + STREAMING_MUX_MIN_WAIT_SECONDS

            for _ in range(worker_count):
                t = threading.Thread(target=_decrypt_worker, daemon=True)
                t.start()
                decrypt_threads.append(t)

        def _handle_download_event(event: dict[str, Any]) -> None:
            event_name = (event.get("event") or "").lower()
            if event_name == "error":
                msg = event.get("message") or event.get("error")
                if msg:
                    seg_errors.append(str(msg))
                return

            if event_name in {"start", "summary", "retry", "cancelled", "progress"}:
                return

            path_value = event.get("path")
            if not path_value:
                return

            if event.get("skipped"):
                return

            if decrypt_threads:
                decrypt_queue.put(dict(event))

        # Segments whose bytes travel inside the manifest itself (e.g. a base64 init segment) have no URL to request
        inline_paths: list[Path] = []
        net_segs = dl_segs
        if any(seg.get("inline_data") for seg in dl_segs):
            net_segs = []
            for seg in dl_segs:
                data = seg.get("inline_data")
                if not data:
                    net_segs.append(seg)
                    continue

                seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                if seg_ext == "m4s":
                    seg_ext = "mp4"

                inline_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
                inline_path.write_bytes(data)
                inline_paths.append(inline_path)
                logger.debug(f"Inline segment written from manifest -> {inline_path.name} ({len(data)} B)")

            logger.info(f"{protocol.upper()}: {len(inline_paths)} inline segment(s) from manifest, {len(net_segs)} to download")

        paths = list(inline_paths)
        if net_segs:
            logger.info(
                f"{protocol.upper()} download started | id={stream.id!r} | type={stream.type} | {task_key} | segs={len(net_segs)}"
            )
            paths += self._run_dl(
                net_segs,
                stream_dir,
                all_headers,
                _progress,
                stream=stream,
                event_cb=_handle_download_event,
                default_ext=default_ext,
            )

        # curl_cffi fallback: once the primary backend (native Velora binary, or curl_cffi itself if that's already the primary) has exhausted its own max_retry attempts for a segment
        if net_segs and not self._stop_check():
            still_failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            if still_failed:
                seg_by_number_fb = {s["number"]: s for s in dl_segs}
                fallback_tasks = []
                for n, _ in still_failed:
                    seg = seg_by_number_fb.get(n)
                    if seg is None:
                        continue

                    seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                    if seg_ext == "m4s":
                        seg_ext = "mp4"

                    fallback_tasks.append(
                        {
                            "url": seg["url"],
                            "path": str(stream_dir / f"seg_{n:05d}.{seg_ext}"),
                            "headers": seg.get("headers") or {},
                            "task_key": task_key,
                            "label": _progress_label,
                            "display_label": _progress_label,
                        }
                    )

                fallback_plan = {
                    "headers": all_headers,
                    "retry_count": RETRY_COUNT,
                    "timeout_seconds": float(REQUEST_TIMEOUT),
                }

                logger.warning(f"{len(fallback_tasks)} segment(s) exhausted the primary backend's retries -- falling back to curl_cffi for up to {RETRY_COUNT} more attempt(s) each")
                recovered = 0
                with ThreadPoolExecutor(max_workers=min(8, max(1, len(fallback_tasks)))) as executor:
                    futures = {executor.submit(_fetch_one, task, fallback_plan): task for task in fallback_tasks}
                    for future in as_completed(futures):
                        result = future.result()
                        if result.get("event") == "completed" and result.get("path"):
                            paths.append(Path(result["path"]))
                            recovered += 1
                            _handle_download_event(result)

                if recovered:
                    logger.info(f"curl_cffi fallback recovered {recovered}/{len(fallback_tasks)} segment(s)")

        # Token-refresh retry: when segments fail (e.g. the CDN manifest token expired mid-download -> HTTP 403, or a transient CDN-side 503 that clears up after a short wait).
        if seg_url_refresh_fn and not self._stop_check():
            seg_by_number = {s["number"]: s for s in dl_segs}
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            rounds = 0
            stall_rounds = 0

            while failed and rounds < MAX_TOKEN_REFRESH_ROUNDS and not self._stop_check():
                rounds += 1

                if TOKEN_REFRESH_BACKOFF_SECONDS > 0:
                    backoff = min(TOKEN_REFRESH_BACKOFF_SECONDS * rounds, 20.0)
                    logger.info(f"Token refresh round {rounds}: waiting {backoff:.1f}s before retrying (transient CDN errors often clear up on their own)")
                    self._interruptible_sleep(backoff)
                    if self._stop_check():
                        break

                failed_numbers = [n for n, _ in failed]
                fresh_map = seg_url_refresh_fn(failed_numbers)
                retry_segs = [
                    {**seg_by_number[n], "url": fresh_map[n]}
                    for n in failed_numbers
                    if n in fresh_map and n in seg_by_number
                ]
                if not retry_segs:
                    break

                logger.warning(f"Token refresh round {rounds}: retrying {len(retry_segs)} segment(s) with a fresh token")
                retry_paths = self._run_dl(
                    retry_segs,
                    stream_dir,
                    all_headers,
                    _progress,
                    stream=stream,
                    event_cb=_handle_download_event,
                    default_ext=default_ext,
                )
                paths.extend(retry_paths)
                new_failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)

                if len(new_failed) >= len(failed):  # no progress this round -> token still dead / host moved
                    stall_rounds += 1
                    failed = new_failed
                    if stall_rounds >= TOKEN_REFRESH_STALL_ROUNDS:
                        logger.warning(f"Token refresh: no progress after {stall_rounds} consecutive round(s), giving up on {len(failed)} segment(s)")
                        break
                    continue

                stall_rounds = 0
                failed = new_failed

        if decrypt_threads:
            for _ in decrypt_threads:
                decrypt_queue.put(None)
            for t in decrypt_threads:
                t.join()

        if mux_setup_thread is not None:
            # Bounded by _mux_join_timeout (sum of the thread's own per-track
            # dynamic waits, computed above) -- must join before closing the
            # merger below so a late attach_feeder() can never race a closed
            # file handle.
            mux_setup_thread.join(timeout=_mux_join_timeout)
        streaming_feeder = feeder_box[0]

        if dash_decryptor is not None:
            dash_decryptor.close_flux_daemon()
        if ism_decryptor is not None:
            ism_decryptor.close_flux_daemon()

        _live_merge_ok = False
        if _live_merger_box[0] is not None:
            _live_merge_ok = _live_merger_box[0].complete
            _live_merger_box[0].close()
            if _live_merge_ok:
                logger.debug(f"{protocol.upper()} live merge complete -> binary-merge pass skipped for this track")
            else:
                logger.debug(f"{protocol.upper()} live merge incomplete (missing/failed segment) -> falling back to the normal merge pass")

        if stream.type == "video" and streaming_feeder is not None:
            self._finish_streaming_mux(streaming_feeder, live_merge_ok=_live_merge_ok)

        if paths is not None:
            _stream_label_rich = (
                (self._video_labels_by_task_key.get(task_key) or self._video_label)
                if stream.type == "video"
                else self._audio_labels_by_task_key.get(task_key)
                or self._audio_labels.get((stream.language or "und").lower(), stream.language or "und")
                if stream.type == "audio"
                else stream.language or "und"
            )

            _plain_label = Text.from_markup(_stream_label_rich).plain.strip() or task_key
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            if failed:
                failed_numbers = {n for n, _ in failed}
                aes_failed = sum(
                    1
                    for seg in dl_segs
                    if seg["number"] in failed_numbers
                    and str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128"
                )
                aes_note = (
                    f" ({aes_failed} had an AES-128 key pending — never fetched, segment missing before decrypt)"
                    if aes_failed
                    else ""
                )
                if seg_errors:
                    top = "; ".join(
                        f"{m} (x{n})" for m, n in Counter(e.strip() for e in seg_errors if e.strip()).most_common(3)
                    )
                    logger.warning(f"{_plain_label}: {len(failed)}/{total} segment(s) failed to download — most common error(s): {top}{aes_note}")
                else:
                    logger.warning(f"{_plain_label}: {len(failed)}/{total} segment(s) failed to download{aes_note}")

                if _media_segs_only and min(s["number"] for s in _media_segs_only) in failed_numbers:
                    logger.warning(
                        f"{_plain_label}: the FIRST media segment (right after init) is missing — the merged file is "
                        "likely unparsable (fMP4 trun/tfhd continuity breaks), not just a small A/V gap. "
                        "Expect the duration probe to fail and this track to be dropped from the mux."
                    )

                with self._failed_segments_lock:
                    self._failed_segments.append((_plain_label, failed))

        if decrypt_aborted["reason"] is not None:
            # A genuinely permanent condition (e.g. no content key for this
            # track's KID at all) -- no segment could ever decrypt, so there's
            # nothing to gain by continuing.
            raise RuntimeError(decrypt_aborted["reason"])
        elif decrypt_errors:
            # Per-segment decrypt failures (already retried once with a fresh
            # download inside _decrypt_dash_segment/_decrypt_hls_segment/etc.,
            # and the affected segments dropped) -- a warning and a small A/V
            # gap, not a reason to fail this entire track.
            logger.warning(
                f"{len(decrypt_errors)} segment decrypt error(s) on this track (first: {decrypt_errors[0]}) -- "
                "continuing with the segments that did decrypt"
            )

        if needs_ism_live and ism_init_paths[0] is not None:
            _protected_init_path, _clean_init_path = ism_init_paths[0]
            if _clean_init_path.exists():
                paths.append(_clean_init_path)

        if self._stop_check() or not paths:
            return

        # Derive the merged-file extension from a *media* segment
        sample_url = next(
            (s["url"] for s in dl_segs if s.get("seg_type") != "init"),
            dl_segs[0]["url"] if dl_segs else "",
        )
        ext = merged_segment_ext(sample_url, default=default_ext)
        
        # Reuse the live-merge target's filename when one was already assigned above
        out_path = _live_out_path if _live_out_path is not None else self.output_dir / self._out_filename(stream, ext)

        # ----- ISM POST‑PROCESSING (batch path) -----
        # Skipped when needs_ism_live: segments were already normalized +
        # decrypted individually as they downloaded (see _decrypt_ism_segment
        # above), so ISM can fall through to the same generic binary-merge +
        # skip-redundant-decrypt + verify path DASH/HLS already use for their
        # own live decrypt case below (guarded by `not live_decryption`).
        if protocol_lower == "ism" and self.key and _stream_is_encrypted and not needs_ism_live:
            success = self._ism_postproc(paths, out_path, stream, bar_manager, task_key, total)
            if not success:
                logger.error("ISM post‑processing failed")
            return

        # Standard merge for HLS/DASH (and ISM when live-decrypted per-segment)
        is_plain_subtitle = (
            stream is not None
            and getattr(stream, "type", "") == "subtitle"
            and not getattr(stream, "is_wvtt_mp4", False)
        )

        merge_total_size = sum(p.stat().st_size for p in paths if p.exists())
        _merge_t0 = time.monotonic()
        if _live_merge_ok:
            # Live merge already wrote out_path in order as segments arrived
            # -- there's no separate merge pass left to run below (see the
            # `elif _live_merge_ok` branch).
            logger.debug(f"Live merge already complete -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
        else:
            logger.info(f"Merge starting -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "pct": 100,
                    "segments": f"{total}/{total}",
                    "size": f"{_fmt_size(merge_total_size)}/{_fmt_size(merge_total_size)}",
                    "speed": "Merge",
                }
            )

        def _sniff_vtt_content(raw: bytes) -> bool:
            """Prova a leggere i primi byte come testo; se sono gzip, decomprime prima."""
            try:
                if raw[:2] == b"\x1f\x8b":  # magic number gzip
                    raw = gzip.decompress(raw)[:64]
                else:
                    raw = raw[:64]
                head = raw.decode("utf-8-sig", errors="replace").lstrip("\ufeff\ufffd").lstrip()
                return head.startswith("WEBVTT")
            except Exception:
                return False

        _is_webvtt_sub = False
        _detect_reason = "no paths"

        if is_plain_subtitle and paths:
            _ext_says_vtt = out_path.suffix.lower() == ".vtt"
            _content_says_vtt = False

            for p in paths:
                try:
                    raw = p.read_bytes()[:512]
                    if _sniff_vtt_content(raw):
                        _content_says_vtt = True
                        break
                except Exception as exc:
                    logger.warning(f"[merge_detect] could not sniff {getattr(p, 'name', p)}: {exc}")
                    continue

            _is_webvtt_sub = _ext_says_vtt or _content_says_vtt
            _detect_reason = f"ext={_ext_says_vtt}, content={_content_says_vtt}"

        logger.info(f"[merge_detect] {out_path.name}: is_webvtt_sub={_is_webvtt_sub} ({_detect_reason}), {len(paths)} segment(s)")

        stream_is_encrypted = stream.drm.method is not None
        already_decrypted_per_segment = False

        # Some HLS BYTERANGE packagings make every media segment a
        # self-initializing MP4 document (its own ftyp+moov, not a bare
        # moof+mdat fragment sharing the EXT-X-MAP init).
        if (
            not is_plain_subtitle
            and not live_decryption
            and self.key
            and stream_is_encrypted
            and not _skip_post_decrypt()
        ):
            existing = [p for p in paths if p.exists() and p.stat().st_size > 0]
            ftyp_count = sum(1 for p in existing if _reads_as_self_initializing_mp4(p))
            if len(existing) > 1 and ftyp_count > 1:
                logger.info(f"{out_path.name}: {ftyp_count}/{len(existing)} segment(s) are self-initializing MP4 documents — decrypting each individually before merge instead of once after")
                decrypted_paths: list[Path] = []
                per_segment_ok = True
                for p in existing:
                    dec_p = p.with_suffix(p.suffix + ".dec")
                    try:
                        if Decryptor().decrypt(str(p), self.key, str(dec_p), stream_type=stream.type):
                            decrypted_paths.append(dec_p)
                        else:
                            per_segment_ok = False
                            logger.warning(f"{out_path.name}: per-segment decrypt failed for {p.name}")
                            break
                    except Exception as exc:
                        per_segment_ok = False
                        logger.warning(f"{out_path.name}: per-segment decrypt error for {p.name}: {exc}")
                        break

                if per_segment_ok:
                    paths = decrypted_paths
                    already_decrypted_per_segment = True
                else:
                    for dec_p in decrypted_paths:
                        dec_p.unlink(missing_ok=True)

        if _is_webvtt_sub:
            merged = merge_vtt_files(paths, merge_logger=logger)
            n_headers = merged.count("WEBVTT")
            if n_headers != 1:
                logger.warning(f"[merge_vtt] {out_path.name}: expected 1 WEBVTT header after merge, found {n_headers}")
            out_path.write_text(merged, encoding="utf-8")
            logger.debug(f"WebVTT cue-merge completed -> {out_path.name}")
        elif _live_merge_ok:
            logger.debug(f"Live merge already wrote {out_path.name} in order -- binary-merge pass skipped")
        else:
            binary_merge_segments(paths, out_path, merge_logger=logger)
            logger.debug(f"Binary merge completed -> {out_path.name}")
        logger.info(f"Merge finished -> {out_path.name} in {time.monotonic() - _merge_t0:.1f}s")

        # A raw-byte-concatenated MPEG-TS whose source has a genuine PCR/PTS discontinuity at a segment boundary
        if not is_plain_subtitle and _total_duration > 0 and out_path.suffix.lower() == ".ts" and out_path.exists():
            from VibraVid.core.muxing.helper.audio.probe import get_video_duration

            merged_duration = get_video_duration(str(out_path))
            if merged_duration and merged_duration < _total_duration * 0.95:
                logger.warning(f"{out_path.name}: merged duration {merged_duration:.1f}s is well short of the manifest's {_total_duration:.1f}s -- likely a mid-stream splice the raw concat can't express. Re-merging via ffmpeg's concat demuxer.")
                if concat_demux_merge_segments(paths, out_path, merge_logger=logger):
                    _fixed_duration = get_video_duration(str(out_path))
                    logger.info(f"{out_path.name}: concat-demuxer re-merge -> {_fixed_duration:.1f}s" if _fixed_duration else f"{out_path.name}: concat-demuxer re-merge done")

        if already_decrypted_per_segment:
            for p in paths:
                p.unlink(missing_ok=True)

        # Reset absolute fragment timestamps. Must run AFTER decryption
        def _normalize_out_path() -> None:
            if is_plain_subtitle or out_path.suffix.lower() not in (".mp4", ".m4s", ".m4a"):
                return

            from VibraVid.core.muxing.helper.video import normalize_timestamps

            norm_path = normalize_timestamps(out_path, logger)
            if norm_path is None:
                return

            try:
                out_path.unlink(missing_ok=True)
                norm_path.rename(out_path)
            except OSError as exc:
                logger.error(f"[normalize] rename-back failed, keeping un-normalized file: {exc}")
                norm_path.unlink(missing_ok=True)

        # Live-decrypted fragments keep their original absolute tfdt 
        _is_live_fragmented = (
            protocol_lower in ("dash", "ism", "hls")
            and live_decryption
            and stream_is_encrypted
            and not is_plain_subtitle
            and out_path.suffix.lower() in (".mp4", ".m4s", ".m4a")
        )
        if _is_live_fragmented:
            # Cheapest possible fix first: subtract the first fragment's tfdt from
            # every fragment's tfdt in place (a handful of small seeks+writes, no
            # resize, no ffmpeg) -- see _tfdt_rebase.py.
            from .util._tfdt_rebase import rebase_fragment_timestamps

            if not rebase_fragment_timestamps(out_path, logger=logger):
                if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() == "ffmpeg":
                    self._needs_join_ts_fix = True
                else:
                    _normalize_out_path()
        elif already_decrypted_per_segment or not ((not live_decryption) and self.key and stream_is_encrypted):
            _normalize_out_path()

        decrypted_ok = already_decrypted_per_segment
        decrypt_already_reported = False
        if already_decrypted_per_segment:
            # Each segment was already decrypted individually before the merge above — the merged file is plaintext already.
            logger.info(f"Decrypt already done per-segment -> {out_path.name}")
        elif _skip_post_decrypt() and stream_is_encrypted:
            logger.info(f"skip_post_decrypt: leaving {out_path.name} encrypted (raw merged track kept for testing)")
        elif (
            (not live_decryption)
            and self.key
            and stream_is_encrypted
            and out_path.exists()
            and out_path.stat().st_size > 0
            and not is_plain_subtitle
        ):
            post_merge_path = out_path.with_suffix(out_path.suffix + ".dec")

            # Continue this track's own progress bar for the decrypt phase: keep the track
            # label, just swap the status (the "@ Merge" text) for the decrypt method/backend
            def _decrypt_cb(parsed: dict[str, Any] | None) -> None:
                if not parsed:
                    return

                # Only the bar position (pct) and the status text change; segment count and
                # size stay as the merge left them — just "@ Merge" -> "@ CTR".
                bar_manager.handle_progress_line(
                    {
                        "task_key": task_key,
                        "pct": parsed.get("pct"),
                        "speed": parsed.get("status") or "Decrypt",
                    }
                )

            _decrypt_t0 = time.monotonic()
            logger.info(f"Decrypt starting -> {out_path.name}")
            try:
                decryptor = Decryptor()
                if decryptor.decrypt(
                    str(out_path), self.key, str(post_merge_path), stream_type=stream.type, progress_cb=_decrypt_cb
                ):
                    decrypted_ok = True
                    logger.info(f"Decrypt finished -> {out_path.name} in {time.monotonic() - _decrypt_t0:.1f}s")
                    try:
                        out_path.unlink(missing_ok=True)
                        post_merge_path.rename(out_path)
                        # Same fragmented-mp4 tfdt issue as the live-decrypt path above: defer the
                        # -avoid_negative_ts/-fflags +genpts fix into the Join Media ffmpeg pass
                        # instead of a second full-file remux here, when that pass can apply it.
                        if (
                            not is_plain_subtitle
                            and out_path.suffix.lower() in (".mp4", ".m4s", ".m4a")
                            and config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() == "ffmpeg"
                        ):
                            self._needs_join_ts_fix = True
                        else:
                            _normalize_out_path()
                    except Exception as exc:
                        logger.error(f"rename failed: {exc}")
                        if post_merge_path.exists():
                            try:
                                post_merge_path.unlink()
                            except Exception:
                                pass
                else:
                    decrypt_already_reported = True
                    kid_hint = ", ".join(stream.drm.get_all_kids()) if stream.drm else ""
                    track_label = f"{stream.type} {stream.resolution or stream.language or ''}".strip()
                    logger.warning(f"Decrypt failed -> {out_path.name} after {time.monotonic() - _decrypt_t0:.1f}s (kid={kid_hint or 'unknown'})")
                    bar_manager.handle_progress_line({"task_key": task_key, "speed": "Failed"})
                    with self._decrypt_failures_lock:
                        self.decrypt_failures.append(
                            {
                                "label": track_label,
                                "track": out_path.name,
                                "message": f"required KID(s): {kid_hint or 'unknown'}",
                            }
                        )
                    if post_merge_path.exists():
                        try:
                            post_merge_path.unlink()
                        except Exception:
                            pass

            except Exception as exc:
                logger.error(f"Decrypt error -> {out_path.name} after {time.monotonic() - _decrypt_t0:.1f}s: {exc}")

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.debug(f"{protocol.upper()} merged {len(paths):>4} segs -> {out_path.name} ({out_path.stat().st_size // 1024} KB)")

            if decrypted_ok:
                # Finalize the bar at 100%, keeping segment/size/status as-is.
                bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
            elif decrypt_already_reported:
                _progress(total, total, out_path.stat().st_size, 0.0, speed_label="Failed")
            elif _live_merge_ok:
                # Live merge already wrote out_path segment-by-segment as they
                # arrived (see the identical guard around line 925) -- no
                # separate merge pass ran here either, so "Merge" would be
                # mislabeling work that never happened. Just finalize pct.
                bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
            else:
                _progress(total, total, out_path.stat().st_size, 0.0, speed_label="Merge")
        else:
            logger.error(f"{protocol.upper()} binary merge produced empty file: {out_path}")

        # Signal completion unconditionally (success or failure)
        self._record_track_done(task_key, out_path if out_path.exists() and out_path.stat().st_size > 0 else None)
