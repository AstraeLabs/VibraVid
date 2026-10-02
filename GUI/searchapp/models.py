# 06.06.25

from django.db import models
from django.utils import timezone


class WatchlistItem(models.Model):
    name = models.CharField(max_length=255)
    source_alias = models.CharField(max_length=100)
    item_payload = models.TextField()
    is_movie = models.BooleanField(default=False)
    poster_url = models.URLField(max_length=500, null=True, blank=True)
    tmdb_id = models.CharField(max_length=50, null=True, blank=True)
    num_seasons = models.IntegerField(default=0)
    last_season_episodes = models.IntegerField(default=0)

    preferred_quality = models.CharField(max_length=5, blank=True, default="")
    auto_all_seasons = models.BooleanField(default=False)
    auto_completed = models.JSONField(default=dict, blank=True)
    auto_status = models.CharField(max_length=255, blank=True, default="")
    auto_enabled = models.BooleanField(default=False)
    auto_season = models.IntegerField(null=True, blank=True)
    auto_last_episode_count = models.IntegerField(default=0)
    auto_last_checked_at = models.DateTimeField(null=True, blank=True)
    auto_last_downloaded_at = models.DateTimeField(null=True, blank=True)

    # Metadata for tracking changes
    added_at = models.DateTimeField(default=timezone.now)
    last_checked_at = models.DateTimeField(default=timezone.now)

    # Flags to indicate new content
    has_new_seasons = models.BooleanField(default=False)
    has_new_episodes = models.BooleanField(default=False)

    class Meta:
        ordering = ["-added_at"]
        unique_together = ("name", "source_alias")

    def __str__(self):
        return f"{self.name} ({self.source_alias})"


# ─────────────────────────────────────────────────────
# ARR Integration Models
# ─────────────────────────────────────────────────────


class ArrMediaRequest(models.Model):
    """Tracks media items synced from Sonarr/Radarr."""

    class ContentType(models.TextChoices):
        SERIE = "serie", "Serie"
        MOVIE = "movie", "Movie"
        ANIME = "anime", "Anime"

    class SyncSource(models.TextChoices):
        POLLING = "polling", "Polling"
        WEBHOOK = "webhook", "Webhook"
        MANUAL_RESYNC = "manual_resync", "Manual Resync"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DOWNLOADING = "downloading", "Downloading"
        COMPLETED = "completed", "Completed"
        IMPORT_PENDING = "import_pending", "Import Pending"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    # External IDs
    imdb_id = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    tmdb_id = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    tvdb_id = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    arr_id = models.IntegerField(help_text="Sonarr series ID or Radarr movie ID")
    arr_source = models.CharField(max_length=10, help_text="sonarr or radarr")

    # Media info
    title = models.CharField(max_length=500)
    content_type = models.CharField(max_length=10, choices=ContentType.choices)
    season_number = models.IntegerField(null=True, blank=True)
    episode_number = models.IntegerField(null=True, blank=True)
    episode_id = models.IntegerField(null=True, blank=True, help_text="Sonarr episode ID")
    year = models.IntegerField(null=True, blank=True)
    provider = models.CharField(max_length=100, default="streamingcommunity")

    # State
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    sync_source = models.CharField(max_length=15, choices=SyncSource.choices, default=SyncSource.POLLING)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_webhook_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["arr_source", "arr_id"], name="arr_mediarequest_source_id_idx"),
            models.Index(fields=["status"], name="arr_mediarequest_status_idx"),
            models.Index(fields=["content_type"], name="arr_mediarequest_content_idx"),
        ]

    def __str__(self):
        ep = f" S{self.season_number}E{self.episode_number}" if self.season_number else ""
        return f"[{self.arr_source}] {self.title}{ep} ({self.status})"


class ArrWebhookEvent(models.Model):
    """Audit log for incoming Seerr/Overseerr webhooks."""

    class EventType(models.TextChoices):
        MEDIA_APPROVED = "MEDIA_APPROVED", "Media Approved"
        MEDIA_PENDING = "MEDIA_PENDING", "Media Pending"
        TEST_NOTIFICATION = "TEST_NOTIFICATION", "Test Notification"
        UNKNOWN = "UNKNOWN", "Unknown"

    event_type = models.CharField(max_length=30, choices=EventType.choices, default=EventType.UNKNOWN)
    source = models.CharField(max_length=20, default="unknown", db_index=True)
    media_type = models.CharField(max_length=20, null=True, blank=True)
    tmdb_id = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    arr_item_id = models.IntegerField(null=True, blank=True)
    ignored_by_priority = models.BooleanField(default=False)
    raw_payload = models.JSONField()
    processed = models.BooleanField(default=False)
    error = models.TextField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"Webhook {self.event_type} @ {self.received_at}"


class ArrProcessingQueue(models.Model):
    """Deduplication table — ensures the same media item is not enqueued twice."""

    # Unique key built from arr_source + arr_id + season + episode
    dedup_key = models.CharField(max_length=200, unique=True, db_index=True)
    media_request = models.ForeignKey(ArrMediaRequest, on_delete=models.CASCADE, related_name="queue_entries")

    enqueued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(null=True)

    class Meta:
        ordering = ["-enqueued_at"]

    def __str__(self):
        return f"Queue [{self.dedup_key}] — {'done' if self.completed_at else 'pending'}"


class DownloadHistory(models.Model):
    download_id = models.CharField(max_length=128, db_index=True)
    payload = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.download_id} @ {self.created_at}"
