# Generated manually for Phase 2 — NewsCrawler intel schema

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Tag",
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
                ("name", models.CharField(max_length=64, unique=True)),
                ("slug", models.SlugField(max_length=64, unique=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ThreatActor",
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
                ("name", models.CharField(max_length=255, unique=True)),
                ("aliases", models.JSONField(blank=True, default=list)),
                ("description", models.TextField(blank=True)),
                ("country", models.CharField(blank=True, max_length=64)),
                ("motivation", models.CharField(blank=True, max_length=128)),
                ("references", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name_plural": "threat actors",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Indicator",
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
                    "ioc_type",
                    models.CharField(
                        choices=[
                            ("ipv4", "IPv4"),
                            ("ipv6", "IPv6"),
                            ("domain", "Domain"),
                            ("url", "URL"),
                            ("email", "Email"),
                            ("md5", "MD5"),
                            ("sha1", "SHA1"),
                            ("sha256", "SHA256"),
                            ("cve", "CVE"),
                            ("filename", "Filename"),
                            ("mutex", "Mutex"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("value", models.CharField(db_index=True, max_length=2048)),
                (
                    "normalized_value",
                    models.CharField(blank=True, db_index=True, max_length=2048),
                ),
                ("description", models.TextField(blank=True)),
                (
                    "confidence",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("confirmed", "Confirmed"),
                        ],
                        db_index=True,
                        default="medium",
                        max_length=16,
                    ),
                ),
                (
                    "tlp",
                    models.CharField(
                        choices=[
                            ("clear", "TLP:CLEAR"),
                            ("green", "TLP:GREEN"),
                            ("amber", "TLP:AMBER"),
                            ("amber+strict", "TLP:AMBER+STRICT"),
                            ("red", "TLP:RED"),
                        ],
                        default="amber",
                        max_length=16,
                    ),
                ),
                ("source", models.CharField(blank=True, db_index=True, max_length=128)),
                ("source_url", models.URLField(blank=True, max_length=2048)),
                (
                    "first_seen",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                (
                    "last_seen",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "misp_attribute_uuid",
                    models.CharField(blank=True, db_index=True, max_length=64),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_indicators",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tags",
                    models.ManyToManyField(
                        blank=True, related_name="indicators", to="intel.tag"
                    ),
                ),
                (
                    "threat_actors",
                    models.ManyToManyField(
                        blank=True, related_name="indicators", to="intel.threatactor"
                    ),
                ),
            ],
            options={
                "ordering": ["-last_seen", "-id"],
            },
        ),
        migrations.CreateModel(
            name="Threat",
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
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        db_index=True,
                        default="medium",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("new", "New"),
                            ("triaged", "Triaged"),
                            ("confirmed", "Confirmed"),
                            ("false_positive", "False Positive"),
                            ("closed", "Closed"),
                        ],
                        db_index=True,
                        default="new",
                        max_length=32,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("x", "X / Twitter"),
                            ("telegram", "Telegram"),
                            ("cert", "CERT Feed"),
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
                ("source_url", models.URLField(blank=True, max_length=2048)),
                (
                    "published_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                (
                    "evidence_score",
                    models.DecimalField(
                        db_index=True, decimal_places=2, default=0, max_digits=5
                    ),
                ),
                (
                    "cvss_score",
                    models.DecimalField(
                        blank=True, decimal_places=1, max_digits=3, null=True
                    ),
                ),
                (
                    "epss_score",
                    models.DecimalField(
                        blank=True, decimal_places=4, max_digits=5, null=True
                    ),
                ),
                ("is_kev", models.BooleanField(db_index=True, default=False)),
                ("cve_ids", models.JSONField(blank=True, default=list)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_threats",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "indicators",
                    models.ManyToManyField(
                        blank=True, related_name="threats", to="intel.indicator"
                    ),
                ),
                (
                    "tags",
                    models.ManyToManyField(
                        blank=True, related_name="threats", to="intel.tag"
                    ),
                ),
                (
                    "threat_actors",
                    models.ManyToManyField(
                        blank=True, related_name="threats", to="intel.threatactor"
                    ),
                ),
            ],
            options={
                "ordering": ["-published_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="DataLeak",
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
                ("description", models.TextField(blank=True)),
                (
                    "leak_type",
                    models.CharField(
                        choices=[
                            ("credentials", "Credentials"),
                            ("stealer_log", "Stealer Log"),
                            ("source_code", "Source Code"),
                            ("api_key", "API Key / Secret"),
                            ("paste", "Paste Dump"),
                            ("breach_dump", "Breach Dump"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        db_index=True,
                        default="medium",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("new", "New"),
                            ("investigating", "Investigating"),
                            ("confirmed", "Confirmed"),
                            ("contained", "Contained"),
                            ("false_positive", "False Positive"),
                            ("closed", "Closed"),
                        ],
                        db_index=True,
                        default="new",
                        max_length=32,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("hudson_rock", "Hudson Rock"),
                            ("proxynova", "ProxyNova"),
                            ("breachdirectory", "BreachDirectory"),
                            ("pastebin", "Pastebin"),
                            ("github", "GitHub"),
                            ("gitlab", "GitLab"),
                            ("bitbucket", "Bitbucket"),
                            ("stackoverflow", "StackOverflow"),
                            ("npm", "npm Registry"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="manual",
                        max_length=32,
                    ),
                ),
                ("source_url", models.URLField(blank=True, max_length=2048)),
                (
                    "affected_organization",
                    models.CharField(blank=True, db_index=True, max_length=255),
                ),
                (
                    "affected_domain",
                    models.CharField(blank=True, db_index=True, max_length=255),
                ),
                (
                    "discovered_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("record_count", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_leaks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "related_indicators",
                    models.ManyToManyField(
                        blank=True,
                        related_name="related_leaks",
                        to="intel.indicator",
                    ),
                ),
                (
                    "tags",
                    models.ManyToManyField(
                        blank=True, related_name="leaks", to="intel.tag"
                    ),
                ),
            ],
            options={
                "verbose_name": "data leak",
                "verbose_name_plural": "data leaks",
                "ordering": ["-discovered_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="CompromisedCredential",
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
                ("email", models.EmailField(blank=True, db_index=True, max_length=254)),
                (
                    "username",
                    models.CharField(blank=True, db_index=True, max_length=255),
                ),
                ("password", models.CharField(blank=True, max_length=512)),
                (
                    "password_fingerprint",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="SHA-256 of password for correlation without revealing plaintext.",
                        max_length=64,
                    ),
                ),
                ("url", models.URLField(blank=True, max_length=2048)),
                (
                    "domain",
                    models.CharField(blank=True, db_index=True, max_length=255),
                ),
                (
                    "stealer_family",
                    models.CharField(
                        choices=[
                            ("redline", "RedLine"),
                            ("raccoon", "Raccoon"),
                            ("vidar", "Vidar"),
                            ("rastealer", "Raccoon Stealer / RaStealer"),
                            ("unknown", "Unknown"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="unknown",
                        max_length=32,
                    ),
                ),
                (
                    "infected_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("country", models.CharField(blank=True, max_length=64)),
                ("raw_line", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "leak",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credentials",
                        to="intel.dataleak",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="indicator",
            index=models.Index(
                fields=["ioc_type", "is_active"], name="intel_indic_ioc_typ_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="indicator",
            index=models.Index(
                fields=["source", "last_seen"], name="intel_indic_source_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="indicator",
            constraint=models.UniqueConstraint(
                fields=("ioc_type", "normalized_value"),
                name="uniq_indicator_type_normalized_value",
            ),
        ),
        migrations.AddIndex(
            model_name="threat",
            index=models.Index(
                fields=["severity", "status"], name="intel_threa_severit_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="threat",
            index=models.Index(
                fields=["source", "published_at"], name="intel_threa_source_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="dataleak",
            index=models.Index(
                fields=["leak_type", "severity"], name="intel_datal_leak_ty_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="dataleak",
            index=models.Index(
                fields=["affected_domain", "discovered_at"],
                name="intel_datal_affecte_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="compromisedcredential",
            index=models.Index(
                fields=["domain", "email"], name="intel_compr_domain_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="compromisedcredential",
            index=models.Index(
                fields=["stealer_family", "infected_at"],
                name="intel_compr_stealer_idx",
            ),
        ),
    ]
