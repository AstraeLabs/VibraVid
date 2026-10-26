# 09.10.26

import json
import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import runner
from .models import ScheduledDownload

logger = logging.getLogger(__name__)

Status = ScheduledDownload.Status

# Only what the download forms send; anything else in the request is dropped.
_FORM_FIELDS = (
    "source_alias", "item_payload", "quality", "audio_format", "audio_language", "season", "episode",
    "download_type", "season_number", "selected_episodes", "selected_seasons",
)
_MAX_AHEAD = timedelta(days=366)
_PAST_TOLERANCE = timedelta(minutes=1)


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"status": "error", "message": message}, status=status)


def _parse_run_at(raw) -> datetime | None:
    """`run_at` travels as epoch seconds: the browser owns the user's clock and time zone."""
    try:
        run_at = datetime.fromtimestamp(float(raw), tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    now = timezone.now()
    if run_at < now - _PAST_TOLERANCE or run_at > now + _MAX_AHEAD:
        return None
    return run_at


def _summary(params: dict) -> str:
    kind = params.get("download_type")
    season = params.get("season_number") or params.get("season")
    if kind == "full_series":
        return "Whole series"
    if kind == "selected_seasons":
        return "Seasons " + ", ".join(s.strip() for s in params.get("selected_seasons", "").split(",") if s.strip())
    if kind == "full_season":
        return f"Season {season}"
    if kind:
        return f"S{season} · E{params.get('selected_episodes', '')}"
    if season:
        return f"S{season}" + (f" · E{params['episode']}" if params.get("episode") else "")
    return ""


def _row(job: ScheduledDownload) -> dict:
    return {
        "id": job.pk,
        "title": job.title,
        "site": job.source_alias,
        "summary": job.summary,
        "poster": job.poster,
        "run_at": job.run_at.timestamp(),
        "status": job.status,
        "note": job.note,
    }


@require_POST
def schedule_add(request: HttpRequest) -> JsonResponse:
    """Store a download form for later instead of starting it."""
    params = {k: request.POST[k].strip() for k in _FORM_FIELDS if request.POST.get(k, "").strip()}

    try:
        item = json.loads(params.get("item_payload", ""))
    except ValueError:
        item = None
    if not params.get("source_alias") or not isinstance(item, dict) or not item.get("name"):
        return _error("Missing title data.")

    kind = params.get("download_type")
    if kind == "selected_seasons" and not params.get("selected_seasons"):
        return _error("Select at least one season.")
    if kind in {"full_season", "episodes"} and not params.get("season_number"):
        return _error("Missing season number.")
    if kind == "episodes" and not params.get("selected_episodes"):
        return _error("Select at least one episode.")

    run_at = _parse_run_at(request.POST.get("run_at"))
    if run_at is None:
        return _error("Pick a time in the future.")

    job = ScheduledDownload.objects.create(
        title=str(item["name"])[:500],
        source_alias=params["source_alias"][:100],
        summary=_summary(params)[:255],
        poster=str(item.get("poster") or ""),
        params=params,
        run_at=run_at,
    )
    runner.wake()
    logger.info("[Schedule] '%s' scheduled for %s", job.title, job.run_at)
    return JsonResponse({"status": "success", "item": _row(job)})


@require_GET
def schedule_list(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"items": [_row(job) for job in ScheduledDownload.objects.exclude(status=Status.STARTING)]})


@require_POST
def schedule_update(request: HttpRequest) -> JsonResponse:
    """Remove a job, move it to another time, or start it now."""
    action = request.POST.get("action")
    job_id = str(request.POST.get("id") or "")
    job = ScheduledDownload.objects.filter(pk=job_id).exclude(status=Status.STARTING).first() if job_id.isdigit() else None
    if job is None:
        return _error("Scheduled download not found.", status=404)

    if action == "remove":
        job.delete()
        return JsonResponse({"status": "success"})

    if action == "run":
        run_at = timezone.now()
    elif action == "move":
        run_at = _parse_run_at(request.POST.get("run_at"))
        if run_at is None:
            return _error("Pick a time in the future.")
    else:
        return _error("Unknown action.")

    job.run_at, job.status, job.note = run_at, Status.PENDING, ""
    job.save(update_fields=["run_at", "status", "note"])
    runner.wake()
    return JsonResponse({"status": "success", "item": _row(job)})
