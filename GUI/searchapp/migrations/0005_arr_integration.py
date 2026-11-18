# Generated migration for ARR integration models

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("searchapp", "0004_watchlistitem_auto_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArrMediaRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("imdb_id", models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ("tmdb_id", models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ("tvdb_id", models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ("arr_id", models.IntegerField(help_text="Sonarr series ID or Radarr movie ID")),
                ("arr_source", models.CharField(help_text="sonarr or radarr", max_length=10)),
                ("title", models.CharField(max_length=500)),
                (
                    "content_type",
                    models.CharField(
                        choices=[("serie", "Serie"), ("movie", "Movie"), ("anime", "Anime")], max_length=10
                    ),
                ),
                ("season_number", models.IntegerField(blank=True, null=True)),
                ("episode_number", models.IntegerField(blank=True, null=True)),
                ("episode_id", models.IntegerField(blank=True, help_text="Sonarr episode ID", null=True)),
                ("year", models.IntegerField(blank=True, null=True)),
                ("provider", models.CharField(default="streamingcommunity", max_length=100)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("downloading", "Downloading"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        default="pending",
                        max_length=15,
                    ),
                ),
                (
                    "sync_source",
                    models.CharField(
                        choices=[("polling", "Polling"), ("webhook", "Webhook"), ("manual_resync", "Manual Resync")],
                        default="polling",
                        max_length=15,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_webhook_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ArrWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("MEDIA_APPROVED", "Media Approved"),
                            ("MEDIA_PENDING", "Media Pending"),
                            ("TEST_NOTIFICATION", "Test Notification"),
                            ("UNKNOWN", "Unknown"),
                        ],
                        default="UNKNOWN",
                        max_length=30,
                    ),
                ),
                ("raw_payload", models.JSONField()),
                ("processed", models.BooleanField(default=False)),
                ("error", models.TextField(blank=True, null=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-received_at"],
            },
        ),
        migrations.CreateModel(
            name="ArrProcessingQueue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("dedup_key", models.CharField(db_index=True, max_length=200, unique=True)),
                ("enqueued_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("success", models.BooleanField(null=True)),
                (
                    "media_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="queue_entries",
                        to="searchapp.arrmediarequest",
                    ),
                ),
            ],
            options={
                "ordering": ["-enqueued_at"],
            },
        ),
        migrations.AddIndex(
            model_name="arrmediarequest",
            index=models.Index(fields=["arr_source", "arr_id"], name="arr_mediarequest_source_id_idx"),
        ),
        migrations.AddIndex(
            model_name="arrmediarequest",
            index=models.Index(fields=["status"], name="arr_mediarequest_status_idx"),
        ),
        migrations.AddIndex(
            model_name="arrmediarequest",
            index=models.Index(fields=["content_type"], name="arr_mediarequest_content_idx"),
        ),
    ]
