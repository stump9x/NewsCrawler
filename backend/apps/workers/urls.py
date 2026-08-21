from django.urls import path

from .osint_views import OSINTHealthProxyView, OSINTScanView, OSINTSitesView
from .views import IngestFeedsView, ParseStealerView, WorkerHealthView

urlpatterns = [
    path("workers/health/", WorkerHealthView.as_view(), name="workers-health"),
    path("workers/parse-stealer/", ParseStealerView.as_view(), name="workers-parse-stealer"),
    path("workers/ingest-feeds/", IngestFeedsView.as_view(), name="workers-ingest-feeds"),
    path("osint/health/", OSINTHealthProxyView.as_view(), name="osint-health"),
    path("osint/sites/", OSINTSitesView.as_view(), name="osint-sites"),
    path("osint/scan/", OSINTScanView.as_view(), name="osint-scan"),
]
