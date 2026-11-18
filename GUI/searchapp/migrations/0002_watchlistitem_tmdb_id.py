# 2026.02.12

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("searchapp", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="watchlistitem",
            name="tmdb_id",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
