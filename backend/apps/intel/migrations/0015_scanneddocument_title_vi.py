# Generated manually for ScannedDocument Vietnamese titles

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intel", "0014_document_scan"),
    ]

    operations = [
        migrations.AddField(
            model_name="scanneddocument",
            name="title_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="scanneddocument",
            name="title_vi",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="scanneddocument",
            name="title_vi_provider",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="scanneddocument",
            name="title_vi_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("ok", "OK"),
                    ("rule", "Rule"),
                    ("skipped", "Skipped"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="scanneddocument",
            name="title_vi_translated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
