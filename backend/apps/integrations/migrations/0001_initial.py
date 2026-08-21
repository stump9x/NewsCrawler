# Generated for Phase 6 integrations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AIBriefing",
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
                ("content", models.TextField(blank=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("anthropic", "Anthropic"),
                            ("huggingface", "Hugging Face"),
                            ("local", "Local template"),
                        ],
                        default="local",
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("window_hours", models.PositiveIntegerField(default=24)),
                ("threat_count", models.PositiveIntegerField(default=0)),
                ("indicator_count", models.PositiveIntegerField(default=0)),
                ("leak_count", models.PositiveIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("raw_response", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ai_briefings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="IntegrationSyncLog",
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
                (
                    "target",
                    models.CharField(
                        choices=[("misp", "MISP"), ("thehive", "TheHive")],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "direction",
                    models.CharField(
                        choices=[("export", "Export"), ("import", "Import")],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("success", "Success"),
                            ("partial", "Partial"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("message", models.TextField(blank=True)),
                ("records_processed", models.PositiveIntegerField(default=0)),
                ("details", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
