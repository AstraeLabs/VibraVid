# 01.04.26

import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from VibraVid.core.ui.bar_manager import console
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)
_ENGINE_LOG_LEVELS = {"FLUX": 23}
_TAIL_SEEK_NOISE = re.compile(
    r"co located POCs unavailable|"
    r"reference picture missing during reorder|"
    r"Missing reference picture|"
    r"non-existing (PPB|SL|DPB)|"
    r"error while decoding MB \d+"
)


for _engine_name, _level in _ENGINE_LOG_LEVELS.items():
    logging.addLevelName(_level, _engine_name)


def _log_engine_output_enabled() -> bool:
    override = getattr(context_tracker, "log_engine_output", None)
    if override is not None:
        return bool(override)
    return config_manager.config.get_bool("DRM", "log_engine_output")


def _strip_profile_lines(text: str) -> str:
    """Drop `transmux`'s `TRANSMUX_PROFILE` diagnostic lines"""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("[profile]"))


def _tail_decode_check(output_path: str, seconds: float = 4.0, full: bool = False, _retry_depth: int = 0) -> str | None:
    """
    Decode *output_path* with ffmpeg to catch a decrypt engine reporting exit-0/non-empty-file on genuinely corrupt content.

    Uses a tail window (default 4s) for video, or full decode for audio.
    Automatically retries with a larger window if the only errors are benign H264 seek-artifacts.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None

    cmd = [ffmpeg, "-v", "error", "-fflags", "+discardcorrupt"]
    if not full:
        cmd += ["-sseof", f"-{seconds}"]
    cmd += ["-i", output_path, "-f", "null", "-"]

    try:
        logger.info(f"{'full' if full else 'tail'} decode check for {output_path}: running ffmpeg cmd: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120 if full else 30,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except Exception as exc:
        logger.debug(f"{'full' if full else 'tail'} decode check could not run for {output_path}: {exc}")
        return None

    stderr_text = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if not stderr_text:
        return None

    # Filter out lines that are only H264 seek noise, and retry with a longer tail if that's all we see.
    lines = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()]
    real_errors = [ln for ln in lines if not _TAIL_SEEK_NOISE.search(ln)]

    if not real_errors and lines and not full and _retry_depth < 2:
        retry_seconds = seconds * 2
        logger.debug(f"tail decode check for {output_path}: only H264 seek noise at {seconds}s, retrying with {retry_seconds}s")
        
        return _tail_decode_check(
            output_path,
            seconds=retry_seconds,
            full=full,
            _retry_depth=_retry_depth + 1,
        )

    return "\n".join(real_errors) if real_errors else None



def _render_bar(percent: int, length: int = 10) -> str:
    """Return a Rich-formatted inline progress bar string."""
    filled = int((percent / 100) * length)
    bar = "[dim][[/dim]" + f"[green]{'=' * filled}[/green]" + f"[dim]{'-' * (length - filled)}[/dim]" + "[dim]][/dim]"
    return f"{bar} [dim]{percent:3d}%[/dim]"


def run_with_progress(
    cmd: list,
    label: str,
    encrypted_path: str,
    output_path: str,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: int | None = 1800,
    status: str | None = None,
    engine_name: str | None = None,
    stream_type: str = "video",
) -> tuple:
    """
    Launch *cmd* as a subprocess and monitor its progress by watching how
    fast the *output_path* file grows relative to *encrypted_path*.

    Progress is reported either via *progress_cb* (dict with ``task_key``,
    ``pct``, etc.) or by printing an inline Rich bar to the console.

    Returns:
        ``True`` on success (exit-code 0 and output file > 1 kB).
        ``(False, stderr_text)`` on failure.
    """
    file_size = os.path.getsize(encrypted_path) if os.path.isfile(encrypted_path) else 0
    progress_percent = 0
    last_rendered_percent = -1
    stop_monitor = threading.Event()
    last_progress_update = time.monotonic()
    last_observed_percent = -1
    process_holder = {"process": None}
    task_key = f"decrypt_{os.path.basename(encrypted_path)}"

    def _emit(percent: int, current_size: int) -> None:
        if progress_cb is None:
            return

        try:
            progress_cb(
                {
                    "task_key": task_key,
                    "label": label,
                    "status": status,
                    "pct": percent,
                    "segments": f"{percent}/100",
                    "compact_metrics": True,
                }
            )
        except Exception as exc:
            logger.debug(f"progress_cb error for {task_key}: {exc}")

    def _monitor() -> None:
        nonlocal progress_percent, last_progress_update, last_observed_percent
        while not stop_monitor.is_set():
            now = time.monotonic()
            process = process_holder["process"]
            running = process is not None and process.poll() is None

            if os.path.exists(output_path) and file_size > 0:
                current_size = os.path.getsize(output_path)
                observed_percent = min(int((current_size / file_size) * 100), 99)
                if observed_percent != last_observed_percent:
                    last_observed_percent = observed_percent
                    progress_percent = observed_percent
                    last_progress_update = now
                    _emit(progress_percent, current_size)

                elif running and progress_percent < 99 and now - last_progress_update >= 0.20:
                    progress_percent = min(progress_percent + 1, 99)
                    last_progress_update = now
                    _emit(progress_percent, current_size)

            elif running and progress_percent < 90 and now - last_progress_update >= 0.30:
                progress_percent = min(progress_percent + 1, 90)
                last_progress_update = now
                _emit(progress_percent, 0)

            time.sleep(0.05)

    engine_level = _ENGINE_LOG_LEVELS.get((engine_name or "").upper(), logging.INFO)
    log_engine_output = (engine_name or "").upper() in _ENGINE_LOG_LEVELS and _log_engine_output_enabled()
    def _log_engine_line(line: str) -> None:
        stripped = line.rstrip()
        if stripped:
            logger.log(engine_level, stripped)

    stderr_lines: list[str] = []
    stdout_lines: list[str] = []
    logger.info(f"Starting subprocess for {label}: {' '.join(cmd)}")
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        process_holder["process"] = process

        monitor_thread = threading.Thread(target=_monitor, daemon=True)
        monitor_thread.start()

        def _read_stderr() -> None:
            for line in process.stderr:
                stderr_lines.append(line)
                if log_engine_output:
                    _log_engine_line(line)

        def _read_stdout() -> None:
            for line in process.stdout:
                stdout_lines.append(line)
                if log_engine_output:
                    _log_engine_line(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()
        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stdout_thread.start()

        if progress_cb is None:
            console.print(f"{label} {_render_bar(0)}", end="\r")
        else:
            _emit(0, 0)

        start_wait = time.monotonic()
        while process.poll() is None:
            if progress_cb is None and progress_percent != last_rendered_percent:
                console.print(f"{label} {_render_bar(progress_percent)}", end="\r")
                last_rendered_percent = progress_percent

            if timeout_seconds and (time.monotonic() - start_wait) > timeout_seconds:
                process.kill()
                return False, f"Timeout after {timeout_seconds}s"

            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue

        process.wait()
        stderr_thread.join(timeout=2)
        stdout_thread.join(timeout=2)

    except Exception as exc:
        stop_monitor.set()
        return False, str(exc)
    finally:
        stop_monitor.set()

    final_percent = 100 if process.returncode == 0 else progress_percent
    final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

    if progress_cb is None:
        console.print(f"{label} {_render_bar(final_percent)}")
    else:
        _emit(final_percent, final_size)

    if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        full_check = stream_type == "audio"
        decode_error = _tail_decode_check(output_path, full=full_check)
        if decode_error is None:
            return True
        check_label = "full" if full_check else "tail"
        logger.warning(f"{label}: exit 0 and output present, but {check_label} decode check found errors — treating as failed: {decode_error}")
        return False, f"{check_label} decode check failed:\n{decode_error}"

    stderr_text = _strip_profile_lines("".join(stderr_lines)).strip()
    stdout_text = "".join(stdout_lines).strip()
    combined = (stderr_text + ("\n" + stdout_text if stdout_text else "")).strip()
    return False, combined
