from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness / readiness probe for Docker and load balancers."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        db_ok = False
        try:
            connection.ensure_connection()
            db_ok = True
        except Exception:
            db_ok = False

        payload = {
            "status": "ok" if db_ok else "degraded",
            "service": "newscrawler-backend",
            "phase": 6,
            "database": "up" if db_ok else "down",
            "timestamp": timezone.now().isoformat(),
        }
        code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=code)
