# 01.04.25

import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from VibraVid.core.decryptor import decryptor as _decryptor
from VibraVid.core.decryptor._subprocess_runner import _tail_decode_check
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.utils import config_manager

from ..util.formatting import format_size as _fmt_size
from .constants import logger

# flux | ffmpeg counts as stuck when neither process shows a sign of life for this long.
_PIPE_IDLE_TIMEOUT_SECONDS = 180.0
_STDERR_TAIL_LINES = 40


def _pump(pipe, on_line: Callable[[str], None]) -> threading.Thread:
    """Feed every line of *pipe* to *on_line* from a daemon thread (a callback error must never stop the drain)."""

    def _run() -> None:
        try:
            for raw in pipe:
                try:
                    on_line(raw.decode("utf-8", errors="replace").rstrip())
                except Exception:
                    logger.debug("chunks-pipe: line handler failed", exc_info=True)
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class ChunksPipeMixin:
    def _chunks_pipe_eligible(self) -> bool:
        """Whether the video track may be piped into the final MKV; every refusal falls back to the file mode."""

        def _no(reason: str) -> bool:
            logger.info(f"chunks-pipe: not eligible ({reason})")
            return False

        if getattr(context_tracker, "no_livemux", False):
            return _no("--no-livemux")
        if getattr(context_tracker, "no_concurrent", False):
            return _no("--no-concurrent: the other tracks would not be ready")
        output_path = getattr(self, "_streaming_mux_output_path", None) or getattr(self, "output_path", None)
        if not output_path or not str(output_path).lower().endswith(".mkv"):
            return _no(f"output_path={output_path!r} is not .mkv")
        if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() != "ffmpeg":
            return _no("PROCESS.engine != ffmpeg")
        if getattr(self, "other_tracks", None):
            return _no(f"other_tracks={self.other_tracks}")
        flux_path = self._flux_path()
        if not _decryptor.flux_supports_tfdt_rebase(flux_path):
            return _no(f"flux {flux_path} lacks --chunks/--tfdt-rebase (needs >= 0.5.4)")

        logger.info(f"chunks-pipe: eligible (output={output_path}, flux={flux_path})")
        return True

    def _flux_path(self) -> str:
        return getattr(self, "flux_path", None) or _decryptor.Decryptor().flux_path

    def _discard_partial_pipe_output(self) -> None:
        """Remove the final MKV a failed chunks pipe may have left half-written (the pipe writes straight to it)."""
        output_path = getattr(self, "_streaming_mux_output_path", None)
        if output_path and not self._keep_tmp_on_error():
            try:
                os.remove(output_path)
            except OSError:
                pass

    def _try_chunks_pipe_mux(self, ctx, paths: list[Path], out_path: Path) -> bool:
        """Produce the final MKV with ``flux --chunks | ffmpeg``. False leaves nothing behind and the caller falls back to the file mode."""
        keys = self._resolve_norm_keys(os.path.join(str(ctx.stream_dir), "seg_00000.mp4"), self.key, self.key_fallback)
        if not keys:
            return False

        # Same command (titles, languages, dispositions, chapters, subtitles, hvc1 tag) as the live mux, with the video on stdin.
        plan = self._prepare_streaming_mux("mp4", ctx.stream, progress=True, chunks_pipe=True)
        if plan is None:
            return False
        try:
            return self._run_chunks_pipe(ctx, paths, plan, keys)
        finally:
            self._cleanup_streaming_mux_chapter_file()

    def _run_chunks_pipe(self, ctx, paths: list[Path], plan, keys: list[tuple[str, str]]) -> bool:
        flux_cmd = [self._flux_path(), "--chunks", str(ctx.stream_dir), "-o", "-", "--ffmpeg-path", plan.cmd[0], "--tfdt-rebase"]
        for kid, key in keys:
            flux_cmd += ["--key", f"{kid.lower()}:{key.lower()}"]
        logger.info(f"chunks-pipe: {_decryptor.Decryptor._redacted_cmd(flux_cmd)} | {' '.join(plan.cmd)}")

        total_in = 0
        for p in paths:
            try:
                total_in += p.stat().st_size
            except OSError:
                pass

        t0 = time.monotonic()
        last_sign_of_life = [t0]
        flux_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        ffmpeg_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

        def on_flux_line(line: str) -> None:
            last_sign_of_life[0] = time.monotonic()
            flux_tail.append(line)
            low = line.lower()
            if "error" in low or "warning" in low:
                logger.warning(f"[FLUX] {line}")
            elif line.startswith("[profile]"):
                logger.info(f"[FLUX] {line}")

        def on_ffmpeg_line(line: str) -> None:
            ffmpeg_tail.append(line)
            logger.debug(f"[FFMPEG] {line}")

        def on_progress_line(line: str) -> None:
            # ffmpeg -progress: total_size is the output size so far in bytes ("N/A" before the first packet).
            key, _, value = line.partition("=")
            if key not in ("total_size", "progress"):
                return
            last_sign_of_life[0] = time.monotonic()
            if key == "total_size" and value.isdigit():
                self._emit_pipe_progress(ctx, int(value), total_in)

        try:
            flux = subprocess.Popen(flux_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            logger.warning(f"chunks-pipe: cannot start flux ({exc}); falling back to file mode")
            return False
        try:
            ffmpeg = subprocess.Popen(plan.cmd, stdin=flux.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            _kill(flux)
            logger.warning(f"chunks-pipe: cannot start ffmpeg ({exc}); falling back to file mode")
            return False
        flux.stdout.close()  # ffmpeg owns the read end now; flux gets a broken pipe if ffmpeg dies

        pumps = [
            _pump(flux.stderr, on_flux_line),
            _pump(ffmpeg.stderr, on_ffmpeg_line),
            _pump(ffmpeg.stdout, on_progress_line),
        ]

        abort = getattr(self, "_abort_event", None)
        stalled = ""
        while True:
            try:
                ffmpeg.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                idle = time.monotonic() - last_sign_of_life[0]
                if abort is not None and abort.is_set():
                    stalled = "aborted"
                elif idle > _PIPE_IDLE_TIMEOUT_SECONDS:
                    stalled = f"no sign of life for {idle:.0f}s"
                if stalled:
                    break
        
        if not stalled:
            try:
                flux.wait(timeout=_PIPE_IDLE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                stalled = "flux did not exit after ffmpeg finished"
        
        if stalled:
            _kill(ffmpeg)
            _kill(flux)
        for thread in pumps:
            thread.join(timeout=2.0)

        if stalled or flux.returncode != 0 or ffmpeg.returncode != 0:
            logger.warning(
                f"chunks-pipe: failed ({stalled or f'flux rc={flux.returncode}, ffmpeg rc={ffmpeg.returncode}'}); falling back to file mode"
            )
            if flux_tail:
                logger.warning("chunks-pipe: flux stderr tail:\n" + "\n".join(flux_tail))
            if ffmpeg.returncode not in (0, None) and ffmpeg_tail:
                logger.warning("chunks-pipe: ffmpeg stderr tail:\n" + "\n".join(ffmpeg_tail))
            self._discard_partial_pipe_output()
            return False

        output_path = plan.output_path
        if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
            logger.warning("chunks-pipe: output missing or empty; falling back to file mode")
            self._discard_partial_pipe_output()
            return False

        # Backstop against a wrong key that slipped past flux's own check.
        decode_error = _tail_decode_check(output_path, seconds=4.0, full=False)
        if decode_error is not None:
            logger.warning(f"chunks-pipe: tail decode check failed, falling back to file mode: {decode_error}")
            self._discard_partial_pipe_output()
            return False

        self.streaming_mux_result = output_path
        self.streaming_mux_chapters_injected = plan.chapters_injected
        logger.info(f"chunks-pipe: finished -> {os.path.basename(output_path)} in {time.monotonic() - t0:.1f}s ({_fmt_size(os.path.getsize(output_path))})")
        return True

    @staticmethod
    def _emit_pipe_progress(ctx, size: int, total: int) -> None:
        """Progress is cosmetic: a UI failure must never fail the mux."""
        try:
            pct = min(99, size * 100 // total) if total else 0
            ctx.bar_manager.handle_progress_line(
                {"task_key": ctx.task_key, "pct": pct, "size": f"{_fmt_size(size)}/{_fmt_size(total)}", "speed": "Mux"}
            )
        except Exception:
            logger.debug("chunks-pipe: progress update failed", exc_info=True)
