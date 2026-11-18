from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("searchapp", "0005_arr_integration"),
    ]

    operations = [
        # ── ArrWebhookEvent extra fields ──────────────────────────────────
        migrations.AddField(
            model_name="arrwebhookevent",
            name="source",
            field=models.CharField(db_index=True, default="unknown", max_length=20),
        ),
        migrations.AddField(
            model_name="arrwebhookevent",
            name="media_type",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="arrwebhookevent",
            name="tmdb_id",
            field=models.CharField(blank=True, db_index=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="arrwebhookevent",
            name="arr_item_id",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="arrwebhookevent",
            name="ignored_by_priority",
            field=models.BooleanField(default=False),
        ),
        # ── ArrMediaRequest: add import_pending status ────────────────────
        migrations.AlterField(
            model_name="arrmediarequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("downloading", "Downloading"),
                    ("completed", "Completed"),
                    ("import_pending", "Import Pending"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped"),
                ],
                default="pending",
                max_length=15,
            ),
        ),
    ]
