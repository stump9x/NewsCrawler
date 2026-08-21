from django.contrib import admin

from .models import (
    AlertNotification,
    CompromisedCredential,
    DataLeak,
    DocumentScanKeyword,
    FeedSource,
    Indicator,
    ScannedDocument,
    Tag,
    Threat,
    ThreatActor,
    WatchRule,
)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_at")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ThreatActor)
class ThreatActorAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "is_active", "updated_at")
    list_filter = ("is_active", "country")
    search_fields = ("name", "aliases", "description")


@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = (
        "ioc_type",
        "value",
        "confidence",
        "tlp",
        "source",
        "is_active",
        "last_seen",
    )
    list_filter = ("ioc_type", "confidence", "tlp", "is_active", "source")
    search_fields = ("value", "normalized_value", "description", "source")
    filter_horizontal = ("tags", "threat_actors")
    readonly_fields = ("normalized_value", "created_at", "updated_at")


@admin.register(Threat)
class ThreatAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "severity",
        "status",
        "source",
        "is_kev",
        "evidence_score",
        "published_at",
    )
    list_filter = ("severity", "status", "source", "is_kev")
    search_fields = ("title", "summary", "cve_ids")
    filter_horizontal = ("tags", "indicators", "threat_actors")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DataLeak)
class DataLeakAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "leak_type",
        "severity",
        "status",
        "source",
        "affected_domain",
        "discovered_at",
    )
    list_filter = ("leak_type", "severity", "status", "source")
    search_fields = ("title", "affected_organization", "affected_domain")
    filter_horizontal = ("tags", "related_indicators")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CompromisedCredential)
class CompromisedCredentialAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "username",
        "domain",
        "stealer_family",
        "leak",
        "infected_at",
        "created_at",
    )
    list_filter = ("stealer_family", "country")
    search_fields = ("email", "username", "domain", "url")
    readonly_fields = ("password_fingerprint", "created_at", "updated_at")
    # Never expose stealer plaintext or raw dump lines in Django admin UI
    exclude = ("password", "raw_line")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "leak",
                    "email",
                    "username",
                    "domain",
                    "url",
                    "stealer_family",
                    "infected_at",
                    "country",
                )
            },
        ),
        (
            "Correlation",
            {
                "classes": ("collapse",),
                "fields": ("password_fingerprint", "metadata"),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(FeedSource)
class FeedSourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "confidence",
        "country_code",
        "is_active",
        "last_status",
        "consecutive_failures",
        "last_fetched_at",
    )
    list_filter = ("category", "is_active", "confidence", "last_status")
    search_fields = ("name", "url", "country", "notes")


@admin.register(WatchRule)
class WatchRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "keyword", "target", "is_active", "min_severity", "updated_at")
    list_filter = ("is_active", "target", "min_severity")
    search_fields = ("name", "keyword")


@admin.register(DocumentScanKeyword)
class DocumentScanKeywordAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "keyword",
        "filetypes",
        "priority",
        "is_active",
        "last_hit_count",
        "last_scanned_at",
    )
    list_filter = ("is_active", "filetypes")
    search_fields = ("name", "keyword", "notes")


@admin.register(ScannedDocument)
class ScannedDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "title_vi_status",
        "filetype",
        "matched_keyword",
        "importance_score",
        "host",
        "status",
        "discovered_at",
    )
    list_filter = ("filetype", "status", "source", "is_important", "title_vi_status")
    search_fields = ("title", "title_vi", "source_url", "file_path", "matched_keyword", "host")
    readonly_fields = ("created_at", "updated_at", "discovered_at", "title_vi_translated_at")


@admin.register(AlertNotification)
class AlertNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "severity", "is_read", "rule", "created_at")
    list_filter = ("is_read", "severity")
    search_fields = ("title", "message")
