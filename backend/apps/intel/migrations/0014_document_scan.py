# Generated manually for document scan feature

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intel", "0013_threat_title_vi"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentScanKeyword",
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
                ("name", models.CharField(max_length=128)),
                ("keyword", models.CharField(db_index=True, max_length=255)),
                (
                    "filetypes",
                    models.CharField(
                        default="pdf",
                        help_text="Comma-separated extensions used with filetype: (default pdf).",
                        max_length=64,
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "priority",
                    models.PositiveSmallIntegerField(
                        db_index=True,
                        default=50,
                        help_text="Higher priority keywords are scanned first.",
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("last_scanned_at", models.DateTimeField(blank=True, null=True)),
                ("last_hit_count", models.PositiveIntegerField(default=0)),
            ],
            options={
                "verbose_name": "document scan keyword",
                "verbose_name_plural": "document scan keywords",
                "ordering": ["-priority", "name"],
            },
        ),
        migrations.CreateModel(
            name="ScannedDocument",
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
                ("title", models.CharField(max_length=512)),
                ("summary", models.TextField(blank=True)),
                ("source_url", models.URLField(max_length=2048, unique=True)),
                (
                    "file_path",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Display path / URL path for notifications (e.g. /journals/.../file.pdf).",
                        max_length=1024,
                    ),
                ),
                ("host", models.CharField(blank=True, db_index=True, max_length=255)),
                (
                    "filetype",
                    models.CharField(db_index=True, default="pdf", max_length=16),
                ),
                (
                    "matched_keyword",
                    models.CharField(blank=True, db_index=True, max_length=255),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("google", "Google"),
                            ("bing", "Bing"),
                            ("brave", "Brave"),
                            ("duckduckgo", "DuckDuckGo"),
                            ("searx", "SearxNG"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="searx",
                        max_length=32,
                    ),
                ),
                ("engine", models.CharField(blank=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("new", "New"),
                            ("reviewed", "Reviewed"),
                            ("archived", "Archived"),
                            ("false_positive", "False Positive"),
                        ],
                        db_index=True,
                        default="new",
                        max_length=32,
                    ),
                ),
                (
                    "importance_score",
                    models.PositiveSmallIntegerField(db_index=True, default=0),
                ),
                ("is_important", models.BooleanField(db_index=True, default=True)),
                ("published_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "discovered_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "keyword",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="documents",
                        to="intel.documentscankeyword",
                    ),
                ),
            ],
            options={
                "verbose_name": "scanned document",
                "verbose_name_plural": "scanned documents",
                "ordering": ["-discovered_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="alertnotification",
            name="document",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="alerts",
                to="intel.scanneddocument",
            ),
        ),
        migrations.AddIndex(
            model_name="scanneddocument",
            index=models.Index(
                fields=["is_important", "discovered_at"],
                name="intel_scann_is_impo_6f3a1c_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="scanneddocument",
            index=models.Index(
                fields=["filetype", "discovered_at"],
                name="intel_scann_filetyp_9b2e4d_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentscankeyword",
            constraint=models.UniqueConstraint(
                fields=("keyword", "filetypes"),
                name="uniq_document_scan_keyword_filetypes",
            ),
        ),
    ]
