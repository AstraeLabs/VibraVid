# 09.10.26

from django.db import models
from django.utils import timezone


class ScheduledDownload(models.Model):
    """A download request kept aside until `run_at`, then handed to the normal queue."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        STARTING = "starting", "Starting"
        MISSED = "missed", "Missed"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=500)
    source_alias = models.CharField(max_length=100)
    summary = models.CharField(max_length=255, blank=True, default="")
    poster = models.TextField(blank=True, default="")

    # The fields of the download form, replayed unchanged when the time comes.
    params = models.JSONField(default=dict)

    run_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["run_at", "id"]

    def __str__(self):
        return f"{self.title} @ {self.run_at} ({self.status})"
