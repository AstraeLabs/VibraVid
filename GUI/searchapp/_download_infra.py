# 09.06.26

import atexit
import concurrent.futures
import logging
import os
import re
import signal
import threading
import time
from typing import Any

from VibraVid.cli.run import execute_hooks
from VibraVid.core.ui.tracker import download_tracker

logger = logging.getLogger(__name__)

__all__ = [
    "download_executor",
    "scheduled_downloads",
    "scheduled_downloads_lock",
    "cancelled_scheduled_downloads",
    "set_max_download_slots",
    "_acquire_download_slot",
    "_release_download_slot",
    "_add_scheduled_download",
    "_remove_scheduled_download",
    "_cancel_scheduled_download",
    "_remove_queued_download",
    "_clear_queued_downloads",
    "_is_scheduled_cancelled",
    "_extract_series_base_title",
    "_same_series",
    "_get_scheduled_downloads",
    "_enrich_active_downloads_with_series",
    "_prune_scheduled_downloads",
    "shutdown_downloads",
    "_submit_download_task",
    "signal_handler",
]


download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="DownloadWorker")
scheduled_downloads: dict[str, dict[str, Any]] = {}
scheduled_downloads_lock = threading.Lock()
cancelled_scheduled_downloads: set[str] = set()

# ── Download concurrency limiter
_download_slot_cond = threading.Condition()
_active_downloads = 0
_max_download_slots = 1


def set_max_download_slots(n: int) -> None:
    global _max_download_slots
    _max_download_slots = max(1, n)
    with _download_slot_cond:
        _download_slot_cond.notify_all()


def _acquire_download_slot() -> None:
    global _active_downloads
    with _download_slot_cond:
        while _active_downloads >= _max_download_slots:
            _download_slot_cond.wait()
        _active_downloads += 1


def _release_download_slot() -> None:
    global _active_downloads
    with _download_slot_cond:
        _active_downloads -= 1
        _download_slot_cond.notify()


def _add_scheduled_download(
    download_id: str, title: str, site: str, media_type: str = "Film", season: str = None, episodes: str = None,
    poster: str = None, planned_episodes: list[int] | None = None,
    stop_scope: str = "download", batch_id: str | None = None,
) -> None:
    with scheduled_downloads_lock:
        scheduled_downloads[download_id] = {
            "id": download_id,
            "title": title,
            "site": site,
            "type": media_type,
            "season": season,
            "episodes": episodes,
            "poster": poster,
            "planned_episodes": planned_episodes,
            "stop_scope": stop_scope,
            "batch_id": batch_id,
            "scheduled_at": time.time(),
        }
        cancelled_scheduled_downloads.discard(download_id)


def _remove_scheduled_download(download_id: str) -> None:
    with scheduled_downloads_lock:
        scheduled_downloads.pop(download_id, None)
        cancelled_scheduled_downloads.discard(download_id)


def _cancel_scheduled_download(download_id: str) -> None:
    with scheduled_downloads_lock:
        cancelled_scheduled_downloads.add(download_id)
        scheduled_downloads.pop(download_id, None)


def _remove_queued_download(download_id: str, active_ids: set[str] | None = None) -> bool:
    """Cancel one scheduled download only if it has not started."""
    active_ids = active_ids or set()
    if not download_id or download_id in active_ids:
        return False

    with scheduled_downloads_lock:
        if download_id not in scheduled_downloads:
            return False
        cancelled_scheduled_downloads.add(download_id)
        scheduled_downloads.pop(download_id, None)
        return True


def _clear_queued_downloads(active_ids: set[str] | None = None) -> list[str]:
    """Cancel all scheduled downloads that have not started."""
    active_ids = active_ids or set()

    with scheduled_downloads_lock:
        queued_ids = [
            download_id
            for download_id in scheduled_downloads
            if download_id not in active_ids
        ]
        for download_id in queued_ids:
            cancelled_scheduled_downloads.add(download_id)
            scheduled_downloads.pop(download_id, None)

    return queued_ids


def _is_scheduled_cancelled(download_id: str) -> bool:
    with scheduled_downloads_lock:
        return download_id in cancelled_scheduled_downloads


def _extract_series_base_title(raw_title: str) -> str:
    """Normalize title to a stable series base name (strip season/episode suffixes)."""
    title = str(raw_title or "").strip()
    if not title:
        return ""
    # Examples: "Show - S1", "Show - S1 E3", "Show - S01 E01-02"
    base = re.split(r"\s-\sS\d+(?:\sE[\d\-\*,]+)?", title, maxsplit=1, flags=re.IGNORECASE)[0]
    return base.strip()


def _same_series(title: str, series_base: str) -> bool:
    if not series_base:
        return False
    return _extract_series_base_title(title).casefold() == series_base.casefold()


def _get_scheduled_downloads(exclude_ids: set | None = None) -> list[dict[str, Any]]:
    exclude_ids = exclude_ids or set()
    progress = {}
    for item in download_tracker.get_history() + download_tracker.get_active_downloads():
        episode = item.get("episode")
        if isinstance(episode, int):
            progress[item.get("id")] = max(progress.get(item.get("id"), 0), episode)
    with scheduled_downloads_lock:
        # Keep progress independent from the user-clearable, bounded history.
        for item in scheduled_downloads.values():
            item["last_episode"] = max(item.get("last_episode", 0), progress.get(item["id"], 0))
        scheduled = sorted((dict(item) for item in scheduled_downloads.values()),
                           key=lambda item: item.get("scheduled_at", 0))
    rows = []
    for item in scheduled:
        planned = item.pop("planned_episodes", None)
        if planned:
            for episode in planned:
                if episode <= item.get("last_episode", 0):
                    continue
                rows.append({**item, "id": f"{item['id']}:E{episode}",
                             "parent_id": item["id"], "episodes": str(episode),
                             "title": f"{_extract_series_base_title(item['title'])} - S{item['season']} E{episode}"})
        elif item["id"] not in exclude_ids:
            rows.append(item)
    return rows


def _enrich_active_downloads_with_series(active_downloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach series_name for active TV downloads so GUI can show the parent series."""
    with scheduled_downloads_lock:
        scheduled_by_id = {k: dict(v) for k, v in scheduled_downloads.items()}

    enriched: list[dict[str, Any]] = []
    for item in active_downloads:
        row = dict(item)
        media_type = str(row.get("type") or "").lower()
        scheduled_info = scheduled_by_id.get(row.get("id"), {})
        row["stop_scope"] = scheduled_info.get("stop_scope") or (
            "episode" if media_type in {"serie", "tv", "series", "anime"} else "download"
        )

        if media_type in {"serie", "tv", "series", "anime"}:
            series_name = ""
            row_id = row.get("id")

            scheduled_info = scheduled_by_id.get(row_id)
            if scheduled_info:
                series_name = _extract_series_base_title(scheduled_info.get("title", ""))

            if not series_name:
                title = str(row.get("title") or "").strip()
                title_base = _extract_series_base_title(title)
                # Only trust title-derived series name when title contains the Sxx suffix pattern.
                if title_base and title_base != title:
                    series_name = title_base

            if series_name:
                row["series_name"] = series_name

        enriched.append(row)

    return enriched


def _prune_scheduled_downloads(_active_downloads: list[dict[str, Any]], history: list[dict[str, Any]]) -> None:
    """Jobs are removed by their worker, never by age or episode history.

    A queued season can legitimately wait more than six hours, and all its
    episodes share the job ID. Neither condition means the job has finished.
    """
    return


def shutdown_downloads():
    """Shutdown the download executor and clear scheduled downloads."""
    logging.disable(logging.CRITICAL)

    with scheduled_downloads_lock:
        scheduled_downloads.clear()
        cancelled_scheduled_downloads.clear()
    download_tracker.shutdown()
    download_executor.shutdown(wait=True)


def _submit_download_task(fn):
    """Submit a task to the download executor, recreating it if it was shutdown."""
    global download_executor
    try:
        return download_executor.submit(fn)
    except RuntimeError:
        # Executor has been shutdown (interpreter shutdown or explicit call). Recreate.
        try:
            download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="DownloadWorker")
            return download_executor.submit(fn)
        except Exception as exc:
            logger.exception("Could not recreate download executor: %s", exc)
            raise


# Ensure downloads are shut down on exit
atexit.register(shutdown_downloads)


# Handle SIGINT and SIGTERM to shutdown properly
def signal_handler(signum, frame):
    shutdown_thread = threading.Thread(target=shutdown_downloads, daemon=True)
    shutdown_thread.start()

    logger.info("Running post-run hooks...")
    hooks_thread = threading.Thread(target=execute_hooks, args=("post_run",), daemon=True)
    hooks_thread.start()
    hooks_thread.join(timeout=5)

    logger.info("Downloads shutdown started, exiting immediately...")
    os._exit(0)


if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
