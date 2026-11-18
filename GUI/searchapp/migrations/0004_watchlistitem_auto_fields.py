# 2026.02.12

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("searchapp", "0003_watchlistitem_is_movie"),
    ]

    operations = [
        migrations.AddField(
            model_name="watchlistitem",
            name="auto_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="watchlistitem",
            name="auto_season",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="watchlistitem",
            name="auto_last_episode_count",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="watchlistitem",
            name="auto_last_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="watchlistitem",
            name="auto_last_downloaded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
