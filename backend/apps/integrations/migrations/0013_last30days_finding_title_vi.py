from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0012_last30days_progress_pct"),
    ]

    operations = [
        migrations.AddField(
            model_name="last30daysfinding",
            name="title_vi",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="last30daysfinding",
            name="title_vi_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("ok", "OK"),
                    ("skipped", "Skipped"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="last30daysfinding",
            name="title_vi_provider",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="last30daysfinding",
            name="title_vi_translated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="last30daysfinding",
            name="title_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="last30daysfinding",
            name="snippet_vi",
            field=models.TextField(blank=True),
        ),
    ]
