from django.contrib import admin

from .models import (
    AIBriefing,
    IntegrationSyncLog,
    Last30DaysFinding,
    Last30DaysResearch,
)


@admin.register(AIBriefing)
class AIBriefingAdmin(admin.ModelAdmin):
    list_display = ("title", "provider", "status", "window_hours", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("title", "content")
    readonly_fields = ("created_at", "updated_at", "raw_response")


@admin.register(IntegrationSyncLog)
class IntegrationSyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "target",
        "direction",
        "status",
        "records_processed",
        "created_at",
    )
    list_filter = ("target", "direction", "status")
    readonly_fields = ("created_at", "updated_at", "details")


class Last30DaysFindingInline(admin.TabularInline):
    model = Last30DaysFinding
    extra = 0
    fields = ("source", "title", "score", "url", "published_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(Last30DaysResearch)
class Last30DaysResearchAdmin(admin.ModelAdmin):
    list_display = (
        "topic",
        "status",
        "depth",
        "item_count",
        "lookback_days",
        "duration_ms",
        "created_at",
    )
    list_filter = ("status", "depth")
    search_fields = ("topic",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "raw_report",
        "stderr_tail",
        "source_counts",
        "clusters",
        "errors_by_source",
    )
    inlines = [Last30DaysFindingInline]


@admin.register(Last30DaysFinding)
class Last30DaysFindingAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "score", "research", "published_at")
    list_filter = ("source",)
    search_fields = ("title", "url", "author")
    readonly_fields = ("created_at", "updated_at", "metadata", "engagement")
