# 01.04.25

import os
import re
import threading
from pathlib import Path
from typing import Any

from VibraVid.core.muxing.helper.chapters import persist_chapters_file, sort_chapters, write_ffmetadata_chapters
from VibraVid.core.muxing.helper.sub.convert import convert_subtitle, extract_vtt_from_wvtt_mp4
from VibraVid.core.muxing.helper.sub.disposition import (
    SubtitleDispositionInfo,
    build_subtitle_disposition_args,
    get_configured_disposition_language,
)
from VibraVid.core.muxing.helper.video.ts import is_mpegts_file
from VibraVid.core.muxing.streaming_mux import StreamingMuxFeeder
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import resolve_iso639_2, resolve_language_display_name
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager

from .constants import (
    STREAMING_MUX_MAX_WAIT_SECONDS,
    STREAMING_MUX_MIN_THROUGHPUT_BPS,
    STREAMING_MUX_MIN_WAIT_SECONDS,
    STREAMING_MUX_SECONDS_PER_SEGMENT,
    logger,
)
from .live_merge import _LiveMerger
from .sniff import _streaming_mux_enabled


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


def _audio_title_and_iso_lang(entry: Any) -> tuple[str, str]:
    if isinstance(entry, dict):
        lang_source = entry.get("language") or entry.get("name") or "und"
        title = entry.get("name") or resolve_language_display_name(lang_source)
    else:
        lang_source = entry.resolved_language or entry.language or "und"
        title = getattr(entry, "name", "") or resolve_language_display_name(lang_source)
    return title, resolve_iso639_2(lang_source)


def _subtitle_disposition_info(entry: Any) -> "SubtitleDispositionInfo":
    """Normalize one subtitle entry into the shared SubtitleDispositionInfo"""
    if isinstance(entry, dict):
        lang_source = entry.get("language") or entry.get("name") or ""
        forced = bool(entry.get("forced"))
        sdh = bool(entry.get("sdh"))
        cc = bool(entry.get("cc"))
    else:
        lang_source = entry.resolved_language or entry.language or ""
        forced = bool(getattr(entry, "forced", False))
        sdh = bool(getattr(entry, "is_sdh", False))
        cc = bool(getattr(entry, "is_cc", False))
    return SubtitleDispositionInfo(language=lang_source, forced=forced, sdh=sdh, cc=cc)


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


class StreamingMuxMixin:
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

        # Chapters (if the downloader queued any) -- injected as an extra ffmetadata input
        # in this same ffmpeg pass, mirroring join_media()'s own chapter_input_idx handling in merge.py
        chapter_input_idx: int | None = None
        raw_chapters = getattr(self, "_streaming_mux_chapters", None)
        if raw_chapters:
            chapters = sort_chapters(raw_chapters)
            if chapters[0]["seconds"] > 0:
                chapters = [{"name": "Intro", "seconds": 0}] + chapters

            persist_chapters_file(chapters, getattr(self, "temp_dir", None))
            self._streaming_mux_chapter_file = write_ffmetadata_chapters(chapters)
            chapter_input_idx = 1 + len(audio_paths) + len(subtitle_paths)
            cmd += ["-f", "ffmetadata", "-i", self._streaming_mux_chapter_file]

        cmd += ["-map", "0:v:0"]
        video_codecs = (getattr(video_stream, "codecs", "") or "").lower()
        if any(p in video_codecs for p in ("hev", "hvc", "dvh", "dvhe")):
            cmd += ["-tag:v", "hvc1"]

        for i in range(len(audio_paths)):
            cmd += ["-map", f"{i + 1}:a"]

        sub_input_base = 1 + len(audio_paths)
        for i in range(len(subtitle_paths)):
            cmd += ["-map", f"{sub_input_base + i}:s"]

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

        # Subtitle dispositions (forced/hearing_impaired/config-driven default) -- shared with join_media()'s
        cmd += build_subtitle_disposition_args(
            [_subtitle_disposition_info(e) for e in subtitle_entries],
            get_configured_disposition_language(),
        )

        if chapter_input_idx is not None:
            cmd += ["-map_metadata", str(chapter_input_idx)]

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

        # Not confirmed until _finish_streaming_mux_inner() sees the fast-path output actually succeed -- if it falls back to the normal mux instead
        self._streaming_mux_chapter_pending = chapter_input_idx is not None

        logger.info(f"streaming_mux: started early cross-track mux -> {output_path}")
        return feeder

    def _finish_streaming_mux(self, feeder: "StreamingMuxFeeder", live_merge_ok: bool) -> None:
        """Finalize the streaming-mux fast path, if it was started. If *live_merge_ok* is False, abort the fast path and fall back to the normal mux."""
        try:
            self._finish_streaming_mux_inner(feeder, live_merge_ok)
        finally:
            chapter_file = getattr(self, "_streaming_mux_chapter_file", None)
            if chapter_file:
                try:
                    os.unlink(chapter_file)
                except OSError:
                    pass

    def _finish_streaming_mux_inner(self, feeder: "StreamingMuxFeeder", live_merge_ok: bool) -> None:
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
        self.streaming_mux_chapters_injected = bool(getattr(self, "_streaming_mux_chapter_pending", False))
        logger.info(f"streaming_mux: fast-path cross-track mux succeeded -> {output_path}")
