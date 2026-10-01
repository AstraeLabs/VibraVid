# 01.04.25

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..curl_bridge import run_download_plan_curl_cffi


def _run_curl_cffi_fallback(
    fallback_tasks: list[dict],
    fallback_plan: dict,
    done_before: int,
    bytes_before: int,
    total: int,
    progress_cb: Callable[..., None] | None,
    event_cb: Callable[[dict[str, Any]], None] | None,
    stop_check: Callable[[], bool] | None,
) -> list[Path]:
    """Run curl_cffi recovery downloads for segments that exhausted the primary backend's retries, delegating to the shared runner so cancellation and progress reporting behave like the primary path."""
    if not fallback_tasks:
        return []

    def _offset_progress(done: int, _fallback_total: int, total_bytes: int, speed: float) -> None:
        if progress_cb:
            progress_cb(done_before + done, total, bytes_before + total_bytes, speed)

    plan = dict(fallback_plan)
    plan["tasks"] = fallback_tasks
    plan.setdefault("concurrency", min(8, max(1, len(fallback_tasks))))

    results = run_download_plan_curl_cffi(
        plan,
        progress_cb=_offset_progress,
        event_cb=event_cb,
        stop_check=stop_check,
    )
    return [Path(item["path"]) for item in results if item.get("path")]
