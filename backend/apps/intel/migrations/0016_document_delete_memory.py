# Generated manually for document delete / keyword delete memory

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intel", "0015_scanneddocument_title_vi"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeletedDocumentScanKeyword",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("keyword", models.CharField(db_index=True, max_length=255)),
                ("filetypes", models.CharField(default="pdf", max_length=64)),
                ("name", models.CharField(blank=True, default="", max_length=128)),
            ],
            options={
                "verbose_name": "deleted document scan keyword",
                "verbose_name_plural": "deleted document scan keywords",
            },
        ),
        migrations.CreateModel(
            name="BlockedScannedDocumentUrl",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_url", models.URLField(max_length=2048, unique=True)),
                ("title", models.CharField(blank=True, default="", max_length=512)),
                (
                    "reason",
                    models.CharField(blank=True, default="user_deleted", max_length=64),
                ),
            ],
            options={
                "verbose_name": "blocked scanned document URL",
                "verbose_name_plural": "blocked scanned document URLs",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="deleteddocumentscankeyword",
            constraint=models.UniqueConstraint(
                fields=("keyword", "filetypes"),
                name="uniq_deleted_document_scan_keyword_filetypes",
            ),
        ),
    ]
