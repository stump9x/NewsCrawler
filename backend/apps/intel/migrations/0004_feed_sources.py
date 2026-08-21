# Generated manually for FeedSource + Threat.Source.NEWS

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0003_watch_rules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="threat",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("x", "X / Twitter"),
                    ("telegram", "Telegram"),
                    ("cert", "CERT Feed"),
                    ("news", "News / RSS"),
                    ("ransomware", "Ransomware Blog"),
                    ("cve_feed", "CVE Feed"),
                    ("osint", "OSINT"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="manual",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="FeedSource",
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
                ("url", models.URLField(max_length=2048, unique=True)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("cert", "CERT / Advisory"),
                            ("breach", "Data Breach"),
                            ("news", "Security News"),
                            ("ransomware", "Ransomware"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="news",
                        max_length=32,
                    ),
                ),
                (
                    "confidence",
                    models.PositiveSmallIntegerField(
                        db_index=True,
                        default=2,
                        help_text="Watcher-style confidence 1 (high trust) … 5 (noisy)",
                    ),
                ),
                ("country", models.CharField(blank=True, max_length=64)),
                ("country_code", models.CharField(blank=True, max_length=8)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("notes", models.TextField(blank=True)),
                ("last_fetched_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_status",
                    models.CharField(blank=True, db_index=True, max_length=16),
                ),
                ("last_error", models.TextField(blank=True)),
                ("last_item_count", models.PositiveIntegerField(default=0)),
            ],
            options={
                "verbose_name": "feed source",
                "verbose_name_plural": "feed sources",
                "ordering": ["confidence", "name"],
            },
        ),
    ]
