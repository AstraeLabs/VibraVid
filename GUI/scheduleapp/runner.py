# 09.10.26

import logging
import threading
from datetime import timedelta

from django.contrib import messages
from django.db import close_old_connections
from django.http import HttpRequest, QueryDict
from django.utils import timezone

from .models import ScheduledDownload

logger = logging.getLogger(__name__)

# A job found overdue by more than this (the app was down at its time) is not
# started on its own: it is kept as "missed" and the user decides.
GRACE_SECONDS = 3600
_MAX_WAIT_SECONDS = 30

_loop_started = False
_loop_lock = threading.Lock()
_wake = threading.Event()

Status = ScheduledDownload.Status


class _Notes:
    """Stands in for the messages storage of a request nobody will render."""

    def __init__(self):
        self.errors: list[str] = []

    def add(self, level, message, extra_tags=""):
        if level >= messages.ERROR:
            self.errors.append(str(message))


def wake() -> None:
    """Make the loop look at the table again now (a job was added or moved)."""
    _wake.set()


def _dispatch(job: ScheduledDownload) -> str:
    """Replay the stored form through the same views a click goes through. Returns the error, if any."""
    from searchapp import views

    request = HttpRequest()
    request.method = "POST"
    post = QueryDict(mutable=True)
    for key, value in (job.params or {}).items():
        post[key] = "" if value is None else str(value)
    request.POST = post
    request._messages = notes = _Notes()

    if post.get("download_type"):
        views._handle_series_download(request)
    else:
        views.start_download(request)
    return "; ".join(notes.errors)


def run_due(now=None) -> int:
    """Start every pending job whose time has come. Returns how many were handed to the queue."""
    now = now or timezone.now()
    started = 0
    for job in ScheduledDownload.objects.filter(status=Status.PENDING, run_at__lte=now):
        # Claim the row first: a second caller must not start the same job.
        if not ScheduledDownload.objects.filter(pk=job.pk, status=Status.PENDING).update(status=Status.STARTING):
            continue

        if now - job.run_at > timedelta(seconds=GRACE_SECONDS):
            ScheduledDownload.objects.filter(pk=job.pk).update(
                status=Status.MISSED, note="The app was not running at the scheduled time.")
            logger.warning("[Schedule] Missed '%s' (due %s)", job.title, job.run_at)
            continue

        try:
            error = _dispatch(job)
        except Exception as exc:
            logger.exception("[Schedule] Could not start '%s'", job.title)
            error = str(exc) or exc.__class__.__name__

        if error:
            ScheduledDownload.objects.filter(pk=job.pk).update(status=Status.FAILED, note=error[:2000])
            logger.warning("[Schedule] '%s' not started: %s", job.title, error)
        else:
            # From here on the job lives in the download queue like any other.
            ScheduledDownload.objects.filter(pk=job.pk).delete()
            logger.info("[Schedule] Started '%s'", job.title)
            started += 1
    return started


def _seconds_to_next(now) -> float:
    next_run = (ScheduledDownload.objects.filter(status=Status.PENDING)
                .order_by("run_at").values_list("run_at", flat=True).first())
    if next_run is None:
        return _MAX_WAIT_SECONDS
    return max(1.0, min(_MAX_WAIT_SECONDS, (next_run - now).total_seconds()))


def _loop() -> None:
    try:
        close_old_connections()
        # Claimed but never handed off: the process died in between.
        ScheduledDownload.objects.filter(status=Status.STARTING).update(
            status=Status.MISSED, note="Interrupted by a restart.")
    except Exception:
        logger.exception("[Schedule] Startup check failed")

    while True:
        delay = _MAX_WAIT_SECONDS
        try:
            close_old_connections()
            run_due()
            delay = _seconds_to_next(timezone.now())
        except Exception:
            logger.exception("[Schedule] Loop error")
        finally:
            close_old_connections()
        _wake.wait(delay)
        _wake.clear()


def start_schedule_loop() -> None:
    global _loop_started
    with _loop_lock:
        if _loop_started:
            return
        _loop_started = True
    threading.Thread(target=_loop, daemon=True, name="ScheduleLoop").start()
