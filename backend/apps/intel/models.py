"""
Core CTI domain models for NewsCrawler.

Phase 2 focuses on indicators (IOCs), threat items (The Wire),
data leaks, and compromised credential records from stealer logs.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Tag(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=64, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ThreatActor(TimeStampedModel):
    name = models.CharField(max_length=255, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    country = models.CharField(max_length=64, blank=True)
    motivation = models.CharField(max_length=128, blank=True)
    references = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "threat actors"

    def __str__(self) -> str:
        return self.name


class Indicator(TimeStampedModel):
    """Indicator of Compromise (IOC)."""

    class Type(models.TextChoices):
        IPV4 = "ipv4", "IPv4"
        IPV6 = "ipv6", "IPv6"
        DOMAIN = "domain", "Domain"
        URL = "url", "URL"
        EMAIL = "email", "Email"
        MD5 = "md5", "MD5"
        SHA1 = "sha1", "SHA1"
        SHA256 = "sha256", "SHA256"
        CVE = "cve", "CVE"
        FILENAME = "filename", "Filename"
        MUTEX = "mutex", "Mutex"
        OTHER = "other", "Other"

    class Confidence(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CONFIRMED = "confirmed", "Confirmed"

    class TLP(models.TextChoices):
        CLEAR = "clear", "TLP:CLEAR"
        GREEN = "green", "TLP:GREEN"
        AMBER = "amber", "TLP:AMBER"
        AMBER_STRICT = "amber+strict", "TLP:AMBER+STRICT"
        RED = "red", "TLP:RED"

    ioc_type = models.CharField(max_length=32, choices=Type.choices, db_index=True)
    value = models.CharField(max_length=2048, db_index=True)
    normalized_value = models.CharField(max_length=2048, db_index=True, blank=True)
    description = models.TextField(blank=True)
    confidence = models.CharField(
        max_length=16,
        choices=Confidence.choices,
        default=Confidence.MEDIUM,
        db_index=True,
    )
    tlp = models.CharField(max_length=16, choices=TLP.choices, default=TLP.AMBER)
    source = models.CharField(max_length=128, blank=True, db_index=True)
    source_url = models.URLField(max_length=2048, blank=True)
    first_seen = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="indicators")
    threat_actors = models.ManyToManyField(
        ThreatActor, blank=True, related_name="indicators"
    )
    # Reserved for Phase 6 MISP sync
    misp_attribute_uuid = models.CharField(max_length=64, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_indicators",
    )

    class Meta:
        ordering = ["-last_seen", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["ioc_type", "normalized_value"],
                name="uniq_indicator_type_normalized_value",
            )
        ]
        indexes = [
            models.Index(fields=["ioc_type", "is_active"]),
            models.Index(fields=["source", "last_seen"]),
        ]

    def __str__(self) -> str:
        return f"{self.ioc_type}:{self.value}"

    def save(self, *args, **kwargs):
        self.normalized_value = self.normalize(self.ioc_type, self.value)
        if self.last_seen and self.first_seen and self.last_seen < self.first_seen:
            self.last_seen = self.first_seen
        super().save(*args, **kwargs)

    @staticmethod
    def normalize(ioc_type: str, value: str) -> str:
        cleaned = (value or "").strip()
        if ioc_type in {
            Indicator.Type.DOMAIN,
            Indicator.Type.EMAIL,
            Indicator.Type.URL,
            Indicator.Type.CVE,
        }:
            return cleaned.lower()
        if ioc_type in {Indicator.Type.MD5, Indicator.Type.SHA1, Indicator.Type.SHA256}:
            return cleaned.lower()
        return cleaned


class Threat(TimeStampedModel):
    """Intelligence item for The Wire / situational awareness."""

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        NEW = "new", "New"
        TRIAGED = "triaged", "Triaged"
        CONFIRMED = "confirmed", "Confirmed"
        FALSE_POSITIVE = "false_positive", "False Positive"
        CLOSED = "closed", "Closed"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        X = "x", "X / Twitter"
        TELEGRAM = "telegram", "Telegram"
        CERT = "cert", "CERT Feed"
        NEWS = "news", "News / RSS"
        RANSOMWARE = "ransomware", "Ransomware Blog"
        CVE_FEED = "cve_feed", "CVE Feed"
        OSINT = "osint", "OSINT"
        OTHER = "other", "Other"

    class TitleViStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "OK"
        RULE = "rule", "Rule"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=512)
    title_vi = models.CharField(max_length=512, blank=True, default="")
    title_vi_status = models.CharField(
        max_length=16,
        choices=TitleViStatus.choices,
        default=TitleViStatus.PENDING,
        db_index=True,
    )
    title_vi_provider = models.CharField(max_length=64, blank=True, default="")
    title_vi_translated_at = models.DateTimeField(null=True, blank=True)
    title_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    summary = models.TextField(blank=True)
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.MANUAL,
        db_index=True,
    )
    source_url = models.URLField(max_length=2048, blank=True)
    published_at = models.DateTimeField(default=timezone.now, db_index=True)
    wire_priority = models.PositiveSmallIntegerField(
        default=0,
        db_index=True,
        help_text="Higher values pin items to the top of The Wire (e.g. Vietnam-related).",
    )
    wire_relevant = models.BooleanField(
        default=True,
        db_index=True,
        help_text="False hides off-topic RSS content from The Wire.",
    )
    evidence_score = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, db_index=True
    )
    cvss_score = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True
    )
    epss_score = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    is_kev = models.BooleanField(default=False, db_index=True)
    cve_ids = models.JSONField(default=list, blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="threats")
    indicators = models.ManyToManyField(
        Indicator, blank=True, related_name="threats"
    )
    threat_actors = models.ManyToManyField(
        ThreatActor, blank=True, related_name="threats"
    )
    raw_payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_threats",
    )

    class Meta:
        ordering = ["-wire_priority", "-published_at", "-id"]
        indexes = [
            models.Index(fields=["severity", "status"]),
            models.Index(fields=["source", "published_at"]),
            models.Index(
                fields=["wire_priority", "published_at"],
                name="intel_threa_wire_pr_idx",
            ),
        ]
        constraints = [
            # Blank URLs stay allowed (title+source fallback); non-empty links
            # must be unique after normalize_wire_url() at ingest time.
            models.UniqueConstraint(
                fields=["source_url"],
                condition=~models.Q(source_url=""),
                name="intel_threat_source_url_nonempty_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class ThreatFavorite(TimeStampedModel):
    """A per-account bookmark for a Wire story."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="threat_favorites",
    )
    threat = models.ForeignKey(
        "Threat",
        on_delete=models.CASCADE,
        related_name="favorites",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "threat"),
                name="uniq_threat_favorite_user_threat",
            )
        ]
        indexes = [
            models.Index(
                fields=("user", "created_at"),
                name="intel_fav_user_created_idx",
            ),
            models.Index(
                fields=("threat", "created_at"),
                name="intel_fav_threat_created_idx",
            ),
        ]


class DataLeak(TimeStampedModel):
    """Observed data leak / breach / information disclosure event."""

    class LeakType(models.TextChoices):
        CREDENTIALS = "credentials", "Credentials"
        STEALER_LOG = "stealer_log", "Stealer Log"
        SOURCE_CODE = "source_code", "Source Code"
        API_KEY = "api_key", "API Key / Secret"
        PASTE = "paste", "Paste Dump"
        BREACH_DUMP = "breach_dump", "Breach Dump"
        OTHER = "other", "Other"

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        NEW = "new", "New"
        INVESTIGATING = "investigating", "Investigating"
        CONFIRMED = "confirmed", "Confirmed"
        CONTAINED = "contained", "Contained"
        FALSE_POSITIVE = "false_positive", "False Positive"
        CLOSED = "closed", "Closed"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        HUDSON_ROCK = "hudson_rock", "Hudson Rock"
        PROXYNOVA = "proxynova", "ProxyNova"
        BREACHDIRECTORY = "breachdirectory", "BreachDirectory"
        PASTEBIN = "pastebin", "Pastebin"
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"
        BITBUCKET = "bitbucket", "Bitbucket"
        STACKOVERFLOW = "stackoverflow", "StackOverflow"
        NPM = "npm", "npm Registry"
        SEARX = "searx", "SearxNG"
        OTHER = "other", "Other"

    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    leak_type = models.CharField(
        max_length=32, choices=LeakType.choices, db_index=True
    )
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.MANUAL,
        db_index=True,
    )
    source_url = models.URLField(max_length=2048, blank=True)
    affected_organization = models.CharField(max_length=255, blank=True, db_index=True)
    affected_domain = models.CharField(max_length=255, blank=True, db_index=True)
    discovered_at = models.DateTimeField(default=timezone.now, db_index=True)
    record_count = models.PositiveIntegerField(default=0)
    tags = models.ManyToManyField(Tag, blank=True, related_name="leaks")
    related_indicators = models.ManyToManyField(
        Indicator, blank=True, related_name="related_leaks"
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_leaks",
    )

    class Meta:
        ordering = ["-discovered_at", "-id"]
        indexes = [
            models.Index(fields=["leak_type", "severity"]),
            models.Index(fields=["affected_domain", "discovered_at"]),
        ]
        verbose_name = "data leak"
        verbose_name_plural = "data leaks"

    def __str__(self) -> str:
        return self.title


class CompromisedCredential(TimeStampedModel):
    """
    Account record extracted from stealer logs or breach dumps.

    OPSEC: plaintext secrets are write-only via API and never returned
    in list/retrieve serializers (masked). Prefer storing only a hash
    for correlation when possible.
    """

    class StealerFamily(models.TextChoices):
        REDLINE = "redline", "RedLine"
        RACCOON = "raccoon", "Raccoon"
        VIDAR = "vidar", "Vidar"
        RASTEALER = "rastealer", "Raccoon Stealer / RaStealer"
        UNKNOWN = "unknown", "Unknown"
        OTHER = "other", "Other"

    leak = models.ForeignKey(
        DataLeak,
        on_delete=models.CASCADE,
        related_name="credentials",
        null=True,
        blank=True,
    )
    email = models.EmailField(blank=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, db_index=True)
    # Sensitive — never expose via default API responses
    password = models.CharField(max_length=512, blank=True)
    password_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="SHA-256 of password for correlation without revealing plaintext.",
    )
    url = models.URLField(max_length=2048, blank=True)
    domain = models.CharField(max_length=255, blank=True, db_index=True)
    stealer_family = models.CharField(
        max_length=32,
        choices=StealerFamily.choices,
        default=StealerFamily.UNKNOWN,
        db_index=True,
    )
    infected_at = models.DateTimeField(null=True, blank=True, db_index=True)
    country = models.CharField(max_length=64, blank=True)
    raw_line = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["domain", "email"]),
            models.Index(fields=["stealer_family", "infected_at"]),
        ]

    def __str__(self) -> str:
        identity = self.email or self.username or "unknown"
        return f"{identity}@{self.domain or 'n/a'}"


class FeedSource(TimeStampedModel):
    """
    Configurable RSS/Atom source for near-real-time breach / leak / advisory news.

    Seeded from a curated Watcher-compatible list; analysts can add more via API/UI.
    """

    class Category(models.TextChoices):
        CERT = "cert", "CERT / Advisory"
        BREACH = "breach", "Data Breach"
        NEWS = "news", "Security News"
        RANSOMWARE = "ransomware", "Ransomware"
        OTHER = "other", "Other"

    name = models.CharField(max_length=128)
    url = models.URLField(max_length=2048, unique=True)
    category = models.CharField(
        max_length=32, choices=Category.choices, default=Category.NEWS, db_index=True
    )
    confidence = models.PositiveSmallIntegerField(
        default=2,
        help_text="Watcher-style confidence 1 (high trust) … 5 (noisy)",
        db_index=True,
    )
    country = models.CharField(max_length=64, blank=True)
    country_code = models.CharField(max_length=8, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=16, blank=True, db_index=True)
    last_error = models.TextField(blank=True)
    last_item_count = models.PositiveIntegerField(default=0)
    consecutive_failures = models.PositiveSmallIntegerField(
        default=0,
        db_index=True,
        help_text="Incremented on fetch error; reset on success. Deleted after threshold.",
    )
    http_etag = models.CharField(max_length=255, blank=True)
    http_last_modified = models.CharField(max_length=128, blank=True)
    last_body_sha256 = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA-256 of last fetched body; skip XML parse when unchanged.",
    )
    processing_version = models.PositiveSmallIntegerField(
        default=0,
        help_text="RSS parser/policy version last applied to this feed body.",
    )
    is_wordpress = models.BooleanField(default=False, db_index=True)
    wordpress_site_url = models.URLField(max_length=2048, blank=True)
    sitemap_last_scanned_at = models.DateTimeField(null=True, blank=True)
    requires_tor = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Fetch via TOR_SOCKS_PROXY (.onion or clearnet hosts blocked on egress).",
    )

    class Meta:
        ordering = ["confidence", "name"]
        verbose_name = "feed source"
        verbose_name_plural = "feed sources"

    def __str__(self) -> str:
        return f"{self.name} ({self.category})"


class WatchRule(TimeStampedModel):
    """Keyword watch rule — alert when intel matches organisation context (Watcher-style)."""

    class Target(models.TextChoices):
        THREATS = "threats", "Threats / The Wire"
        LEAKS = "leaks", "Data Leaks"
        INDICATORS = "indicators", "Indicators"
        SEARX = "searx", "Searx leak search"
        ALL = "all", "All intel"

    name = models.CharField(max_length=128)
    keyword = models.CharField(max_length=255, db_index=True)
    target = models.CharField(
        max_length=32, choices=Target.choices, default=Target.ALL, db_index=True
    )
    is_active = models.BooleanField(default=True, db_index=True)
    case_sensitive = models.BooleanField(default=False)
    min_severity = models.CharField(
        max_length=16,
        choices=Threat.Severity.choices,
        default=Threat.Severity.INFO,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="watch_rules",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["keyword", "target", "created_by"],
                name="uniq_watchrule_keyword_target_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name}:{self.keyword}"


class DocumentScanKeyword(TimeStampedModel):
    """
    Configurable search phrases for automatic PDF/document discovery.

    Queries are built as exact-phrase Google dorks:
    ``\"cyber warfare\" filetype:pdf`` (not loose OR of single tokens).
    Executed via SearxNG (Google-biased) over a recent time window.
    """

    name = models.CharField(max_length=128)
    keyword = models.CharField(
        max_length=255,
        db_index=True,
        help_text='Exactly two words, e.g. "cyber warfare" or "Taiwan Strait".',
    )
    filetypes = models.CharField(
        max_length=64,
        default="pdf",
        help_text="Comma-separated extensions used with filetype: (default pdf).",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    priority = models.PositiveSmallIntegerField(
        default=50,
        db_index=True,
        help_text="Higher priority keywords are scanned first.",
    )
    notes = models.TextField(blank=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    last_hit_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-priority", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["keyword", "filetypes"],
                name="uniq_document_scan_keyword_filetypes",
            )
        ]
        verbose_name = "document scan keyword"
        verbose_name_plural = "document scan keywords"

    def __str__(self) -> str:
        return f"{self.name}:{self.keyword}"

    def build_query(self) -> str:
        from apps.integrations.searx.document_scan import build_document_query

        return build_document_query(self.keyword or "", self.filetypes or "pdf")


class DeletedDocumentScanKeyword(TimeStampedModel):
    """Remember user-deleted keyword phrases so seed/startup does not resurrect them."""

    keyword = models.CharField(max_length=255, db_index=True)
    filetypes = models.CharField(max_length=64, default="pdf")
    name = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["keyword", "filetypes"],
                name="uniq_deleted_document_scan_keyword_filetypes",
            )
        ]
        verbose_name = "deleted document scan keyword"
        verbose_name_plural = "deleted document scan keywords"

    def __str__(self) -> str:
        return f"{self.keyword}:{self.filetypes}"


class BlockedScannedDocumentUrl(TimeStampedModel):
    """URLs the user dismissed — never re-ingest on later document scans."""

    source_url = models.URLField(max_length=2048, unique=True)
    title = models.CharField(max_length=512, blank=True, default="")
    reason = models.CharField(max_length=64, blank=True, default="user_deleted")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "blocked scanned document URL"
        verbose_name_plural = "blocked scanned document URLs"

    def __str__(self) -> str:
        return self.source_url[:80]


class ScannedDocument(TimeStampedModel):
    """Important PDF/document hit discovered by automatic open-web scanning."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        REVIEWED = "reviewed", "Reviewed"
        ARCHIVED = "archived", "Archived"
        FALSE_POSITIVE = "false_positive", "False Positive"

    class Source(models.TextChoices):
        GOOGLE = "google", "Google"
        BING = "bing", "Bing"
        BRAVE = "brave", "Brave"
        DUCKDUCKGO = "duckduckgo", "DuckDuckGo"
        SEARX = "searx", "SearxNG"
        OTHER = "other", "Other"

    title = models.CharField(max_length=512)
    title_vi = models.CharField(max_length=512, blank=True, default="")
    title_vi_status = models.CharField(
        max_length=16,
        choices=Threat.TitleViStatus.choices,
        default=Threat.TitleViStatus.PENDING,
        db_index=True,
    )
    title_vi_provider = models.CharField(max_length=64, blank=True, default="")
    title_vi_translated_at = models.DateTimeField(null=True, blank=True)
    title_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    summary = models.TextField(blank=True)
    source_url = models.URLField(max_length=2048, unique=True)
    file_path = models.CharField(
        max_length=1024,
        blank=True,
        db_index=True,
        help_text="Display path / URL path for notifications (e.g. /journals/.../file.pdf).",
    )
    host = models.CharField(max_length=255, blank=True, db_index=True)
    filetype = models.CharField(max_length=16, default="pdf", db_index=True)
    keyword = models.ForeignKey(
        DocumentScanKeyword,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
    )
    matched_keyword = models.CharField(max_length=255, blank=True, db_index=True)
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.SEARX,
        db_index=True,
    )
    engine = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    importance_score = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_important = models.BooleanField(default=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    discovered_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-published_at", "-id"]
        indexes = [
            models.Index(fields=["is_important", "discovered_at"]),
            models.Index(fields=["filetype", "discovered_at"]),
        ]
        verbose_name = "scanned document"
        verbose_name_plural = "scanned documents"

    def __str__(self) -> str:
        return self.title


class AlertNotification(TimeStampedModel):
    """In-app alert produced by watch-rule matches (email/Slack later)."""

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    rule = models.ForeignKey(
        WatchRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    title = models.CharField(max_length=512)
    message = models.TextField(blank=True)
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.MEDIUM, db_index=True
    )
    is_read = models.BooleanField(default=False, db_index=True)
    threat = models.ForeignKey(
        Threat, null=True, blank=True, on_delete=models.CASCADE, related_name="alerts"
    )
    leak = models.ForeignKey(
        DataLeak, null=True, blank=True, on_delete=models.CASCADE, related_name="alerts"
    )
    indicator = models.ForeignKey(
        Indicator,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="alerts",
    )
    document = models.ForeignKey(
        "ScannedDocument",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="alerts",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="alert_notifications",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title
