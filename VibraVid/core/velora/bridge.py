# 01.04.26

import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from VibraVid.core.velora.util.formatting import format_size, format_speed, normalize_path_key, resolve_display_total
from VibraVid.setup import get_velora_path

logger = logging.getLogger("velora_bridge")
_QUEUE_SENTINEL = object()
_EVENT_CB_LOCK = threading.Lock()
_PROGRESS_CB_LOCK = threading.Lock()
DEFAULT_WAIT_TIMEOUT_SECONDS = 900.0
SPEED_WINDOW_SECONDS = 3.0


def _safe_event_cb(event_cb: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if not event_cb:
        return
    try:
        with _EVENT_CB_LOCK:
            event_cb(event)
    except Exception as exc:
        logger.debug(f"event_cb raised: {exc}")


def _safe_progress_cb(
    progress_cb: Callable[[int, int, int, float], None] | None, done: int, total: int, total_bytes: int, speed: float
) -> None:
    if not progress_cb:
        return
    try:
        with _PROGRESS_CB_LOCK:
            progress_cb(done, total, total_bytes, speed)
    except Exception as exc:
        logger.debug(f"progress_cb raised: {exc}")


def _request_stop_via_stdin(process: subprocess.Popen[str]) -> bool:
    if process.stdin is None or process.poll() is not None:
        return False
    try:
        process.stdin.write('{"event":"stop"}\n')
        process.stdin.flush()
        return True
    except Exception as exc:
        logger.debug(f"Failed to send stop event on stdin: {exc}")
        return False


def _terminate_process_tree(process: subprocess.Popen[str], graceful_timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return

    try:
        process.terminate()
        process.wait(timeout=graceful_timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception as exc:
        logger.debug(f"Graceful terminate failed: {exc}")

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                text=True,
            )
        except Exception as exc:
            logger.debug(f"taskkill failed: {exc}")
    else:
        try:
            import signal

            os.killpg(process.pid, signal.SIGKILL)
        except Exception as exc:
            logger.debug(f"killpg failed: {exc}")

    try:
        process.wait(timeout=2.0)
    except Exception:
        pass


def _stop_process_orderly(process: subprocess.Popen[str], graceful_timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return

    sent = _request_stop_via_stdin(process)
    if sent:
        try:
            process.wait(timeout=graceful_timeout)
            return
        except subprocess.TimeoutExpired:
            logger.debug("Stop event sent but process did not exit in time")
        except Exception as exc:
            logger.debug(f"Error while waiting after stop event: {exc}")

    _terminate_process_tree(process, graceful_timeout=graceful_timeout)


def _format_header_keys(headers: dict[str, Any] | None) -> str:
    """Return a comma-separated, sorted list of non-sensitive header key names."""
    if not headers:
        return ""
    keys = [str(k) for k in headers.keys() if str(k).strip()]
    if not keys:
        return ""

    filtered = [k for k in keys if k.lower() not in {"authorization", "cookie"}]
    display_keys = filtered or keys
    return ",".join(sorted(display_keys))


def _format_bridge_event(event: dict[str, Any]) -> str:
    """Format a Velora event dict into a human-readable string for logging."""
    event_name = (event.get("event") or "").lower()
    label = event.get("display_label") or event.get("label") or event.get("task_key") or "download"
    url = event.get("url") or ""
    path = event.get("path") or ""
    headers = _format_header_keys(event.get("headers") if isinstance(event.get("headers"), dict) else None)
    elapsed_seconds = event.get("elapsed_seconds")

    if event_name == "start":
        return f"START {label} | tasks={event.get('task_count', '?')} | concurrency={event.get('concurrency', '?')}"

    if event_name == "summary":
        elapsed_display = f"{float(elapsed_seconds):.1f}s" if isinstance(elapsed_seconds, (int, float)) else "?"
        return f"SUMMARY {label} | completed={event.get('completed', '?')}/{event.get('total', '?')} | bytes={format_size(int(event.get('bytes') or 0))} | elapsed={elapsed_display}"

    if event_name == "progress":
        return f"TICK {label} | segments={event.get('segments', '?')} | size={event.get('size', '?')} | speed={event.get('speed', '?')}"

    if event_name == "completed":
        parts = [f"GET {url}" if url else "GET", f"PATH: {path}" if path else None]
        if headers:
            parts.append(f"HEADERS: {headers}")

        parts.extend(
            [
                f"segments={event.get('segments', '?')}",
                f"size={event.get('size', '?')}",
                f"speed={event.get('speed', '?')}",
                f"in={float(elapsed_seconds):.1f}s" if isinstance(elapsed_seconds, (int, float)) else None,
                f"skipped={bool(event.get('skipped', False))}",
            ]
        )
        return f"DONE {label} | " + " | ".join(p for p in parts if p)

    if event_name == "retry":
        parts = [f"GET {url}" if url else "GET", f"PATH: {path}" if path else None]
        if headers:
            parts.append(f"HEADERS: {headers}")

        parts.extend(
            [
                f"RETRY={event.get('attempt', '?')}/{event.get('retry_count', '?')}",
                f"ERROR: {event.get('message', event.get('error', ''))}",
                f"in={float(elapsed_seconds):.1f}s" if isinstance(elapsed_seconds, (int, float)) else None,
            ]
        )
        return f"RETRY {label} | " + " | ".join(p for p in parts if p)

    if event_name == "error":
        parts = [f"GET {url}" if url else "GET", f"PATH: {path}" if path else None]
        if headers:
            parts.append(f"HEADERS: {headers}")

        parts.extend(
            [
                f"ERROR: {event.get('message', '')}",
                f"RETRY={event.get('attempt', '?')}/{event.get('retry_count', '?')}"
                if event.get("attempt") is not None
                else None,
                f"in={float(elapsed_seconds):.1f}s" if isinstance(elapsed_seconds, (int, float)) else None,
            ]
        )
        return f"ERROR {label} | " + " | ".join(p for p in parts if p)

    if event_name == "cancelled":
        return f"CANCELLED {label} | {event.get('message', 'Cancellation requested')}"

    return f"{event_name.upper() or 'EVENT'} {label} | {event}"


def _normalize_event_task_key(event: dict[str, Any]) -> dict[str, Any]:
    if "_task_key" in event and "task_key" not in event:
        event = dict(event)
        event["task_key"] = event.pop("_task_key")

    return event


def run_download_plan(
    plan: dict[str, Any],
    progress_cb: Callable[[int, int, int, float], None] | None = None,
    event_cb: Callable[[dict[str, Any]], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
    wait_timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    known_total: int = 0,
) -> list[dict[str, Any]]:
    """
    Launch the Velora binary for *plan* and stream its events back to the caller.

        - plan: A fully-populated Velora download-plan dict (will be serialised to a temporary JSON file on disk).
        - progress_cb: Called with ``(done_count, total, total_bytes, speed_bps)`` after each completed segment.
        - event_cb: Called with the raw (normalised) event dict for every ``completed``, ``retry``, ``error`` and ``cancelled`` event.
        - stop_check: Zero-argument callable; when it returns ``True`` the Velora process is terminated and the function returns immediately.
        - known_total: Exact byte total (HTTP Content-Length or stream.estimated_size) for stable ``X/Y`` display.

    Returns: List of ``{"path", "bytes", "task_key", "label", "display_label", "skipped"}`` dicts — one per successfully completed segment.
    """
    binary_path = get_velora_path()
    if not binary_path:
        raise FileNotFoundError("Velora binary not found")

    tasks = plan.get("tasks") or []
    total = len(tasks)
    if total == 0:
        return []

    task_lookup_by_path: dict[str, dict[str, Any]] = {
        normalize_path_key(task.get("path", "")): task for task in tasks if task.get("path")
    }

    plan_path: str | None = None
    process: subprocess.Popen[str] | None = None
    stop_thread: threading.Thread | None = None
    reader_thread: threading.Thread | None = None
    results: list[dict[str, Any]] = []
    done_count = 0
    total_bytes = 0
    started_at = time.monotonic()
    prev_estimated = 0
    speed_window: deque[tuple[float, int]] = deque()
    speed_window.append((started_at, 0))

    try:
        # Write plan to a temp file.
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as tmp:
            plan_path = tmp.name
            try:
                os.chmod(plan_path, 0o600)
            except Exception:
                pass
            json.dump(plan, tmp, ensure_ascii=False)
            tmp.flush()

        command = (
            ["dotnet", binary_path, plan_path] if binary_path.lower().endswith(".dll") else [binary_path, plan_path]
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=(os.name != "nt"),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )

        # Read stdout in a dedicated thread → queue so the main loop cannot block forever if the process crashes silently.
        line_queue: queue.Queue[object] = queue.Queue()

        def _stdout_reader() -> None:
            assert process is not None and process.stdout is not None
            try:
                for raw_line in process.stdout:
                    line_queue.put(raw_line)
            finally:
                line_queue.put(_QUEUE_SENTINEL)

        reader_thread = threading.Thread(target=_stdout_reader, daemon=True)
        reader_thread.start()

        if stop_check:

            def _watch_stop() -> None:
                assert process is not None
                logger.debug("Stop-watcher thread started")
                while process.poll() is None:
                    if stop_check():
                        logger.warning("Stop requested, forwarding stop event to Velora...")
                        _stop_process_orderly(process, graceful_timeout=5.0)
                        return

                    time.sleep(0.5)
                logger.debug("Stop-watcher thread exiting (process already dead)")

            stop_thread = threading.Thread(target=_watch_stop, daemon=True)
            stop_thread.start()

        while True:
            try:
                item = line_queue.get(timeout=0.5)
            except queue.Empty:
                # Check if process died and queue is drained.
                if process.poll() is not None and line_queue.empty():
                    logger.debug(f"Process exited with code {process.returncode}, queue empty, exiting main loop")
                    break
                continue

            if item is _QUEUE_SENTINEL:
                break

            if stop_check and stop_check():
                _stop_process_orderly(process, graceful_timeout=5.0)
                break

            raw_line = item
            line = str(raw_line).strip()
            if not line:
                continue

            try:
                event: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                logger.info(f"Failed to decode JSON from Velora: {line}")
                continue

            # Enrich event with url/headers from the plan when missing.
            path_key = normalize_path_key(str(event.get("path") or ""))
            if path_key and path_key in task_lookup_by_path:
                task = task_lookup_by_path[path_key]
                event.setdefault("url", task.get("url"))
                task_headers = task.get("headers") if isinstance(task.get("headers"), dict) else {}
                if task_headers:
                    event.setdefault("headers", task_headers)

            event = _normalize_event_task_key(event)
            event_name = (event.get("event") or "").lower()

            if event_name in {"start", "summary"}:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(_format_bridge_event(event))
                continue

            if event_name == "retry":
                logger.warning(_format_bridge_event(event))
                if event_cb:
                    normalized_event = dict(event)
                    normalized_event.setdefault("task_key", plan.get("task_key", "download"))
                    normalized_event.setdefault("label", plan.get("label", ""))
                    normalized_event.setdefault("display_label", plan.get("display_label", ""))
                    normalized_event.setdefault("segments", "0/1")
                    normalized_event.setdefault("speed", "ERR")
                    _safe_event_cb(event_cb, normalized_event)
                continue

            if event_name in {"error", "cancelled"}:
                if event_name == "error":
                    logger.warning(_format_bridge_event(event))
                else:
                    logger.info(_format_bridge_event(event))

                if event_cb:
                    normalized_event = dict(event)
                    normalized_event.setdefault("task_key", plan.get("task_key", "download"))
                    normalized_event.setdefault("label", plan.get("label", ""))
                    normalized_event.setdefault("display_label", plan.get("display_label", ""))
                    normalized_event.setdefault("segments", "0/1")
                    normalized_event.setdefault("speed", "ERR")
                    _safe_event_cb(event_cb, normalized_event)
                continue

            if event_name == "progress":
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(_format_bridge_event(event))

                live_bytes = int(event.get("total_bytes") or 0)
                display_bytes = max(total_bytes, live_bytes)  # monotonic merge; does NOT mutate the ledger

                now = time.monotonic()
                speed_window.append((now, display_bytes))
                while len(speed_window) > 1 and now - speed_window[0][0] > SPEED_WINDOW_SECONDS:
                    speed_window.popleft()
                window_start_at, window_start_bytes = speed_window[0]
                speed = (display_bytes - window_start_bytes) / max(now - window_start_at, 0.001)
                _safe_progress_cb(progress_cb, done_count, total, display_bytes, speed)

                progress_event = dict(event)
                progress_event.pop("total_bytes", None)
                progress_event.setdefault("task_key", plan.get("task_key", "download"))
                progress_event.setdefault("label", plan.get("label", ""))
                progress_event.setdefault("display_label", plan.get("display_label", ""))
                progress_event.setdefault("pct", int((done_count / total) * 100) if total else 100)
                progress_event.setdefault("segments", f"{done_count}/{total}")
                estimated_total = max(
                    resolve_display_total(
                        total_bytes, done_count, total,
                        known_total=known_total,
                        prev_estimated=prev_estimated,
                    ),
                    display_bytes,
                )
                prev_estimated = estimated_total
                progress_event["size"] = (
                    f"{format_size(display_bytes)}/{format_size(estimated_total)}"
                    if estimated_total
                    else format_size(display_bytes)
                )
                progress_event["speed"] = format_speed(speed)

                _safe_event_cb(event_cb, progress_event)
                continue

            if event_name == "completed" or "path" in event:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(_format_bridge_event(event))
                done_count += 1
                bytes_written = int(event.get("bytes") or 0)
                total_bytes += bytes_written

                now = time.monotonic()
                speed_window.append((now, total_bytes))
                while len(speed_window) > 1 and now - speed_window[0][0] > SPEED_WINDOW_SECONDS:
                    speed_window.popleft()
                window_start_at, window_start_bytes = speed_window[0]
                speed = (total_bytes - window_start_bytes) / max(now - window_start_at, 0.001)
                _safe_progress_cb(progress_cb, done_count, total, total_bytes, speed)

                progress_event = dict(event)
                progress_event.setdefault("task_key", plan.get("task_key", "download"))
                progress_event.setdefault("label", plan.get("label", ""))
                progress_event.setdefault("display_label", plan.get("display_label", ""))
                progress_event.setdefault("pct", int((done_count / total) * 100) if total else 100)
                progress_event.setdefault("segments", f"{done_count}/{total}")
                estimated_total = resolve_display_total(
                    total_bytes, done_count, total,
                    known_total=known_total,
                    prev_estimated=prev_estimated,
                )
                prev_estimated = estimated_total
                progress_event.setdefault(
                    "size",
                    f"{format_size(total_bytes)}/{format_size(estimated_total)}"
                    if estimated_total
                    else format_size(total_bytes),
                )
                progress_event.setdefault("final_size", format_size(bytes_written))
                progress_event.setdefault("speed", format_speed(speed))

                _safe_event_cb(event_cb, progress_event)

                results.append(
                    {
                        "path": event.get("path"),
                        "bytes": bytes_written,
                        "task_key": progress_event.get("task_key"),
                        "label": progress_event.get("label"),
                        "display_label": progress_event.get("display_label"),
                        "skipped": bool(event.get("skipped", False)),
                    }
                )
                continue

            _safe_event_cb(event_cb, event)

        if process is not None:
            try:
                return_code = process.wait(timeout=max(wait_timeout_seconds, 1.0))
            except subprocess.TimeoutExpired:
                logger.error(f"Velora wait timeout ({wait_timeout_seconds:.1f}s), terminating process tree")
                _terminate_process_tree(process, graceful_timeout=2.0)
                return_code = process.returncode
            if return_code not in (0, None):
                logger.warning(f"Velora exited with code {return_code}")

        return results

    finally:
        # Clean up temp plan file
        if plan_path:
            try:
                Path(plan_path).unlink(missing_ok=True)
                logger.debug(f"Deleted temp plan: {plan_path}")
            except Exception as e:
                logger.warning(f"Failed to delete temp plan: {e}")

        # Terminate velora process with robust cleanup
        if process:
            try:
                if process.poll() is None:
                    logging.info("Terminating velora process...")
                    _stop_process_orderly(process, graceful_timeout=5.0)
            except Exception as e:
                logger.error(f"Error terminating velora: {e}")

        # Join reader thread with longer timeout
        if reader_thread and reader_thread.is_alive():
            logger.debug("Waiting for reader thread...")
            reader_thread.join(timeout=5.0)
            if reader_thread.is_alive():
                logger.warning("Reader thread didn't finish in 5s")

        # Join stop-watcher thread with longer timeout
        if stop_thread and stop_thread.is_alive():
            logger.debug("Waiting for stop-watcher thread...")
            stop_thread.join(timeout=5.0)
            if stop_thread.is_alive():
                logger.warning("Stop-watcher thread didn't finish in 5s")
            if stop_thread.is_alive():
                logger.warning("Stop-watcher thread did not finish within timeout — may be dangling")
