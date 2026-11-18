# 2026.05.08

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("searchapp", "0004_watchlistitem_auto_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="DownloadHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("download_id", models.CharField(db_index=True, max_length=128)),
                ("payload", models.TextField()),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
