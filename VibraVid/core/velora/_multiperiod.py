# 11.07.26

import logging
import os
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
    estimate_total_size as _estimate_total_size,
)
from .util.formatting import (
    format_size as _fmt_size,
)
from .util.formatting import (
    format_speed as _fmt_speed,
)

logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")


def _seg_number_from_path(path: Path) -> int:
    stem = path.stem
    if stem.startswith("seg_"):
        try:
            return int(stem[4:])
        except ValueError:
            pass
    return 999_999_999


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

        total = len(dl_segs)

        def _progress(
            done: int, total_: int, total_bytes: int, speed_bps: float, speed_label: str | None = None
        ) -> None:
            pct = int((done / total_) * 100) if total_ else 0
            estimated_total = _estimate_total_size(total_bytes, done, total_) if done > 0 else total_bytes
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

        decryptor = Decryptor() if self.key else None
        part_files: list[Path] = []

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

        if not part_files:
            logger.error("[multiperiod] no Period produced a usable file")
            return

        out_path = self.output_dir / self._out_filename(stream, "mp4")
        bar_manager.handle_progress_line({"task_key": task_key, "pct": 100, "speed": "Concat"})

        if _ffmpeg_concat(part_files, out_path) and out_path.exists() and out_path.stat().st_size > 0:
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
