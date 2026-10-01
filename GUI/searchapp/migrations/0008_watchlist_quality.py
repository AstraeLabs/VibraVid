from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("searchapp", "0007_merge_20260512_1803")]
    operations = [
        migrations.AddField("watchlistitem", "preferred_quality", models.CharField(max_length=5, blank=True, default="")),
        migrations.AddField("watchlistitem", "auto_all_seasons", models.BooleanField(default=False)),
        migrations.AddField("watchlistitem", "auto_completed", models.JSONField(default=dict, blank=True)),
        migrations.AddField("watchlistitem", "auto_status", models.CharField(max_length=255, blank=True, default="")),
    ]
