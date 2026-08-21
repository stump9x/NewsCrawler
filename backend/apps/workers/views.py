from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsStaffUser
from apps.intel.models import CompromisedCredential, DataLeak
from apps.workers.tasks import ingest_cert_rss, parse_stealer_log_task


class ParseStealerSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=False, max_length=5_000_000)
    leak_id = serializers.IntegerField(required=False, allow_null=True)
    stealer_family = serializers.ChoiceField(
        choices=CompromisedCredential.StealerFamily.choices,
        required=False,
        allow_blank=True,
    )
    create_leak = serializers.BooleanField(default=False)
    leak_title = serializers.CharField(
        required=False, max_length=512, default="Stealer log ingest"
    )
    async_mode = serializers.BooleanField(default=True)

    def validate_leak_id(self, value):
        if value is None:
            return value
        if not DataLeak.objects.filter(pk=value).exists():
            raise serializers.ValidationError("DataLeak not found.")
        return value

    def validate(self, attrs):
        if not attrs.get("leak_id") and not attrs.get("create_leak"):
            raise serializers.ValidationError(
                "Provide leak_id or set create_leak=true."
            )
        return attrs


class IngestFeedsSerializer(serializers.Serializer):
    feeds = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["cert", "all"]
        ),
        allow_empty=False,
    )
    limit = serializers.IntegerField(min_value=1, max_value=200, default=30)
    async_mode = serializers.BooleanField(default=True)


class ParseStealerView(APIView):
    """Enqueue or run stealer log parsing against a DataLeak. Staff only."""

    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = ParseStealerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        family = data.get("stealer_family") or None
        kwargs = {
            "content": data["content"],
            "leak_id": data.get("leak_id"),
            "stealer_family": family,
            "create_leak": data.get("create_leak", False),
            "leak_title": data.get("leak_title", "Stealer log ingest"),
        }
        if data.get("async_mode", True):
            async_result = parse_stealer_log_task.delay(**kwargs)
            return Response(
                {"task_id": async_result.id, "status": "queued"},
                status=status.HTTP_202_ACCEPTED,
            )
        result = parse_stealer_log_task.apply(kwargs=kwargs).get()
        return Response({"task_id": None, "status": "completed", "result": result})


class IngestFeedsView(APIView):
    """Enqueue defense RSS feed ingestion jobs. Staff only."""

    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = IngestFeedsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        feeds = serializer.validated_data["feeds"]
        limit = serializer.validated_data["limit"]
        async_mode = serializer.validated_data["async_mode"]

        if "all" in feeds:
            feeds = ["cert"]

        task_map = {
            "cert": ingest_cert_rss,
        }
        queued = {}
        results = {}
        for feed in feeds:
            task = task_map[feed]
            if async_mode:
                queued[feed] = task.delay(limit_per_feed=max(5, limit // 2)).id
            else:
                results[feed] = task.apply(
                    kwargs={"limit_per_feed": max(5, limit // 2)}
                ).get()

        payload = {"status": "queued" if async_mode else "completed", "tasks": queued}
        if results:
            payload["results"] = results
        code = status.HTTP_202_ACCEPTED if async_mode else status.HTTP_200_OK
        return Response(payload, status=code)


class WorkerHealthView(APIView):
    """Authenticated worker capability probe (do not expose task map anonymously)."""

    def get(self, request):
        return Response(
            {
                "service": "newscrawler-workers",
                "phase": 3,
                "tasks": [
                    "workers.ingest_cert_rss",
                    "workers.ingest_all_feeds",
                    "workers.wire_housekeeping",
                    "integrations.translate_threat_titles",
                ],
            }
        )
