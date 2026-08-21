import re

from rest_framework import serializers

from apps.integrations.models import (
    AIBriefing,
    GitHubFinding,
    GitHubScan,
    IntegrationSyncLog,
    Last30DaysFinding,
    Last30DaysResearch,
)

# Empty-fetch noise — keep in raw_response for logs; hide from SPA warnings[].
_SUPPRESSED_PIPELINE_WARN_RE = re.compile(
    r"không đọc được|empty response|đã thử fallback|fetch[_ ]?fail|hard-failed",
    re.IGNORECASE,
)


class AIBriefingSerializer(serializers.ModelSerializer):
    warnings = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    last_notebook_export = serializers.SerializerMethodField()

    class Meta:
        model = AIBriefing
        fields = (
            "id",
            "title",
            "content",
            "provider",
            "status",
            "window_hours",
            "threat_count",
            "indicator_count",
            "leak_count",
            "error_message",
            "progress",
            "progress_pct",
            "warnings",
            "sources",
            "last_notebook_export",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_warnings(self, obj):
        """User-facing warnings only — empty-fetch noise stays in raw_response."""
        raw = obj.raw_response if isinstance(obj.raw_response, dict) else {}
        warnings = raw.get("warnings") or (raw.get("pipeline") or {}).get("warnings") or []
        if not isinstance(warnings, list):
            return []
        out = []
        for w in warnings[:20]:
            if not w:
                continue
            s = str(w)[:240]
            if _SUPPRESSED_PIPELINE_WARN_RE.search(s):
                continue
            out.append(s)
        return out

    def get_sources(self, obj):
        from apps.integrations.ai.notebook_export import extract_briefing_sources

        return extract_briefing_sources(obj)[:40]

    def get_last_notebook_export(self, obj):
        raw = obj.raw_response if isinstance(obj.raw_response, dict) else {}
        last = raw.get("last_notebook_export")
        return last if isinstance(last, dict) else None


class IntegrationSyncLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationSyncLog
        fields = (
            "id",
            "target",
            "direction",
            "status",
            "message",
            "records_processed",
            "details",
            "created_at",
        )
        read_only_fields = fields


class GenerateBriefingSerializer(serializers.Serializer):
    window_hours = serializers.IntegerField(min_value=1, max_value=168, default=24)
    # Async by default — sync exceeds gunicorn worker timeout.
    async_mode = serializers.BooleanField(default=True)


class KeywordSummarySerializer(serializers.Serializer):
    keyword = serializers.CharField(min_length=2, max_length=128)
    window_hours = serializers.IntegerField(min_value=24, max_value=720, default=168)
    async_mode = serializers.BooleanField(default=True)


class ExtractEntitiesSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=200_000)
    persist = serializers.BooleanField(default=False)


class MISPSyncSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=["export", "import", "both"])
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)
    async_mode = serializers.BooleanField(default=False)


class SearxSearchSerializer(serializers.Serializer):
    query = serializers.CharField(min_length=2, max_length=200)
    engines = serializers.CharField(required=False, allow_blank=True, max_length=256)
    limit = serializers.IntegerField(min_value=1, max_value=60, default=40)
    persist = serializers.BooleanField(default=False)
    exact = serializers.BooleanField(default=True)
    # Force Exa even when Searx/X/Reddit already have enough hits (ignored if EXA_OSINT_MODE=off).
    use_exa = serializers.BooleanField(default=False)


class SearxScanSerializer(serializers.Serializer):
    limit_per_keyword = serializers.IntegerField(min_value=1, max_value=40, default=15)
    async_mode = serializers.BooleanField(default=True)


class DocumentScanSerializer(serializers.Serializer):
    limit_per_keyword = serializers.IntegerField(min_value=1, max_value=40, default=15)
    async_mode = serializers.BooleanField(default=True)
    # Manual scans always force past keyword cooldown.
    force = serializers.BooleanField(default=True)


class GitHubScanCreateSerializer(serializers.Serializer):
    keyword = serializers.CharField(min_length=2, max_length=256, trim_whitespace=True)

    def validate_keyword(self, value):
        if any(ord(char) < 32 for char in value):
            raise serializers.ValidationError("Control characters are not allowed.")
        return " ".join(value.split())


class GitHubScanBulkDeleteSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=100,
    )


class GitHubScanSerializer(serializers.ModelSerializer):
    class Meta:
        model = GitHubScan
        fields = (
            "id",
            "keyword",
            "status",
            "max_results",
            "repository_count",
            "file_count",
            "alert_count",
            "critical_count",
            "non_text_count",
            "api_requests",
            "rate_limit_remaining",
            "coverage_limited",
            "duration_ms",
            "error_message",
            "started_at",
            "completed_at",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class GitHubFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = GitHubFinding
        fields = (
            "id",
            "repository",
            "owner",
            "file_path",
            "extension",
            "html_url",
            "repository_url",
            "is_text_file",
            "keyword_matches",
            "match_lines",
            "match_snippets",
            "severity",
            "alert_types",
            "evidence",
            "score",
            "created_at",
        )
        read_only_fields = fields


class GitHubRepositorySummarySerializer(serializers.Serializer):
    repository = serializers.CharField()
    owner = serializers.CharField(allow_blank=True)
    repository_url = serializers.CharField(allow_blank=True)
    file_count = serializers.IntegerField()
    match_total = serializers.IntegerField()
    alert_count = serializers.IntegerField()
    non_text_count = serializers.IntegerField()
    text_count = serializers.IntegerField()


class Last30DaysCreateSerializer(serializers.Serializer):
    topic = serializers.CharField(min_length=2, max_length=512, trim_whitespace=True)
    depth = serializers.ChoiceField(
        choices=("quick", "default", "deep"), default="quick", required=False
    )
    lookback_days = serializers.IntegerField(
        min_value=1, max_value=90, default=30, required=False
    )
    max_results = serializers.IntegerField(
        min_value=5, max_value=200, default=40, required=False
    )
    sources = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        allow_empty=False,
        max_length=12,
    )

    def validate_topic(self, value):
        if any(ord(char) < 32 for char in value):
            raise serializers.ValidationError("Control characters are not allowed.")
        return " ".join(value.split())

    def validate_sources(self, value):
        allowed = {
            "reddit",
            "hackernews",
            "polymarket",
            "x",
            "web",
        }
        cleaned = []
        for raw in value:
            token = str(raw).strip().lower()
            if not token:
                continue
            if token not in allowed:
                raise serializers.ValidationError(f"Unsupported source: {token}")
            if token not in cleaned:
                cleaned.append(token)
        if not cleaned:
            raise serializers.ValidationError("At least one source is required.")
        return cleaned


class Last30DaysResearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Last30DaysResearch
        fields = (
            "id",
            "topic",
            "status",
            "depth",
            "lookback_days",
            "max_results",
            "sources",
            "source_counts",
            "item_count",
            "duration_ms",
            "progress",
            "progress_pct",
            "clusters",
            "errors_by_source",
            "brief_markdown",
            "error_message",
            "started_at",
            "completed_at",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class Last30DaysFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Last30DaysFinding
        fields = (
            "id",
            "source",
            "title",
            "title_vi",
            "title_vi_status",
            "title_vi_provider",
            "url",
            "host",
            "author",
            "snippet",
            "snippet_vi",
            "published_at",
            "score",
            "engagement_score",
            "relevance",
            "freshness",
            "cluster_id",
            "cluster_title",
            "engagement",
            "created_at",
        )
        read_only_fields = fields


class ForumClaimItemSerializer(serializers.Serializer):
    title = serializers.CharField(min_length=2, max_length=512)
    link = serializers.URLField(max_length=2048, required=False, allow_blank=True)
    url = serializers.URLField(max_length=2048, required=False, allow_blank=True)
    published = serializers.CharField(required=False, allow_blank=True, max_length=64)
    feed = serializers.CharField(required=False, allow_blank=True, max_length=64)


class ForumClaimIngestSerializer(serializers.Serializer):
    items = ForumClaimItemSerializer(many=True)
    async_mode = serializers.BooleanField(default=False)
