# 01.04.25

import time
from typing import TYPE_CHECKING

from VibraVid.core.manifest.stream import track_label

from ..util.formatting import estimate_eta as _estimate_eta
from ..util.formatting import fmt_dur as _fmt_dur
from ..util.formatting import format_size as _fmt_size
from ..util.formatting import format_speed as _fmt_speed
from ..util.formatting import resolve_display_total as _resolve_display_total

if TYPE_CHECKING:
    from .context import _SegmentDownloadContext


class ProgressMixin:
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

    def _stream_stop(self, ctx: "_SegmentDownloadContext") -> bool:
        return self._stop_check() or self._kids_poisoned(self._stream_kids(ctx.stream)) is not None

    def _progress(
        self,
        ctx: "_SegmentDownloadContext",
        done: int,
        total_: int,
        total_bytes: int,
        speed_bps: float,
        speed_label: str | None = None,
    ) -> None:
        ctx.last_total_bytes = total_bytes
        if not ctx.first_bytes_logged and total_bytes > 0:
            ctx.first_bytes_logged = True
            from .constants import logger

            logger.info(
                f"{ctx.protocol.upper()} first bytes received | id={ctx.stream.id!r} | type={ctx.stream.type} | {ctx.task_key}"
            )

        known_total = int(getattr(ctx.stream, "estimated_size", 0) or 0)
        estimated_total = _resolve_display_total(
            total_bytes, done, total_, known_total=known_total,
            known_exact=bool(getattr(ctx.stream, "estimated_size_exact", False)),
        )
        if known_total > 0:
            pct = min(100, int((total_bytes / estimated_total) * 100)) if estimated_total else 0
        else:
            pct = int((done / total_) * 100) if total_ else 0

        size_display = (
            f"{_fmt_size(total_bytes)}/{_fmt_size(estimated_total)}"
            if done < total_
            else f"{_fmt_size(total_bytes)}/{_fmt_size(total_bytes)}"
        )
        duration_display = ""

        if ctx.total_duration > 0:
            media_done = max(0, done - (1 if any(s.get("seg_type") == "init" for s in ctx.dl_segs) else 0))
            elapsed_dur = (
                ctx.seg_dur_cumulative[media_done - 1]
                if media_done > 0 and media_done <= len(ctx.seg_dur_cumulative)
                else 0.0
            )
            duration_display = f"{_fmt_dur(elapsed_dur)}/{_fmt_dur(ctx.total_duration)}"

        ctx.bar_manager.handle_progress_line(
            {
                "task_key": ctx.task_key,
                "label": ctx.progress_label or ctx.task_key,
                "display_label": ctx.progress_label or ctx.task_key,
                "pct": pct,
                "segments": f"{done}/{total_}",
                "size": size_display,
                "speed": speed_label if speed_label is not None else _fmt_speed(speed_bps),
                "eta": None if speed_label is not None else _estimate_eta(estimated_total - total_bytes, speed_bps),
                "duration": duration_display,
            }
        )
