# 16.04.24

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque

from VibraVid.core.ui.bar_manager import console
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.velora.util.formatting import parse_time_scalar
from VibraVid.utils.os import internet_manager
from VibraVid.utils.proc import log_command

logger = logging.getLogger(__name__)
terminate_flag = threading.Event()


class ProgressData:
    """Class to store the last progress data"""

    def __init__(self):
        self.last_data = None
        self.last_lines: list[str] = []
        self.lock = threading.Lock()

    def update(self, data):
        with self.lock:
            self.last_data = data

    def get(self):
        with self.lock:
            return self.last_data

    def set_last_lines(self, lines: list[str]) -> None:
        with self.lock:
            self.last_lines = lines

    def get_last_lines(self) -> list[str]:
        with self.lock:
            return list(self.last_lines)


def _format_eta(eta_seconds: float) -> str:
    """Format ETA seconds into a human-readable string."""
    eta_seconds = max(0, int(eta_seconds))
    h = eta_seconds // 3600
    m = (eta_seconds % 3600) // 60
    s = eta_seconds % 60

    if h > 0:
        return f"{h}h {m:02d}m"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"


def _iter_output_chunks(stream):
    buf = ""
    while True:
        ch = stream.read(1)
        if ch == "":
            if buf:
                yield buf
            return
        if ch in ("\n", "\r"):
            if buf:
                yield buf
            buf = ""
        else:
            buf += ch


def capture_output(
    process: subprocess.Popen,
    description: str,
    progress_data: ProgressData,
    terminate_flag: threading.Event = None,
    total_duration: float | None = None,
    output_path: str | None = None,
) -> None:
    """
    Function to capture and print output from a subprocess.

    Parameters:
        process (subprocess.Popen): The subprocess whose output is captured.
        description (str): Description of the command being executed.
        progress_data (ProgressData): Object to store the last progress data.
        log_path (Optional[str]): Path to log file to write output.
        terminate_flag (threading.Event): Per-invocation flag to signal termination.
        total_duration (Optional[float]): Total video duration in seconds, used to compute ETA.
        output_path (Optional[str]): Path to the file being written by the subprocess, used to compute file size for progress reporting.
    """
    if terminate_flag is None:
        terminate_flag = threading.Event()

    tail_lines: deque[str] = deque(maxlen=20)
    _start_time = time.monotonic()

    try:
        max_length = 0
        last_progress_string = ""

        for line in _iter_output_chunks(process.stdout):
            try:
                line = line.strip()
                logger.debug(f"{line}")

                if not line:
                    continue

                tail_lines.append(line)
                if terminate_flag.is_set():
                    logger.info("FFmpeg process cancelled")
                    break

                if line.startswith("Progress:") and "%" in line:
                    try:
                        pct = int(re.search(r"Progress:\s*(\d+)%", line).group(1))

                        elapsed = max(time.monotonic() - _start_time, 0.001)
                        processed_sec = (pct / 100.0) * total_duration if total_duration else None
                        speed = f"{processed_sec / elapsed:.2f}x" if processed_sec else "N/A"
                        eta_str = "N/A"
                        if processed_sec is not None and processed_sec > 0 and total_duration:
                            eta_str = _format_eta((total_duration - processed_sec) / (processed_sec / elapsed)) if processed_sec / elapsed > 0 else "N/A"

                        byte_size = 0
                        if output_path:
                            try:
                                byte_size = os.path.getsize(output_path)
                            except OSError:
                                byte_size = 0

                        json_data = {
                            "progress_pct": pct,
                            "speed": speed,
                            "size": internet_manager.format_file_size(byte_size),
                            "eta": eta_str,
                        }
                        progress_data.update(json_data)

                        if context_tracker.is_parallel_cli and context_tracker.download_id:
                            download_tracker.update_progress(
                                context_tracker.download_id, "mkvmerge_join", speed=speed, status=f"joining ({pct}%)"
                            )
                        elif context_tracker.should_print:
                            progress_string = (
                                f"{description}[white]: "
                                f"([dim]progress:[/] [yellow]{pct}%[/], "
                                f"[dim]speed:[/] [yellow]{speed}[/], "
                                f"[dim]size:[/] [yellow]{internet_manager.format_file_size(byte_size)}[/], "
                                f"[dim]ETA:[/] [yellow]{eta_str}[/])"
                            )
                            max_length = max(max_length, len(progress_string))
                            last_progress_string = progress_string.ljust(max_length)
                            console.print(last_progress_string, end="\r")
                    except Exception as e:
                        logger.error(f"Error parsing mkvmerge progress line: {line} - {e}")
                    continue

                if "size=" in line:
                    try:
                        data = parse_output_line(line)

                        # The final summary line uses "Lsize=" instead of "size=" --
                        size_key = "Lsize" if "Lsize" in data else "size"
                        byte_size = int(re.findall(r"\d+", data.get(size_key, "0"))[0]) * 1000

                        speed = data.get("speed", "N/A")
                        bitrate = data.get("bitrate", "N/A")
                        time_processed = data.get("time", "N/A")

                        # Compute ETA from total_duration and time already processed
                        eta_str = "N/A"
                        if total_duration and total_duration > 0:
                            processed_sec = parse_time_scalar(time_processed)
                            if processed_sec is not None and processed_sec > 0:
                                remaining_sec = total_duration - processed_sec
                                eta_str = _format_eta(remaining_sec)

                        json_data = {
                            "speed": speed,
                            "size": internet_manager.format_file_size(byte_size),
                            "bitrate": bitrate,
                            "time": time_processed,
                            "eta": eta_str,
                        }
                        progress_data.update(json_data)

                        if context_tracker.is_parallel_cli and context_tracker.download_id:
                            download_tracker.update_progress(
                                context_tracker.download_id,
                                "ffmpeg_join",
                                speed=f"{speed}",
                                size=internet_manager.format_file_size(byte_size),
                                status="joining",
                            )
                        elif context_tracker.should_print:
                            progress_string = (
                                f"{description}[white]: "
                                f"([dim]speed:[/] [yellow]{speed}[/], "
                                f"[dim]size:[/] [yellow]{internet_manager.format_file_size(byte_size)}[/], "
                                f"[dim]bitrate:[/] [yellow]{bitrate}[/], "
                                f"[dim]ETA:[/] [yellow]{eta_str}[/])"
                            )
                            max_length = max(max_length, len(progress_string))
                            last_progress_string = progress_string.ljust(max_length)
                            console.print(last_progress_string, end="\r")

                    except Exception as e:
                        logger.error(f"Error parsing output line: {line} - {e}")

            except Exception as e:
                logger.error(f"Error processing line from subprocess: {e}")

    except Exception as e:
        logger.error(f"Error in capture_output: {e}")

    finally:
        progress_data.set_last_lines(list(tail_lines))
        try:
            terminate_process(process)
        except Exception as e:
            logger.error(f"Error terminating process: {e}")


def parse_output_line(line: str) -> dict:
    """
    Function to parse the output line and extract relevant information.

    Parameters:
        line (str): The output line to parse.

    Returns:
        dict: A dictionary containing parsed information.
    """
    try:
        data = {}
        parts = line.replace("  ", "").replace("= ", "=").split()

        for part in parts:
            key_value = part.split("=")

            if len(key_value) == 2:
                key = key_value[0]
                value = key_value[1]

                if key == "time" and isinstance(value, str) and "." in value:
                    value = value.split(".")[0]
                data[key] = value

        return data

    except Exception as e:
        logger.error(f"Error parsing line: {line} - {e}")
        return {}


def terminate_process(process):
    """
    Function to terminate a subprocess if it's still running.

    Parameters:
        process (subprocess.Popen): The subprocess to terminate.
    """
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except Exception:
                process.kill()
    except Exception as e:
        logger.error(f"Failed to terminate process: {e}")


def capture_ffmpeg_real_time(
    ffmpeg_command: list,
    description: str,
    total_duration: float | None = None,
    wait_timeout_seconds: float = 1800.0,
    output_path: str | None = None,
) -> dict:
    """
    Function to capture real-time output from ffmpeg process.

    Parameters:
        ffmpeg_command (list): The command to execute ffmpeg.
        description (str): Description of the command being executed.
        total_duration (Optional[float]): Total video duration in seconds, used to compute ETA.

    Returns:
        dict: JSON dictionary with the last progress data.
    """
    terminate_flag = threading.Event()
    terminate_flag.clear()

    progress_data = ProgressData()

    _parent_download_id = context_tracker.download_id
    _parent_is_parallel = context_tracker.is_parallel_cli
    process: subprocess.Popen | None = None
    output_thread: threading.Thread | None = None
    timed_out = False

    try:
        log_command(ffmpeg_command, f"Starting ffmpeg process for {description}", log=logger)
        process = subprocess.Popen(
            ffmpeg_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )

        def _output_worker():
            context_tracker.download_id = _parent_download_id
            context_tracker.is_parallel_cli = _parent_is_parallel
            capture_output(process, description, progress_data, terminate_flag, total_duration, output_path)

        output_thread = threading.Thread(target=_output_worker, daemon=True)
        output_thread.start()

        try:
            process.wait(timeout=max(wait_timeout_seconds, 1.0))
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.error(f"FFmpeg timed out after {wait_timeout_seconds:.1f}s; terminating process")
            terminate_flag.set()
            terminate_process(process)
        except KeyboardInterrupt:
            logger.error("Terminating ffmpeg process...")
        except Exception as e:
            logger.error(f"Error in ffmpeg process: {e}")
        finally:
            terminate_flag.set()
            if process and process.stdout:
                try:
                    process.stdout.close()
                except Exception:
                    pass

            if output_thread:
                output_thread.join(timeout=10.0)
                if output_thread.is_alive():
                    logger.warning("FFmpeg output thread did not terminate within timeout")

    except Exception as e:
        logger.error(f"Failed to start ffmpeg process: {e}")

    result = progress_data.get() or {}
    if process is not None:
        result.setdefault("exit_code", process.returncode)
    result.setdefault("timed_out", timed_out)
    result.setdefault("last_lines", progress_data.get_last_lines())
    return result
