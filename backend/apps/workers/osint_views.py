from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.intel.models import Indicator, Threat
from apps.workers.osint_client import OSINTClientError, health, list_sites, scan_username


class OSINTScanSerializer(serializers.Serializer):
    username = serializers.RegexField(
        regex=r"^[A-Za-z0-9._-]{2,64}$",
        max_length=64,
    )
    sites = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
    )
    timeout_seconds = serializers.IntegerField(
        min_value=5, max_value=120, default=45, required=False
    )
    only_found = serializers.BooleanField(default=False, required=False)
    persist = serializers.BooleanField(
        default=True,
        required=False,
        help_text="When true, store found profile URLs as indicators and a threat summary.",
    )


class OSINTSitesView(APIView):
    def get(self, request):
        try:
            data = list_sites()
        except OSINTClientError as exc:
            return Response(
                {"error": "osint_unavailable", "message": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(data)


class OSINTHealthProxyView(APIView):
    """Authenticated proxy to the OSINT microservice health endpoint."""

    def get(self, request):
        try:
            data = health()
        except OSINTClientError as exc:
            return Response(
                {"status": "down", "error": str(exc), "phase": 4},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(data)


class OSINTScanView(APIView):
    def post(self, request):
        serializer = OSINTScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = scan_username(
                username=data["username"],
                sites=data.get("sites") or [],
                timeout_seconds=data.get("timeout_seconds", 45),
                only_found=data.get("only_found", False),
            )
        except OSINTClientError as exc:
            code = status.HTTP_502_BAD_GATEWAY
            if exc.status_code and 400 <= exc.status_code < 500:
                code = status.HTTP_400_BAD_REQUEST
            return Response(
                {
                    "error": "osint_scan_failed",
                    "message": str(exc),
                    "detail": exc.payload,
                },
                status=code,
            )

        persisted = None
        if data.get("persist", True):
            persisted = _persist_scan_results(result, request.user)

        return Response(
            {
                "scan": result,
                "persisted": persisted,
            }
        )


def _persist_scan_results(result: dict, user) -> dict:
    username = result.get("username", "")
    found_urls = [
        item
        for item in result.get("results", [])
        if item.get("status") == "found" and item.get("url")
    ]
    created_indicators = 0
    for item in found_urls:
        url = item["url"]
        obj, created = Indicator.objects.update_or_create(
            ioc_type=Indicator.Type.URL,
            normalized_value=url.lower()[:2048],
            defaults={
                "value": url[:2048],
                "source": "osint_scan",
                "confidence": Indicator.Confidence.MEDIUM,
                "description": f"Digital footprint hit for username={username} on {item.get('site')}",
                "last_seen": timezone.now(),
                "is_active": True,
                "metadata": {
                    "site": item.get("site"),
                    "category": item.get("category"),
                    "username": username,
                },
                "created_by": user if getattr(user, "is_authenticated", False) else None,
            },
        )
        if created:
            created_indicators += 1

    threat = None
    if found_urls:
        title = f"OSINT footprint: {username} ({len(found_urls)} profiles)"
        threat, _ = Threat.objects.update_or_create(
            title=title[:512],
            source=Threat.Source.OSINT,
            defaults={
                "summary": (
                    f"Username scan found {len(found_urls)} profiles across "
                    f"{result.get('total', 0)} sites in {result.get('duration_ms', 0)}ms."
                ),
                "severity": Threat.Severity.INFO,
                "status": Threat.Status.NEW,
                "published_at": timezone.now(),
                "evidence_score": min(100, len(found_urls) * 5),
                "raw_payload": {
                    "username": username,
                    "found": len(found_urls),
                    "total": result.get("total"),
                    "duration_ms": result.get("duration_ms"),
                },
                "created_by": user if getattr(user, "is_authenticated", False) else None,
            },
        )
        threat.indicators.set(
            Indicator.objects.filter(
                source="osint_scan",
                metadata__username=username,
            )
        )

    return {
        "indicators_created": created_indicators,
        "found_profiles": len(found_urls),
        "threat_id": getattr(threat, "id", None),
    }
