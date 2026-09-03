from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AIBriefingViewSet,
    DocumentScanView,
    ExtractEntitiesView,
    ForumClaimIngestView,
    GenerateBriefingView,
    GitHubScanViewSet,
    IntegrationSyncLogViewSet,
    IntegrationsHealthView,
    KeywordSummaryView,
    Last30DaysResearchViewSet,
    MISPStatusView,
    MISPSyncView,
    NotebookArticleBodiesView,
    NotebookArticleDigestView,
    NotebookChatChitchatView,
    NotebookChatMetricsView,
    NotebookChatPolishView,
    NotebookHealthyModelsView,
    NotebookMarkProviderUnhealthyView,
    NotebookOllamaUnloadView,
    SearxScanView,
    SearxSearchView,
    SearxStatusView,
    WeeklyDigestView,
)

router = DefaultRouter()
router.register(r"ai/briefings", AIBriefingViewSet, basename="ai-briefing")
router.register(r"integrations/logs", IntegrationSyncLogViewSet, basename="integration-log")
router.register(r"github/scans", GitHubScanViewSet, basename="github-scan")
router.register(
    r"trend/researches",
    Last30DaysResearchViewSet,
    basename="trend-research",
)

urlpatterns = [
    path("integrations/health/", IntegrationsHealthView.as_view(), name="integrations-health"),
    path(
        "integrations/forum-claims/",
        ForumClaimIngestView.as_view(),
        name="forum-claims-ingest",
    ),
    path("ai/briefings/generate/", GenerateBriefingView.as_view(), name="ai-briefing-generate"),
    path(
        "ai/notebook-chat/chitchat/",
        NotebookChatChitchatView.as_view(),
        name="notebook-chat-chitchat",
    ),
    path(
        "ai/notebook-chat/polish/",
        NotebookChatPolishView.as_view(),
        name="notebook-chat-polish",
    ),
    path(
        "ai/notebook-chat/unload-ollama/",
        NotebookOllamaUnloadView.as_view(),
        name="notebook-chat-unload-ollama",
    ),
    path(
        "ai/notebook-chat/healthy-models/",
        NotebookHealthyModelsView.as_view(),
        name="notebook-chat-healthy-models",
    ),
    path(
        "ai/notebook-chat/mark-provider/",
        NotebookMarkProviderUnhealthyView.as_view(),
        name="notebook-chat-mark-provider",
    ),
    path(
        "ai/notebook-chat/metrics/",
        NotebookChatMetricsView.as_view(),
        name="notebook-chat-metrics",
    ),
    path(
        "ai/notebook-chat/article-digest/",
        NotebookArticleDigestView.as_view(),
        name="notebook-chat-article-digest",
    ),
    path(
        "ai/notebook-chat/article-bodies/",
        NotebookArticleBodiesView.as_view(),
        name="notebook-chat-article-bodies",
    ),
    path("ai/keyword-summary/", KeywordSummaryView.as_view(), name="ai-keyword-summary"),
    path("ai/weekly-digest/", WeeklyDigestView.as_view(), name="ai-weekly-digest"),
    path("ai/extract-entities/", ExtractEntitiesView.as_view(), name="ai-extract-entities"),
    path("misp/status/", MISPStatusView.as_view(), name="misp-status"),
    path("misp/sync/", MISPSyncView.as_view(), name="misp-sync"),
    path("searx/status/", SearxStatusView.as_view(), name="searx-status"),
    path("searx/search/", SearxSearchView.as_view(), name="searx-search"),
    path("searx/scan/", SearxScanView.as_view(), name="searx-scan"),
    path("documents/scan/", DocumentScanView.as_view(), name="document-scan"),
    path("", include(router.urls)),
]
