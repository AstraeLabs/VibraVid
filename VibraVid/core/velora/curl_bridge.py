# 28.07.26

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from VibraVid.core.velora.util.formatting import format_size, format_speed, resolve_display_total
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

logger = logging.getLogger("curl_cffi_bridge")
SPEED_WINDOW_SECONDS = 3.0

_thread_local = threading.local()
_GLOBAL_CONCURRENCY_LIMIT = max(1, config_manager.config.get_int("DOWNLOAD", "thread_count"))
_global_semaphore = threading.BoundedSemaphore(_GLOBAL_CONCURRENCY_LIMIT)


def _get_thread_client(timeout: float, verify: bool, proxy_url: str | None):
    client = getattr(_thread_local, "client", None)
    if client is not None:
        return client

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    client = create_client(timeout=timeout, verify=verify, proxies=proxies, http2=False, browser="chrome")
    _thread_local.client = client
    return client


def _fetch_one(task: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Fetch a single segment with retry/backoff, mirroring the Velora plan's retry fields."""
    url = task.get("url", "")
    path = task.get("path", "")
    headers = dict(plan.get("headers") or {})
    headers.update(task.get("headers") or {})

    retry_count = int(plan.get("retry_count") or 0)
    base_delay = float(plan.get("retry_base_delay_seconds") or 1.0)
    max_delay = float(plan.get("retry_max_delay_seconds") or 4.0)
    jitter = float(plan.get("retry_jitter_seconds") or 0.0)
    timeout = float(plan.get("timeout_seconds") or 30.0)
    verify = bool(plan.get("verify_tls", True))
    proxy_url = plan.get("proxy_url")

    client = _get_thread_client(timeout=timeout, verify=verify, proxy_url=proxy_url)

    last_error: str | None = None
    for attempt in range(retry_count + 1):
        written = 0
        expected_length = None
        try:
            with _global_semaphore:
                response = client.get(url, headers=headers, timeout=timeout, stream=True)
                try:
                    response.raise_for_status()
                    expected_length = response.headers.get("content-length")
                    with open(path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            f.write(chunk)
                            written += len(chunk)
                finally:
                    response.close()

            if not written or (expected_length is not None and written != int(expected_length)):
                raise ValueError(f"Incomplete/empty response body (got {written} bytes, expected {expected_length})")

            return {
                "event": "completed",
                "task_key": task.get("task_key"),
                "label": task.get("label"),
                "display_label": task.get("display_label"),
                "url": url,
                "path": path,
                "bytes": written,
                "skipped": False,
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < retry_count:
                delay = min(base_delay * (attempt + 1), max_delay) + random.uniform(0, jitter)
                time.sleep(delay)

    return {
        "event": "error",
        "task_key": task.get("task_key"),
        "label": task.get("label"),
        "display_label": task.get("display_label"),
        "url": url,
        "path": path,
        "message": last_error or "Unknown error",
        "attempt": retry_count,
        "retry_count": retry_count,
    }


def run_download_plan_curl_cffi(
    plan: dict[str, Any],
    progress_cb: Callable[[int, int, int, float], None] | None = None,
    event_cb: Callable[[dict[str, Any]], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
    known_total: int = 0,
) -> list[dict[str, Any]]:
    """Run a Velora download plan using curl_cffi with concurrency, progress reporting, and retry/backoff."""
    tasks = plan.get("tasks") or []
    total = len(tasks)
    if total == 0:
        return []

    concurrency = max(1, int(plan.get("concurrency") or 1))

    parent_dirs = {Path(task["path"]).parent for task in tasks if task.get("path")}
    for parent_dir in parent_dirs:
        parent_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    done_count = 0
    total_bytes = 0
    prev_estimated = 0
    speed_window: deque[tuple[float, int]] = deque()
    speed_window.append((time.monotonic(), 0))
    lock = threading.Lock()

    logger.debug(f"Starting curl_cffi download plan for {plan.get('task_key', 'download')} with {total} segments")

    executor = ThreadPoolExecutor(max_workers=min(concurrency, total))
    try:
        futures = {executor.submit(_fetch_one, task, plan): task for task in tasks}

        for future in as_completed(futures):
            if stop_check and stop_check():
                executor.shutdown(cancel_futures=True)
                break

            event = future.result()
            event_name = event.get("event")

            with lock:
                if event_name == "completed":
                    done_count += 1
                    bytes_written = int(event.get("bytes") or 0)
                    total_bytes += bytes_written
                elif event_name == "error":
                    logger.warning(f"curl_cffi segment failed: {event.get('url')} — {event.get('message')}")

                now = time.monotonic()
                speed_window.append((now, total_bytes))
                while len(speed_window) > 1 and now - speed_window[0][0] > SPEED_WINDOW_SECONDS:
                    speed_window.popleft()
                window_start_at, window_start_bytes = speed_window[0]
                speed = (total_bytes - window_start_bytes) / max(now - window_start_at, 0.001)

                if event_name == "completed":
                    if progress_cb:
                        try:
                            progress_cb(done_count, total, total_bytes, speed)
                        except Exception as exc:
                            logger.debug(f"progress_cb raised: {exc}")

                    if event_cb:
                        estimated_total = resolve_display_total(total_bytes, done_count, total, known_total=known_total, prev_estimated=prev_estimated)
                        prev_estimated = estimated_total
                        progress_event = dict(event)
                        progress_event["pct"] = int((done_count / total) * 100) if total else 100
                        progress_event["segments"] = f"{done_count}/{total}"
                        progress_event["size"] = (
                            f"{format_size(total_bytes)}/{format_size(estimated_total)}"
                            if estimated_total
                            else format_size(total_bytes)
                        )
                        progress_event["speed"] = format_speed(speed)
                        try:
                            event_cb(progress_event)
                        except Exception as exc:
                            logger.debug(f"event_cb raised: {exc}")

                    results.append(
                        {
                            "path": event.get("path"),
                            "bytes": event.get("bytes"),
                            "task_key": event.get("task_key"),
                            "label": event.get("label"),
                            "display_label": event.get("display_label"),
                            "skipped": False,
                        }
                    )
                elif event_cb:
                    normalized_event = dict(event)
                    normalized_event.setdefault("segments", f"{done_count}/{total}")
                    normalized_event.setdefault("speed", "ERR")
                    try:
                        event_cb(normalized_event)
                    except Exception as exc:
                        logger.debug(f"event_cb raised: {exc}")
    finally:
        executor.shutdown(wait=True)

    return results
