from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("searchapp", "0008_watchlist_quality")]
    operations = [
        migrations.AddField(
            "arrprocessingqueue",
            "attempts",
            models.PositiveIntegerField(default=0, help_text="Consecutive failed attempts; polling stops retrying at ARR.max_retry_attempts"),
        ),
    ]
