from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AIBriefing(TimeStampedModel):
    class Provider(models.TextChoices):
        GROQ = "groq", "Groq"
        OLLAMA = "ollama", "Ollama"
        WIGOLO = "wigolo", "Wigolo"
        ANTHROPIC = "anthropic", "Anthropic"
        HUGGINGFACE = "huggingface", "Hugging Face"
        LOCAL = "local", "Local template"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=512)
    content = models.TextField(blank=True)
    provider = models.CharField(
        max_length=32, choices=Provider.choices, default=Provider.LOCAL
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    window_hours = models.PositiveIntegerField(default=24)
    threat_count = models.PositiveIntegerField(default=0)
    indicator_count = models.PositiveIntegerField(default=0)
    leak_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    progress = models.CharField(max_length=255, blank=True, default="")
    progress_pct = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_briefings",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class IntegrationSyncLog(TimeStampedModel):
    class Direction(models.TextChoices):
        EXPORT = "export", "Export"
        IMPORT = "import", "Import"

    class Target(models.TextChoices):
        MISP = "misp", "MISP"
        THEHIVE = "thehive", "TheHive"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    target = models.CharField(max_length=32, choices=Target.choices, db_index=True)
    direction = models.CharField(max_length=16, choices=Direction.choices)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    message = models.TextField(blank=True)
    records_processed = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.target}:{self.direction}:{self.status}"


class GitHubScan(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    keyword = models.CharField(max_length=256, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    max_results = models.PositiveSmallIntegerField(default=1500)
    repository_count = models.PositiveIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    alert_count = models.PositiveIntegerField(default=0)
    critical_count = models.PositiveIntegerField(default=0)
    non_text_count = models.PositiveIntegerField(default=0)
    api_requests = models.PositiveIntegerField(default=0)
    rate_limit_remaining = models.IntegerField(null=True, blank=True)
    coverage_limited = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    active_slot = models.BooleanField(null=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="github_scans",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["active_slot"],
                condition=models.Q(active_slot=True),
                name="uniq_active_github_scan",
            )
        ]

    def __str__(self) -> str:
        return f"{self.keyword}:{self.status}"

    def save(self, *args, **kwargs):
        self.active_slot = (
            True
            if self.status in {self.Status.QUEUED, self.Status.RUNNING}
            else None
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"active_slot"}
        return super().save(*args, **kwargs)


class GitHubFinding(TimeStampedModel):
    class Severity(models.TextChoices):
        INFO = "info", "Info"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    scan = models.ForeignKey(
        GitHubScan, on_delete=models.CASCADE, related_name="findings"
    )
    repository = models.CharField(max_length=512, db_index=True)
    owner = models.CharField(max_length=255, blank=True, db_index=True)
    file_path = models.CharField(max_length=1024)
    extension = models.CharField(max_length=32, blank=True, db_index=True)
    html_url = models.URLField(max_length=2048)
    repository_url = models.URLField(max_length=2048, blank=True)
    is_text_file = models.BooleanField(default=False, db_index=True)
    keyword_matches = models.PositiveIntegerField(default=1)
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.INFO, db_index=True
    )
    alert_types = models.JSONField(default=list, blank=True)
    # Exact in-repo line text for detected leaks (staff-only API).
    evidence = models.TextField(blank=True)
    # Absolute 1-based line numbers where the keyword / secret appears (capped).
    match_lines = models.JSONField(default=list, blank=True)
    # Keyword hit snippets: [{"line": 12, "text": "..."}, ...] (line may be null).
    match_snippets = models.JSONField(default=list, blank=True)
    score = models.IntegerField(default=0, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        # Non-.txt first within a scan/repo, then by relevance score.
        ordering = ["is_text_file", "-score", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["scan", "repository", "file_path"],
                name="uniq_github_scan_repository_file",
            )
        ]
        indexes = [
            models.Index(fields=["scan", "-score"]),
            models.Index(fields=["scan", "severity"]),
            models.Index(fields=["scan", "repository", "is_text_file", "-score"]),
        ]

    def __str__(self) -> str:
        return f"{self.repository}:{self.file_path}"


class Last30DaysResearch(TimeStampedModel):
    """Multi-source social/web research for a topic over ~30 days."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    class Depth(models.TextChoices):
        QUICK = "quick", "Quick"
        DEFAULT = "default", "Default"
        DEEP = "deep", "Deep"

    topic = models.CharField(max_length=512, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    depth = models.CharField(
        max_length=16, choices=Depth.choices, default=Depth.QUICK
    )
    lookback_days = models.PositiveSmallIntegerField(default=30)
    max_results = models.PositiveSmallIntegerField(default=40)
    sources = models.JSONField(default=list, blank=True)
    source_counts = models.JSONField(default=dict, blank=True)
    item_count = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    # Human-readable progress while RUNNING, e.g. "hackernews (2/4)".
    progress = models.CharField(max_length=255, blank=True)
    progress_pct = models.PositiveSmallIntegerField(default=0)
    clusters = models.JSONField(default=list, blank=True)
    errors_by_source = models.JSONField(default=dict, blank=True)
    brief_markdown = models.TextField(blank=True)
    raw_report = models.JSONField(default=dict, blank=True)
    stderr_tail = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    active_slot = models.BooleanField(null=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="last30days_researches",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Last 30 days research"
        verbose_name_plural = "Last 30 days researches"
        constraints = [
            models.UniqueConstraint(
                fields=["active_slot"],
                condition=models.Q(active_slot=True),
                name="uniq_active_last30days_research",
            )
        ]

    def __str__(self) -> str:
        return f"{self.topic}:{self.status}"

    def save(self, *args, **kwargs):
        self.active_slot = (
            True
            if self.status in {self.Status.QUEUED, self.Status.RUNNING}
            else None
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"active_slot"}
        return super().save(*args, **kwargs)


class Last30DaysFinding(TimeStampedModel):
    research = models.ForeignKey(
        Last30DaysResearch, on_delete=models.CASCADE, related_name="findings"
    )
    source = models.CharField(max_length=64, db_index=True)
    title = models.CharField(max_length=512)
    title_vi = models.CharField(max_length=512, blank=True, default="")
    title_vi_status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("ok", "OK"),
            ("skipped", "Skipped"),
            ("failed", "Failed"),
        ],
        default="pending",
        db_index=True,
    )
    title_vi_provider = models.CharField(max_length=64, blank=True, default="")
    title_vi_translated_at = models.DateTimeField(null=True, blank=True)
    title_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    url = models.URLField(max_length=2048, blank=True)
    host = models.CharField(max_length=255, blank=True, db_index=True)
    author = models.CharField(max_length=255, blank=True)
    snippet = models.TextField(blank=True)
    snippet_vi = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    score = models.FloatField(default=0, db_index=True)
    engagement_score = models.FloatField(default=0)
    relevance = models.FloatField(default=0)
    freshness = models.FloatField(default=0)
    cluster_id = models.CharField(max_length=64, blank=True, db_index=True)
    cluster_title = models.CharField(max_length=512, blank=True)
    engagement = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-score", "-id"]
        indexes = [
            models.Index(fields=["research", "-score"]),
            models.Index(fields=["research", "source"]),
        ]

    def __str__(self) -> str:
        return f"{self.source}:{self.title[:40]}"
