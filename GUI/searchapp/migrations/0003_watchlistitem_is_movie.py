# 2026.02.12

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("searchapp", "0002_watchlistitem_tmdb_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="watchlistitem",
            name="is_movie",
            field=models.BooleanField(default=False),
        ),
    ]
