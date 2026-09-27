# 06.06.25

import json
import logging
from datetime import datetime, timezone

from django.http import HttpRequest, JsonResponse

from VibraVid.core.ui.tracker import download_tracker

from .._download_infra import (
    _cancel_scheduled_download,
    _enrich_active_downloads_with_series,
    _extract_series_base_title,
    _get_scheduled_downloads,
    _prune_scheduled_downloads,
    _same_series,
    cancelled_scheduled_downloads,
    scheduled_downloads,
    scheduled_downloads_lock,
)
from ._shared import _tag_anime_entries

logger = logging.getLogger(__name__)


def get_downloads_json(request: HttpRequest) -> JsonResponse:
    """API endpoint to get real-time download progress."""
    active_downloads = _enrich_active_downloads_with_series(download_tracker.get_active_downloads())
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    active_ids = {d.get("id") for d in active_downloads if d.get("id")}
    scheduled = _get_scheduled_downloads(exclude_ids=active_ids)

    try:
        wanted = int(request.GET.get("history", ""))
    except (TypeError, ValueError):
        wanted = 0
    if wanted > 0:
        history = history[:wanted]

    return JsonResponse({
        "active": _tag_anime_entries(active_downloads),
        "scheduled": _tag_anime_entries(scheduled),
        "history": _tag_anime_entries(history)
    })


def get_downloads_summary(request: HttpRequest) -> JsonResponse:
    """API endpoint exposing a compact, read-only summary of the download queue
    (counts + current item), meant for external dashboards (e.g. Homepage)."""
    active_downloads = _enrich_active_downloads_with_series(download_tracker.get_active_downloads())
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    active_ids = {d.get("id") for d in active_downloads if d.get("id")}
    scheduled = _get_scheduled_downloads(exclude_ids=active_ids)

    current = None
    if active_downloads:
        top = active_downloads[0]
        current = {
            "id": top.get("id"),
            "title": top.get("title"),
            "site": top.get("site"),
            "status": top.get("status"),
            "progress": top.get("progress"),
            "speed": top.get("speed"),
            "size": top.get("size"),
        }

    return JsonResponse({
        "active": len(active_downloads),
        "queued": len(scheduled),
        "history": len(history),
        "current": current,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def kill_download(request: HttpRequest) -> JsonResponse:
    """API view to cancel a download."""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            download_id = data.get("download_id")
            if download_id:
                download_tracker.request_stop(download_id)
                return JsonResponse({"status": "success"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    return JsonResponse({"status": "error", "message": "Method not allowed", "status_code": 405}, status=405)


def kill_and_clear_queue(request: HttpRequest) -> JsonResponse:
    """API view to cancel a specific download and empty the entire scheduled queue."""
    if request.method == "POST":
        try:
            data = json.loads(request.body)

            # 1. Kill the active process if provided
            download_id = data.get("download_id")
            series_name = data.get("series_name")
            target_site = ""
            target_series = _extract_series_base_title(series_name)

            if download_id:
                # Resolve target site/title from current scheduled queue first.
                with scheduled_downloads_lock:
                    info = scheduled_downloads.get(download_id)
                if info:
                    target_site = str(info.get("site") or "").strip()
                    if not target_series:
                        target_series = _extract_series_base_title(info.get("title", ""))

                # Fallback to active downloads if needed.
                if not info:
                    active_items = download_tracker.get_active_downloads()
                    active_info = next((d for d in active_items if d.get("id") == download_id), None)
                    if active_info:
                        target_site = str(active_info.get("site") or "").strip()
                        if not target_series:
                            target_series = _extract_series_base_title(active_info.get("title", ""))

                _cancel_scheduled_download(download_id)
                download_tracker.request_stop(download_id)

            # 2. Stop other active downloads for the same series (same site + same series base).
            if target_series:
                active_to_stop = []
                for item in download_tracker.get_active_downloads():
                    current_id = item.get("id")
                    if not current_id:
                        continue
                    if target_site and str(item.get("site") or "").strip() != target_site:
                        continue
                    if _same_series(item.get("title", ""), target_series):
                        active_to_stop.append(current_id)

                for current_id in active_to_stop:
                    _cancel_scheduled_download(current_id)
                    download_tracker.request_stop(current_id)

            # 3. Clear queued items for the same series (same site + same series base).
            with scheduled_downloads_lock:
                to_remove = []
                for d_id, d_info in scheduled_downloads.items():
                    if not target_series:
                        continue
                    if target_site and str(d_info.get("site") or "").strip() != target_site:
                        continue
                    if _same_series(d_info.get("title", ""), target_series):
                        cancelled_scheduled_downloads.add(d_id)
                        to_remove.append(d_id)
                for d_id in to_remove:
                    scheduled_downloads.pop(d_id, None)

            return JsonResponse({"status": "success"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    return JsonResponse({"status": "error", "message": "Method not allowed", "status_code": 405}, status=405)


def clear_download_history(request: HttpRequest) -> JsonResponse:
    """API view to clear the download history."""
    if request.method == "POST":
        try:
            download_tracker.clear_history()
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


__all__ = ['get_downloads_json', 'get_downloads_summary', 'kill_download', 'kill_and_clear_queue', 'clear_download_history']
