from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AlertNotificationViewSet,
    CompromisedCredentialViewSet,
    DataLeakViewSet,
    DocumentScanKeywordViewSet,
    FeedSourceViewSet,
    IndicatorViewSet,
    ScannedDocumentViewSet,
    TagViewSet,
    ThreatActorViewSet,
    ThreatViewSet,
    WatchRuleViewSet,
)

router = DefaultRouter()
router.register(r"tags", TagViewSet, basename="tag")
router.register(r"threat-actors", ThreatActorViewSet, basename="threat-actor")
router.register(r"indicators", IndicatorViewSet, basename="indicator")
router.register(r"threats", ThreatViewSet, basename="threat")
router.register(r"leaks", DataLeakViewSet, basename="leak")
router.register(r"credentials", CompromisedCredentialViewSet, basename="credential")
router.register(r"feed-sources", FeedSourceViewSet, basename="feed-source")
router.register(
    r"document-scan-keywords",
    DocumentScanKeywordViewSet,
    basename="document-scan-keyword",
)
router.register(r"scanned-documents", ScannedDocumentViewSet, basename="scanned-document")
router.register(r"watch-rules", WatchRuleViewSet, basename="watch-rule")
router.register(r"notifications", AlertNotificationViewSet, basename="notification")

urlpatterns = [
    path("", include(router.urls)),
]
