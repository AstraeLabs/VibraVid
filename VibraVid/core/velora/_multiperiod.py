# 11.07.26

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from VibraVid.core.decryptor import Decryptor
from VibraVid.core.muxing.helper.video import binary_merge_segments
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager

from .util.formatting import (
    format_size as _fmt_size,
)
from .util.formatting import (
    format_speed as _fmt_speed,
)
from .util.formatting import (
    resolve_display_total as _resolve_display_total,
)

logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
_VTT_HEADER = re.compile(r"^\ufeff?WEBVTT", re.IGNORECASE)
_VTT_CUE = re.compile(
    r"^(\d{2,}):(\d{2}):(\d{2})[.,](\d{3})"
    r"\s*-->\s*"
    r"(\d{2,}):(\d{2}):(\d{2})[.,](\d{3})"
    r"(.*)$"
)
_VTT_PERIOD_ZERO_TOLERANCE = 60.0


def _stream_confirmed_unencrypted(stream) -> bool:
    drm = getattr(stream, "drm", None)
    return drm is not None and not drm.is_encrypted()


def _seg_number_from_path(path: Path) -> int:
    stem = path.stem
    if stem.startswith("seg_"):
        try:
            return int(stem[4:])
        except ValueError:
            pass
    return 999_999_999


def _vtt_parts_to_s(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _vtt_s_to_stamp(value: float) -> str:
    value = max(0.0, value)
    h = int(value // 3600)
    m = int((value % 3600) // 60)
    s = value - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _vtt_cue_bounds(text: str) -> tuple[float | None, float | None]:
    """First cue start and last cue end of a WebVTT payload, in seconds."""
    first: float | None = None
    last: float | None = None
    for line in text.splitlines():
        m = _VTT_CUE.match(line)
        if not m:
            continue
        if first is None:
            first = _vtt_parts_to_s(m.group(1), m.group(2), m.group(3), m.group(4))
        last = _vtt_parts_to_s(m.group(5), m.group(6), m.group(7), m.group(8))
    return first, last


def _vtt_timeline_is_absolute(bodies: list[str]) -> bool:
    """Whether the per-Period payloads already sit on the presentation timeline."""
    prev_end: float | None = None
    for idx, body in enumerate(bodies):
        first, last = _vtt_cue_bounds(body)
        if first is None:
            continue
        if idx == 0:
            if first > _VTT_PERIOD_ZERO_TOLERANCE:
                return False
        elif prev_end is not None and first < prev_end:
            return False
        prev_end = last if prev_end is None else max(prev_end, last)
    return True


def _vtt_strip_header(text: str) -> str:
    """Drop the ``WEBVTT`` signature and its metadata block, keeping the cues."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or not _VTT_HEADER.match(lines[0]):
        return text
    i = 1
    while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
        i += 1
    if i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:])


def _vtt_shift(text: str, offset: float) -> str:
    """Move every cue in a Period-relative payload onto the presentation timeline."""
    if offset <= 0:
        return text
    out: list[str] = []
    for line in text.splitlines():
        m = _VTT_CUE.match(line)
        if m:
            start = _vtt_parts_to_s(m.group(1), m.group(2), m.group(3), m.group(4)) + offset
            end = _vtt_parts_to_s(m.group(5), m.group(6), m.group(7), m.group(8)) + offset
            line = f"{_vtt_s_to_stamp(start)} --> {_vtt_s_to_stamp(end)}{m.group(9)}"
        out.append(line)
    return "\n".join(out)


def _merge_plain_subtitle_parts(part_files: list[Path], period_offsets: list[float], out_path: Path) -> str:
    """Join plain WebVTT Period parts into ``out_path``."""
    bodies = [p.read_text(encoding="utf-8", errors="replace") for p in part_files]
    absolute = _vtt_timeline_is_absolute(bodies)

    chunks: list[str] = []
    for idx, body in enumerate(bodies):
        cue_text = _vtt_strip_header(body)
        if not absolute and idx < len(period_offsets):
            cue_text = _vtt_shift(cue_text, period_offsets[idx])
        if cue_text.strip():
            chunks.append(cue_text.rstrip("\n") + "\n\n")

    out_path.write_text("WEBVTT\n\n" + "".join(chunks), encoding="utf-8")
    return "absolute" if absolute else "period-relative (shifted)"


def _run_ffmpeg_concat(list_path: Path, out_path: Path, codec_args: list[str]) -> subprocess.CompletedProcess:
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-fflags",
        "+genpts+igndts+discardcorrupt",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        *codec_args,
        "-avoid_negative_ts",
        "make_zero",
    ]
    if out_path.suffix.lower() == ".m4a":
        cmd += ["-f", "mp4"]
    cmd.append(str(out_path))

    logger.info(f"Running _run_ffmpeg_concat for {os.path.basename(out_path)} with cmd: {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1800,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )


def _ffmpeg_concat(part_files: list[Path], out_path: Path) -> bool:
    """Join already-decrypted per-Period MP4s into ``out_path`` (stream copy)."""
    if len(part_files) == 1:
        shutil.move(str(part_files[0]), str(out_path))
        return True

    logger.info(f"[multiperiod] ffmpeg concat {len(part_files)} Period file(s) -> {out_path.name}")

    list_path = out_path.parent / f"{out_path.stem}_concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as fh:
        for p in part_files:
            escaped = str(p.resolve()).replace("'", "'\\''")
            fh.write(f"file '{escaped}'\n")

    try:
        result = _run_ffmpeg_concat(list_path, out_path, ["-c", "copy"])
    except Exception as exc:
        logger.error(f"[multiperiod] ffmpeg concat failed to run: {exc}")
        return False
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except Exception:
            pass

    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size <= 0:
        logger.error(f"[multiperiod] ffmpeg concat failed (rc={result.returncode}): {(result.stderr or '')[-800:]}")
        
        # ffmpeg can leave a truncated partial file behind even after erroring out
        # (e.g. it wrote Period 0 fine, then hit invalid data joining Period 1) --
        # remove it so downstream code doesn't mistake it for a complete track.
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False
    return True


class MultiPeriodMixin:
    def _download_dash_multiperiod(
        self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False
    ) -> None:
        """Per-Period download - merge - decrypt - ffmpeg-concat for multi-period DASH."""
        all_headers = self._build_headers()
        stream_dir = self._make_stream_dir(stream, "dash")
        task_key = self._stream_task_key(stream)

        # Period appearance order (preserves manifest order).
        ordered_periods: list[int] = []
        for seg in stream.segments:
            if seg.period_idx not in ordered_periods:
                ordered_periods.append(seg.period_idx)

        # One flat download list with continuous numbering, remembering each
        # segment's Period so we can regroup after the download completes.
        dl_segs: list[dict[str, Any]] = []
        next_num = 0
        for seg in stream.segments:
            entry: dict[str, Any] = {
                "url": seg.url,
                "number": next_num,
                "seg_type": seg.seg_type,
                "enc": {"method": "NONE"},
                "period_idx": seg.period_idx,
            }
            if seg.byte_range:
                entry["headers"] = {"Range": f"bytes={seg.byte_range}"}
            dl_segs.append(entry)
            next_num += 1

        self._assign_segment_durations(stream, dl_segs, all_headers)

        seg_start, seg_end = self.max_segments if isinstance(self.max_segments, tuple) else (0, self.max_segments)
        if seg_start > 0 or seg_end is not None:
            dl_segs = dl_segs[seg_start:seg_end]
            logger.debug(f"Limiting multi-period DASH download to segments [{seg_start}:{seg_end}] ({len(dl_segs)} segments)")
        dl_segs = self._apply_max_time(dl_segs)

        num_to_period = {e["number"]: e["period_idx"] for e in dl_segs}

        # Presentation-timeline start of each Period, rebuilt from the segment
        # durations the parser recorded. Only plain subtitle payloads that turn
        # out to be Period-relative consume it, but deriving it is cheap.
        period_durations: dict[int, float] = {}
        for e in dl_segs:
            src_idx = e["number"]
            if 0 <= src_idx < len(stream.segments):
                seg_dur = getattr(stream.segments[src_idx], "duration", 0.0) or 0.0
                if seg_dur > 0:
                    period_durations[e["period_idx"]] = period_durations.get(e["period_idx"], 0.0) + seg_dur

        period_start_of: dict[int, float] = {}
        _elapsed = 0.0
        for per in ordered_periods:
            period_start_of[per] = _elapsed
            _elapsed += period_durations.get(per, 0.0)

        total = len(dl_segs)
        _prev_estimated = [0]

        def _progress(
            done: int, total_: int, total_bytes: int, speed_bps: float, speed_label: str | None = None
        ) -> None:
            known_total = int(getattr(stream, "estimated_size", 0) or 0)
            estimated_total = _resolve_display_total(
                total_bytes, done, total_, known_total=known_total, prev_estimated=_prev_estimated[0]
            )
            _prev_estimated[0] = estimated_total
            if known_total > 0:
                pct = min(100, int((total_bytes / known_total) * 100)) if total_bytes else 0
            else:
                pct = int((done / total_) * 100) if total_ else 0
            size_display = (
                f"{_fmt_size(total_bytes)}/{_fmt_size(estimated_total)}"
                if done < total_
                else f"{_fmt_size(total_bytes)}/{_fmt_size(total_bytes)}"
            )
            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "pct": pct,
                    "segments": f"{done}/{total_}",
                    "size": size_display,
                    "speed": speed_label if speed_label is not None else _fmt_speed(speed_bps),
                }
            )

        paths = self._run_dl(dl_segs, stream_dir, all_headers, _progress, stream=stream, default_ext="mp4")

        if self._stop_check() or not paths:
            return

        # Regroup downloaded files by Period.
        period_paths: dict[int, list[Path]] = {p: [] for p in ordered_periods}
        for p in paths:
            n = _seg_number_from_path(p)
            per = num_to_period.get(n)
            if per is not None and p.exists() and p.stat().st_size > 0:
                period_paths.setdefault(per, []).append(p)

        decryptor = Decryptor() if self.key and not _stream_confirmed_unencrypted(stream) else None
        part_files: list[Path] = []
        part_offsets: list[float] = []

        # A plain-WebVTT subtitle Period is raw text concatenated by
        # binary_merge_segments below never real fragmented MP4
        is_plain_subtitle = (
            stream is not None
            and getattr(stream, "type", "") == "subtitle"
            and not getattr(stream, "is_wvtt_mp4", False)
        )

        for order_idx, per in enumerate(ordered_periods):
            p_paths = sorted(period_paths.get(per, []), key=_seg_number_from_path)
            if not p_paths:
                continue

            part_merged = stream_dir / f"period_{order_idx:03d}.mp4"
            bar_manager.handle_progress_line({"task_key": task_key, "pct": 100, "speed": f"Merge P{order_idx}"})
            binary_merge_segments(p_paths, part_merged, merge_logger=logger)

            if not part_merged.exists() or part_merged.stat().st_size <= 0:
                logger.error(f"[multiperiod] Period {order_idx} merge produced empty file")
                continue

            # Decrypt if keys are available - Decryptor auto-detects and simply
            # copies clear Periods (e.g. a clear intro), decrypts encrypted ones.
            if decryptor is not None:
                dec_path = part_merged.with_suffix(".dec.mp4")

                def _dec_cb(parsed: dict[str, Any] | None, order_idx: int = order_idx) -> None:
                    if parsed:
                        bar_manager.handle_progress_line(
                            {
                                "task_key": task_key,
                                "pct": parsed.get("pct"),
                                "speed": parsed.get("status") or f"Dec P{order_idx}",
                            }
                        )

                try:
                    if (
                        decryptor.decrypt(
                            str(part_merged), self.key, str(dec_path), stream_type=stream.type, progress_cb=_dec_cb
                        )
                        and dec_path.exists()
                        and dec_path.stat().st_size > 0
                    ):
                        part_merged.unlink(missing_ok=True)
                        dec_path.rename(part_merged)
                    else:
                        logger.warning(f"[multiperiod] Period {order_idx} decryption failed - keeping raw merge")
                        dec_path.unlink(missing_ok=True)
                        
                except Exception as exc:
                    logger.error(f"[multiperiod] Period {order_idx} decryption error: {exc}")
                    try:
                        dec_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            # Absolute fragment timestamps get reset later, in the Join Media
            # ffmpeg pass, same as the single-period pipeline does (see
            # _decrypt_pipeline.py) -- not here.
            if not is_plain_subtitle:
                self._needs_join_ts_fix = True

            part_files.append(part_merged)
            part_offsets.append(period_start_of.get(per, 0.0))

        if not part_files:
            logger.error("[multiperiod] no Period produced a usable file")
            return

        out_path = self.output_dir / self._out_filename(stream, "mp4")
        bar_manager.handle_progress_line({"task_key": task_key, "pct": 100, "speed": "Concat"})

        if is_plain_subtitle:
            basis = _merge_plain_subtitle_parts(part_files, part_offsets, out_path)
            joined = out_path.exists() and out_path.stat().st_size > 0
            logger.info(f"[multiperiod] joined {len(part_files)} plain WebVTT Period file(s) -> {out_path.name} (cue timeline: {basis})")
        else:
            joined = _ffmpeg_concat(part_files, out_path) and out_path.exists() and out_path.stat().st_size > 0

        if joined:
            logger.info(f"[multiperiod] {len(part_files)} Period(s), {len(dl_segs)} segs joined -> {out_path.name} ({out_path.stat().st_size // 1024} KB)")
            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "pct": 100,
                    "segments": f"{total}/{total}",
                    "size": f"{_fmt_size(out_path.stat().st_size)}/{_fmt_size(out_path.stat().st_size)}",
                    "speed": "Done",
                }
            )
        else:
            logger.error(f"[multiperiod] failed to assemble final file for {stream.type} {stream.resolution or stream.language}")

        # Clean up per-Period intermediates.
        for pf in part_files:
            try:
                pf.unlink(missing_ok=True)
            except Exception:
                pass
