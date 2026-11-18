# 20.01.26

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WatchlistItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("source_alias", models.CharField(max_length=100)),
                ("item_payload", models.TextField()),
                ("poster_url", models.URLField(blank=True, max_length=500, null=True)),
                ("num_seasons", models.IntegerField(default=0)),
                ("last_season_episodes", models.IntegerField(default=0)),
                ("added_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_checked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("has_new_seasons", models.BooleanField(default=False)),
                ("has_new_episodes", models.BooleanField(default=False)),
            ],
            options={
                "ordering": ["-added_at"],
                "unique_together": {("name", "source_alias")},
            },
        ),
    ]
