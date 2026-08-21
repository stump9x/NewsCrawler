from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.permissions import IsStaffUser, IsSuperUser
from apps.intel.models import Indicator
from apps.integrations.ai.briefings import (
    attach_briefing_task_id,
    cleanup_briefing_queue,
    create_ai_briefing,
    create_keyword_summary,
    create_weekly_trending_digest,
    queue_ai_briefing,
    queue_keyword_summary,
    queue_weekly_trending_digest,
)
from apps.integrations.ai.ner import extract_entities, flatten_entities
from apps.integrations.misp.client import misp_configured
from apps.integrations.misp.sync import (
    export_indicators_to_misp,
    import_attributes_from_misp,
)
from apps.integrations.github.client import github_configured
from apps.integrations.last30days import last30days_configured
from apps.integrations.last30days.runner import default_sources
from apps.integrations.models import (
    AIBriefing,
    GitHubScan,
    IntegrationSyncLog,
    Last30DaysResearch,
)
from apps.integrations.searx.client import searx_configured, search_searx
from apps.integrations.searx.leak_scan import (
    ingest_searx_hits,
    scan_leak_keywords_via_searx,
)
from apps.integrations.serializers import (
    AIBriefingSerializer,
    DocumentScanSerializer,
    ExtractEntitiesSerializer,
    GenerateBriefingSerializer,
    GitHubFindingSerializer,
    GitHubRepositorySummarySerializer,
    GitHubScanBulkDeleteSerializer,
    GitHubScanCreateSerializer,
    GitHubScanSerializer,
    IntegrationSyncLogSerializer,
    KeywordSummarySerializer,
    Last30DaysCreateSerializer,
    Last30DaysFindingSerializer,
    Last30DaysResearchSerializer,
    MISPSyncSerializer,
    SearxScanSerializer,
    SearxSearchSerializer,
)
from apps.integrations.tasks import (
    generate_daily_briefing,
    generate_weekly_digest,
    keyword_summary_task,
    misp_export_task,
    misp_import_task,
    scan_searx_leaks,
    run_github_scan_task,
    run_last30days_research_task,
)


class AIBriefingViewSet(
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """History of AI briefings. Default list = successful (ready) only."""

    serializer_class = AIBriefingSerializer
    permission_classes = [IsStaffUser]
    filterset_fields = ("status", "provider")

    def get_permissions(self):
        if self.action == "purge_stale":
            return [IsSuperUser()]
        return [IsStaffUser()]

    def get_queryset(self):
        qs = AIBriefing.objects.all()
        user = self.request.user
        if user and user.is_authenticated and not user.is_superuser:
            qs = qs.filter(created_by=user)
        status_param = (self.request.query_params.get("status") or "").strip().lower()
        if status_param:
            qs = qs.filter(status=status_param)
        elif self.action == "list":
            # History view: only successful summaries unless status= is set.
            qs = qs.filter(status=AIBriefing.Status.READY)
        return qs

    @action(detail=False, methods=["post"], url_path="purge-stale")
    def purge_stale(self, request):
        """Delete failed + stuck pending queues (keeps ready history)."""
        result = cleanup_briefing_queue()
        return Response({"ok": True, **result})

    @action(detail=True, methods=["post"], url_path="summarize")
    def summarize(self, request, pk=None):
        """Tóm tắt nội dung chính từ báo cáo chi tiết đã có (Groq only)."""
        from apps.integrations.ai.briefing_pipeline import summarize_report_text
        from apps.integrations.ai.clients import AIProviderError

        briefing = self.get_object()
        if briefing.status != AIBriefing.Status.READY or not (briefing.content or "").strip():
            return Response(
                {"detail": "Chỉ tóm tắt được báo cáo đã sẵn sàng có nội dung."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        focus = ""
        raw = briefing.raw_response or {}
        if isinstance(raw, dict):
            focus = str(raw.get("focus") or (raw.get("pipeline") or {}).get("focus") or "")
        try:
            result = summarize_report_text(briefing.content, focus=focus or briefing.title)
        except AIProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Không tóm tắt được: {exc}"[:200]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            {
                "ok": True,
                "briefing_id": briefing.id,
                "title": briefing.title,
                "summary": result["text"],
                "provider": result.get("provider") or "groq",
            }
        )

    @action(detail=True, methods=["post"], url_path="to-notebook")
    def to_notebook(self, request, pk=None):
        """Create a NEW Notebook AI notebook and queue all briefing sources into it."""
        from apps.integrations.ai.notebook_export import (
            NotebookExportError,
            export_briefing_to_notebook,
        )

        briefing = self.get_object()
        if briefing.status != AIBriefing.Status.READY:
            return Response(
                {"detail": "Chỉ thêm được báo cáo đã sẵn sàng."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = export_briefing_to_notebook(briefing)
        except NotebookExportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Không xuất Notebook được: {exc}"[:220]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)


class IntegrationSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = IntegrationSyncLog.objects.all()
    serializer_class = IntegrationSyncLogSerializer
    permission_classes = [IsSuperUser]
    filterset_fields = ("target", "direction", "status")


class GenerateBriefingView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = GenerateBriefingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Default async: sync path kills gunicorn workers (Wigolo+Groq > 120s).
        if data.get("async_mode", True):
            briefing = queue_ai_briefing(
                window_hours=data["window_hours"], user=request.user
            )
            task = generate_daily_briefing.delay(
                window_hours=data["window_hours"], briefing_id=briefing.id
            )
            attach_briefing_task_id(briefing, task.id)
            payload = AIBriefingSerializer(briefing).data
            payload["task_id"] = task.id
            payload["queued"] = True
            payload["cleaned"] = (briefing.raw_response or {}).get("cleaned") or {}
            return Response(payload, status=status.HTTP_202_ACCEPTED)
        briefing = create_ai_briefing(
            window_hours=data["window_hours"], user=request.user
        )
        return Response(
            AIBriefingSerializer(briefing).data,
            status=status.HTTP_201_CREATED,
        )


class NotebookChatChitchatView(APIView):
    """Fast social/chitchat reply — Groq 8b only, no crawl or notebook grounding."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.integrations.ai.notebook_chat import (
            is_social_chitchat_query,
            reply_social_chitchat,
        )

        message = str(request.data.get("message") or request.data.get("question") or "").strip()
        if not message:
            return Response(
                {"ok": False, "error": "message_required", "social": True},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not is_social_chitchat_query(message):
            return Response(
                {"ok": False, "error": "not_social_chitchat", "social": False},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            out = reply_social_chitchat(message=message)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {
                    "ok": False,
                    "text": "",
                    "social": True,
                    "error": str(exc)[:200],
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        if not out.get("ok"):
            code = status.HTTP_502_BAD_GATEWAY
            if out.get("error") == "notebook_groq_not_configured":
                code = status.HTTP_503_SERVICE_UNAVAILABLE
            return Response(out, status=code)
        return Response(out)


class NotebookChatPolishView(APIView):
    """Optional light Groq polish for Notebook drafts that are too short/rambling."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.integrations.ai.notebook_chat import (
            answer_quality_issue,
            polish_notebook_answer,
        )

        question = str(request.data.get("question") or "").strip()
        draft = str(request.data.get("draft") or "").strip()
        if not draft:
            return Response(
                {"ok": False, "error": "draft_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        issue = answer_quality_issue(draft)
        # Only polish when quality is poor — never stampede Groq on every chat.
        force = bool(request.data.get("force"))
        if not issue and not force:
            return Response(
                {
                    "ok": True,
                    "text": draft,
                    "polished": False,
                    "issue": None,
                    "skipped": True,
                }
            )
        try:
            out = polish_notebook_answer(question=question, draft=draft)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {
                    "ok": False,
                    "text": draft,
                    "polished": False,
                    "error": str(exc)[:200],
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(out)


class NotebookOllamaUnloadView(APIView):
    """Unload idle Ollama chat models after Notebook used a local fallback.

    This changes a shared server resource, so regular users must not be able
    to evict models for every other account.
    """

    permission_classes = [IsSuperUser]

    def post(self, request):
        from apps.integrations.ai.notebook_chat import unload_ollama_chat_models

        return Response(unload_ollama_chat_models())


class NotebookHealthyModelsView(APIView):
    """Short-TTL readiness for Notebook chat/transform providers (no completions)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.integrations.ai.notebook_model_router import list_healthy_chat_models

        purpose = str(request.query_params.get("purpose") or "chat").strip()
        return Response(list_healthy_chat_models(purpose=purpose))


class NotebookMarkProviderUnhealthyView(APIView):
    """Report shared provider health; mutation is administrator-only.

    A single user's 402/429 report changes the process-wide router state, so
    exposing this write endpoint to every account would permit cross-user DoS.
    """

    permission_classes = [IsSuperUser]

    def post(self, request):
        from apps.integrations.ai.notebook_model_router import (
            mark_provider_success,
            mark_provider_unhealthy,
        )

        provider = str(request.data.get("provider") or "").strip()
        if not provider:
            return Response(
                {"ok": False, "error": "provider_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        seconds = request.data.get("seconds")
        try:
            sec = float(seconds) if seconds is not None else None
        except (TypeError, ValueError):
            sec = None
        reason = str(request.data.get("reason") or "")[:160]
        try:
            latency_ms = float(request.data.get("latency_ms") or 0) or None
        except (TypeError, ValueError):
            latency_ms = None
        if request.data.get("success"):
            mark_provider_success(provider, latency_ms=latency_ms)
            return Response({"ok": True, "provider": provider, "success": True})
        return Response(
            mark_provider_unhealthy(
                provider,
                seconds=sec,
                reason=reason,
                latency_ms=latency_ms,
            )
        )


class NotebookChatMetricsView(APIView):
    """Privacy-safe Chat v2 aggregates and turn telemetry."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.integrations.ai.notebook_chat_metrics import get_chat_metrics

        return Response(get_chat_metrics())

    def post(self, request):
        from apps.integrations.ai.notebook_chat_metrics import record_chat_turn

        try:
            record_chat_turn(
                mode=str(request.data.get("mode") or "grounded"),
                total_ms=float(request.data.get("total_ms") or 0),
                context_ms=float(request.data.get("context_ms") or 0),
                attempts=int(request.data.get("attempts") or 0),
                source_count=int(request.data.get("source_count") or 0),
                citation_status=str(
                    request.data.get("citation_status") or ""
                ),
                citation_coverage=float(
                    request.data.get("citation_coverage") or 0
                ),
            )
        except (TypeError, ValueError):
            return Response(
                {"ok": False, "error": "invalid_metrics"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"ok": True})


class NotebookArticleDigestView(APIView):
    """Crawl URL + cloud summarize + VI translate for «nội dung chính» chat."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.integrations.ai.notebook_digest import digest_article

        url = str(request.data.get("url") or "").strip()
        title = str(request.data.get("title") or "").strip()
        body = str(request.data.get("body") or "")
        question = str(request.data.get("question") or "").strip()
        source_id = str(request.data.get("source_id") or "").strip()
        notebook_id = str(request.data.get("notebook_id") or "").strip()
        refresh = bool(request.data.get("refresh"))
        allow_ollama = request.data.get("allow_ollama")
        if allow_ollama is None:
            allow_ollama = True
        if not url and len(body.strip()) < 80:
            return Response(
                {"ok": False, "error": "url_or_body_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            out = digest_article(
                url=url,
                title=title,
                body=body,
                question=question,
                allow_ollama=bool(allow_ollama),
                source_id=source_id,
                notebook_id=notebook_id,
                refresh=refresh,
            )
        except Exception as exc:  # noqa: BLE001
            # Soft 200 so FE can fall through to normal chat instead of digest/error.
            return Response(
                {
                    "ok": False,
                    "error": str(exc)[:200],
                    "text": "",
                    "provider": "",
                    "recoverable": True,
                    "title": title,
                    "source_url": url,
                    "quotes": [],
                    "fetch": None,
                },
                status=status.HTTP_200_OK,
            )
        # Always 200 for pipeline results (success, unreadable, or recoverable
        # cloud failure) so SPA can fall through without treating as 5xx.
        return Response(out, status=status.HTTP_200_OK)

class NotebookArticleBodiesView(APIView):
    """Resolve cached (or freshly crawled) plain-text bodies for Notebook chat.

    Cache-first: Redis TTL ~3h. ``crawl_on_miss`` only when grounding needs text
    and cache is empty. Caps body size; does not keep unbounded RAM copies.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.integrations.ai.article_body_cache import resolve_article_bodies

        raw_items = request.data.get("items") or request.data.get("sources") or []
        if not isinstance(raw_items, list):
            return Response(
                {"ok": False, "error": "items_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Bound fan-out to protect Wigolo/RAM on whole-notebook asks.
        items = [x for x in raw_items if isinstance(x, dict)][:12]
        crawl_on_miss = bool(request.data.get("crawl_on_miss"))
        refresh = bool(request.data.get("refresh"))
        try:
            resolved = resolve_article_bodies(
                items,
                crawl_on_miss=crawl_on_miss,
                refresh=refresh,
            )
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"ok": False, "error": str(exc)[:200], "items": []},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # Strip nothing — FE needs text for context inject; sizes already capped.
        return Response(
            {
                "ok": True,
                "items": resolved,
                "cache_hits": sum(1 for i in resolved if i.get("cache_hit")),
                "crawled": sum(1 for i in resolved if i.get("crawled")),
            },
            status=status.HTTP_200_OK,
        )


class KeywordSummaryView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = KeywordSummarySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("async_mode", True):
            briefing = queue_keyword_summary(
                keyword=data["keyword"],
                window_hours=data["window_hours"],
                user=request.user,
            )
            task = keyword_summary_task.delay(
                keyword=data["keyword"],
                window_hours=data["window_hours"],
                briefing_id=briefing.id,
            )
            attach_briefing_task_id(briefing, task.id)
            payload = AIBriefingSerializer(briefing).data
            payload["task_id"] = task.id
            payload["queued"] = True
            payload["cleaned"] = (briefing.raw_response or {}).get("cleaned") or {}
            return Response(payload, status=status.HTTP_202_ACCEPTED)
        briefing = create_keyword_summary(
            keyword=data["keyword"],
            window_hours=data["window_hours"],
            user=request.user,
        )
        return Response(
            AIBriefingSerializer(briefing).data,
            status=status.HTTP_201_CREATED,
        )


class WeeklyDigestView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        async_mode = True
        if isinstance(request.data, dict) and "async_mode" in request.data:
            async_mode = bool(request.data.get("async_mode"))
        if async_mode:
            briefing = queue_weekly_trending_digest(user=request.user)
            task = generate_weekly_digest.delay(briefing_id=briefing.id)
            attach_briefing_task_id(briefing, task.id)
            payload = AIBriefingSerializer(briefing).data
            payload["task_id"] = task.id
            payload["queued"] = True
            payload["cleaned"] = (briefing.raw_response or {}).get("cleaned") or {}
            return Response(payload, status=status.HTTP_202_ACCEPTED)
        briefing = create_weekly_trending_digest(user=request.user)
        return Response(
            AIBriefingSerializer(briefing).data,
            status=status.HTTP_201_CREATED,
        )


class ExtractEntitiesView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = ExtractEntitiesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"]
        persist = serializer.validated_data["persist"]
        entities = extract_entities(text)
        created = 0
        if persist:
            for row in flatten_entities(entities):
                _, was_created = Indicator.objects.update_or_create(
                    ioc_type=row["ioc_type"],
                    normalized_value=Indicator.normalize(row["ioc_type"], row["value"]),
                    defaults={
                        "value": row["value"],
                        "source": "ner_extract",
                        "confidence": Indicator.Confidence.MEDIUM,
                        "description": "Extracted by Phase 6 NER helper",
                        "last_seen": timezone.now(),
                        "is_active": True,
                    },
                )
                if was_created:
                    created += 1
        return Response(
            {
                "entities": entities,
                "persisted_created": created,
            }
        )


class MISPStatusView(APIView):
    def get(self, request):
        return Response(
            {
                "configured": misp_configured(),
                "verify_ssl": bool(getattr(settings, "MISP_VERIFY_SSL", True)),
            }
        )


class MISPSyncView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = MISPSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        direction = data["direction"]
        limit = data["limit"]
        async_mode = data["async_mode"]

        if async_mode:
            tasks = {}
            if direction in {"export", "both"}:
                tasks["export"] = misp_export_task.delay(limit=limit).id
            if direction in {"import", "both"}:
                tasks["import"] = misp_import_task.delay(limit=limit).id
            return Response(
                {"status": "queued", "tasks": tasks},
                status=status.HTTP_202_ACCEPTED,
            )

        logs = []
        if direction in {"export", "both"}:
            logs.append(export_indicators_to_misp(limit=limit))
        if direction in {"import", "both"}:
            logs.append(import_attributes_from_misp(limit=limit))
        return Response(
            {
                "status": "completed",
                "results": IntegrationSyncLogSerializer(logs, many=True).data,
            }
        )


class IntegrationsHealthView(APIView):
    """Authenticated recon of optional integration wiring (not for anonymous)."""

    def get(self, request):
        return Response(
            {
                "service": "newscrawler-integrations",
                "phase": 6,
                "ai": {
                    "groq_configured": bool(
                        (getattr(settings, "GROQ_API_KEY", "") or "")
                        or (getattr(settings, "GROQ_API_KEYS", "") or "")
                    ),
                    "anthropic_configured": bool(
                        getattr(settings, "ANTHROPIC_API_KEY", "")
                    ),
                    "huggingface_configured": bool(
                        getattr(settings, "HUGGINGFACE_API_TOKEN", "")
                    ),
                },
                "forum_claims_clearnet": True,
                "misp_configured": misp_configured(),
                "searxng_configured": searx_configured(),
            }
        )


class ForumClaimIngestView(APIView):
    """Manual/webhook clearnet claim headlines → The Wire (metadata-only)."""

    permission_classes = [IsStaffUser]

    def post(self, request):
        from apps.integrations.serializers import ForumClaimIngestSerializer
        from apps.workers.feeds.forum_enrich import enrich_forum_items
        from apps.workers.feeds.forum_safety import prepare_wire_item_for_safety
        from apps.workers.services import ingest_rss_items
        from apps.workers.tasks import ingest_forum_claims

        serializer = ForumClaimIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("async_mode") and not data.get("items"):
            task = ingest_forum_claims.delay(limit_per_feed=25)
            return Response(
                {"task_id": task.id, "status": "queued"},
                status=status.HTTP_202_ACCEPTED,
            )

        prepared = []
        skipped_unsafe = 0
        for row in data.get("items") or []:
            link = str(row.get("link") or row.get("url") or "")
            item = {
                "title": row.get("title"),
                "link": link,
                "summary": "",
                "published": row.get("published") or "",
                "feed": row.get("feed") or "claim-webhook",
                "feed_url": link,
                "category": "news",
                "discovery": "forum-claim",
                "forum_claim": True,
                "metadata_only": True,
                "feed_notes": "claim/dark-web news webhook",
            }
            safe = prepare_wire_item_for_safety(item)
            if safe is None:
                skipped_unsafe += 1
                continue
            prepared.append(safe)

        prepared = enrich_forum_items(prepared)
        stats = ingest_rss_items(prepared, source_label="claim-webhook")
        stats["skipped_unsafe_webhook"] = skipped_unsafe
        stats["submitted"] = len(data.get("items") or [])
        return Response(stats)


class SearxStatusView(APIView):
    def get(self, request):
        from apps.integrations.web_reader.channels import channel_doctor

        doctor = channel_doctor()
        return Response(
            {
                "configured": searx_configured()
                or bool(doctor.get("exa_configured"))
                or any(
                    c.get("id") in {"x_twitter", "reddit_search"} and c.get("ok")
                    for c in doctor.get("channels") or []
                ),
                "engines": getattr(
                    settings,
                    "SEARXNG_ENGINES",
                    "duckduckgo,brave,bing,gitlab,bitbucket,npm,stackoverflow,qwant,ahmia",
                ),
                "channels": doctor.get("channels") or [],
                "query_packs": doctor.get("query_packs"),
                "enrich": doctor.get("enrich"),
                "web_reader": next(
                    (
                        c
                        for c in doctor.get("channels") or []
                        if c.get("id") == "web_reader"
                    ),
                    {},
                ),
                "exa": next(
                    (c for c in doctor.get("channels") or [] if c.get("id") == "exa"),
                    {},
                ),
            }
        )


class SearxSearchView(APIView):
    """Ad-hoc privacy-respecting metasearch (OSINT). Optional persist → DataLeak."""

    def post(self, request):
        from apps.integrations.web_reader.channels.reddit import (
            reddit_search_configured,
            search_reddit,
        )
        from apps.integrations.web_reader.channels.x_twitter import (
            x_twitter_configured,
        )
        from apps.integrations.web_reader.exa import (
            discover_exa_hits,
            exa_configured as _exa_ok,
            should_call_exa,
        )
        from apps.integrations.web_reader.wigolo import (
            wigolo_configured as _wigolo_ok,
        )

        if (
            not searx_configured()
            and not _exa_ok()
            and not _wigolo_ok()
            and not x_twitter_configured()
            and not reddit_search_configured()
        ):
            return Response(
                {
                    "detail": (
                        "No discovery channel configured. Set SEARXNG_URL, "
                        "WIGOLO_URL, EXA_API_KEY, X cookies, and/or REDDIT_COOKIE."
                    ),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = SearxSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        engines = data.get("engines") or None

        from apps.integrations.searx.leak_scan import merge_hits_balanced
        from apps.integrations.web_reader.channels.x_twitter import (
            search_x_twitter_detail,
        )
        from apps.integrations.web_reader.phrase import filter_hits_by_phrase

        groups: list = []
        channel_stats: dict = {}
        phrase = data["query"]

        # Prefer free/cheap channels first — Exa only as gated fallback.
        if x_twitter_configured():
            x_detail = search_x_twitter_detail(
                data["query"], limit=min(data["limit"], 15)
            )
            x_raw = list(x_detail.get("hits") or [])
            x_hits = filter_hits_by_phrase(x_raw, phrase)
            groups.append(x_hits)
            channel_stats["x_twitter"] = {
                "count": len(x_hits),
                "raw": len(x_raw),
                "error": x_detail.get("error") if not x_hits else None,
            }
        if reddit_search_configured():
            reddit_raw = search_reddit(data["query"], limit=min(data["limit"], 20))
            reddit_hits = filter_hits_by_phrase(reddit_raw, phrase)
            groups.append(reddit_hits)
            channel_stats["reddit_search"] = {
                "count": len(reddit_hits),
                "raw": len(reddit_raw),
                "error": None if reddit_hits else ("no_phrase_match" if reddit_raw else "no_hits"),
            }
        if searx_configured():
            searx_raw = search_searx(
                data["query"],
                engines=engines,
                limit=data["limit"],
                exact=data["exact"],
            )
            searx_hits = filter_hits_by_phrase(searx_raw, phrase)
            groups.append(searx_hits)
            channel_stats["searx"] = {
                "count": len(searx_hits),
                "raw": len(searx_raw),
                "error": None,
            }

        # Searx → Wigolo → Exa (save Exa credits when Wigolo fills the gap).
        from apps.integrations.web_reader.wigolo import (
            discover_wigolo_hits,
            should_call_wigolo,
            wigolo_configured as _wigolo_ok,
        )

        kept_before_wigolo = sum(len(g) for g in groups)
        if should_call_wigolo(
            purpose="osint",
            kept_hits=kept_before_wigolo,
            force=bool(data.get("use_exa")),
            configured=_wigolo_ok(),
        ):
            wigolo_raw = discover_wigolo_hits(
                data["query"], limit=min(max(data["limit"], 10), 20)
            )
            wigolo_hits = filter_hits_by_phrase(wigolo_raw, phrase)
            groups.append(wigolo_hits)
            channel_stats["wigolo"] = {
                "count": len(wigolo_hits),
                "raw": len(wigolo_raw),
                "error": None,
                "skipped": False,
            }
        elif _wigolo_ok():
            channel_stats["wigolo"] = {
                "count": 0,
                "raw": 0,
                "error": None,
                "skipped": True,
                "reason": (
                    "mode_off"
                    if str(getattr(settings, "WIGOLO_OSINT_MODE", "fallback")).lower()
                    == "off"
                    else "enough_hits"
                ),
            }

        kept_before_exa = sum(len(g) for g in groups)
        force_exa = bool(data.get("use_exa"))
        if should_call_exa(
            purpose="osint",
            kept_hits=kept_before_exa,
            force=force_exa,
            configured=_exa_ok(),
        ):
            exa_raw = discover_exa_hits(
                data["query"], limit=min(max(data["limit"], 10), 20)
            )
            exa_hits = filter_hits_by_phrase(exa_raw, phrase)
            groups.append(exa_hits)
            channel_stats["exa"] = {
                "count": len(exa_hits),
                "raw": len(exa_raw),
                "error": None,
                "skipped": False,
            }
        elif _exa_ok():
            channel_stats["exa"] = {
                "count": 0,
                "raw": 0,
                "error": None,
                "skipped": True,
                "reason": (
                    "mode_off"
                    if str(getattr(settings, "EXA_OSINT_MODE", "fallback")).lower()
                    == "off"
                    else "enough_hits"
                ),
            }

        results = merge_hits_balanced(*groups, limit=data["limit"])
        # Final safety net: never surface/persist phrase-less hits.
        results = filter_hits_by_phrase(results, phrase)
        # Prefer X/Reddit cards near the top (still newest-first within each band).
        from apps.integrations.web_reader.recency import hit_recency_ts

        _social = {"x_twitter", "reddit_search"}
        results.sort(
            key=lambda h: (
                0 if str(h.get("engine") or "") in _social else 1,
                -hit_recency_ts(h),
            )
        )
        persist_stats = None
        if data["persist"] and results:
            persist_stats = ingest_searx_hits(
                results,
                keyword=data["query"],
                rule=None,
                recipient=request.user if request.user.is_authenticated else None,
            )
        return Response(
            {
                "query": data["query"],
                "count": len(results),
                "results": results,
                "persist": persist_stats,
                "channels": channel_stats,
            }
        )


class SearxScanView(APIView):
    """Trigger Watcher-style keyword sweep across SearxNG. Staff only."""

    permission_classes = [IsStaffUser]

    def post(self, request):
        serializer = SearxScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not searx_configured():
            from apps.integrations.web_reader.exa import exa_configured as _exa_ok

            if not _exa_ok():
                return Response(
                    {"detail": "SearxNG is not configured.", "skipped": True},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        if data["async_mode"]:
            task = scan_searx_leaks.delay(
                limit_per_keyword=data["limit_per_keyword"]
            )
            return Response(
                {"task_id": task.id, "status": "queued"},
                status=status.HTTP_202_ACCEPTED,
            )
        result = scan_leak_keywords_via_searx(
            limit_per_keyword=data["limit_per_keyword"]
        )
        return Response({"status": "completed", "result": result})


class DocumentScanView(APIView):
    """Trigger / inspect automatic PDF discovery via SearxNG. Staff only."""

    permission_classes = [IsStaffUser]

    def get(self, request):
        from apps.integrations.searx.document_scan_status import get_document_scan_status

        return Response(get_document_scan_status())

    def post(self, request):
        from django.conf import settings as dj_settings

        if not bool(getattr(dj_settings, "DOCUMENT_SCAN_ENABLED", False)):
            return Response(
                {
                    "detail": "Document scan is disabled (DOCUMENT_SCAN_ENABLED=false).",
                    "skipped": True,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = DocumentScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not searx_configured():
            return Response(
                {"detail": "SearxNG is not configured.", "skipped": True},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if data["async_mode"]:
            from apps.integrations.searx.document_scan_runner import enqueue_document_scan

            kicked = enqueue_document_scan(
                limit_per_keyword=data["limit_per_keyword"],
                force=data.get("force", True),
                terminate_running=True,
            )
            return Response(
                {
                    "task_id": kicked["task_id"],
                    "status": kicked.get("status") or "started",
                    "generation": kicked.get("generation"),
                    "revoked": kicked.get("revoked") or [],
                },
                status=status.HTTP_202_ACCEPTED,
            )
        from apps.integrations.searx.document_scan import scan_documents_via_searx

        result = scan_documents_via_searx(
            limit_per_keyword=data["limit_per_keyword"],
            force=data.get("force", True),
            publish_progress=True,
        )
        return Response({"status": "completed", "result": result})


class GitHubScanViewSet(
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    queryset = GitHubScan.objects.select_related("created_by").all()
    serializer_class = GitHubScanSerializer
    permission_classes = [IsStaffUser]
    filterset_fields = ("status",)
    search_fields = ("keyword",)
    ordering_fields = ("created_at", "file_count", "alert_count", "repository_count")
    ordering = ("-created_at", "-id")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user and user.is_authenticated and not user.is_superuser:
            return qs.filter(created_by=user)
        return qs

    def get_throttles(self):
        self.throttle_scope = (
            "github_scan_create" if getattr(self, "action", None) == "create" else None
        )
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        if not github_configured():
            return Response(
                {"detail": "GitHub Scanner is not configured. Set GITHUB_TOKEN."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = GitHubScanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stale_minutes = max(
            5,
            int(getattr(settings, "GITHUB_SCAN_STALE_MINUTES", 20) or 20),
        )
        stale_cutoff = timezone.now() - timedelta(minutes=stale_minutes)
        GitHubScan.objects.filter(
            status__in=(GitHubScan.Status.QUEUED, GitHubScan.Status.RUNNING),
            updated_at__lt=stale_cutoff,
        ).update(
            status=GitHubScan.Status.FAILED,
            active_slot=None,
            error_message="Scan exceeded the execution window.",
            completed_at=timezone.now(),
        )
        active = GitHubScan.objects.filter(
            status__in=(GitHubScan.Status.QUEUED, GitHubScan.Status.RUNNING)
        ).first()
        if active is not None:
            # The worker slot is global, but another user's object identity is not.
            visible_id = active.id if (request.user.is_superuser or active.created_by_id == request.user.id) else None
            return Response(
                {
                    "detail": "A GitHub scan is already queued or running.",
                    "scan_id": visible_id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            max_results = max(
                1,
                min(int(getattr(settings, "GITHUB_SCAN_MAX_RESULTS", 1500) or 1500), 1500),
            )
            scan = GitHubScan.objects.create(
                keyword=serializer.validated_data["keyword"],
                max_results=max_results,
                created_by=request.user,
            )
        except IntegrityError:
            active = GitHubScan.objects.filter(active_slot=True).first()
            visible_id = (
                active.id
                if active and (request.user.is_superuser or active.created_by_id == request.user.id)
                else None
            )
            return Response(
                {
                    "detail": "A GitHub scan is already queued or running.",
                    "scan_id": visible_id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            task = run_github_scan_task.delay(scan.id)
        except Exception:  # noqa: BLE001
            scan.status = GitHubScan.Status.FAILED
            scan.error_message = "Task queue unavailable."
            scan.completed_at = timezone.now()
            scan.save(
                update_fields=[
                    "status",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            return Response(
                {"detail": "Unable to queue GitHub scan.", "scan_id": scan.id},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                **GitHubScanSerializer(scan).data,
                "task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"])
    def status(self, request):
        return Response({"configured": github_configured()})

    def destroy(self, request, *args, **kwargs):
        scan = self.get_object()
        if scan.status in {GitHubScan.Status.QUEUED, GitHubScan.Status.RUNNING}:
            return Response(
                {"detail": "Cannot delete a queued or running scan."},
                status=status.HTTP_409_CONFLICT,
            )
        scan_id = scan.id
        scan.delete()
        return Response({"deleted": [scan_id]}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        serializer = GitHubScanBulkDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = list(dict.fromkeys(serializer.validated_data["ids"]))
        qs = GitHubScan.objects.filter(id__in=ids)
        if not request.user.is_superuser:
            qs = qs.filter(created_by=request.user)
        blocked = list(
            qs.filter(
                status__in=(GitHubScan.Status.QUEUED, GitHubScan.Status.RUNNING)
            ).values_list("id", flat=True)
        )
        deletable = qs.exclude(id__in=blocked)
        deleted_ids = list(deletable.values_list("id", flat=True))
        deletable.delete()
        return Response(
            {
                "deleted": deleted_ids,
                "blocked": blocked,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def findings(self, request, pk=None):
        scan = self.get_object()
        queryset = scan.findings.all()
        severity = (request.query_params.get("severity") or "").strip().lower()
        alerts_only = (request.query_params.get("alerts_only") or "").lower()
        repository = (request.query_params.get("repository") or "").strip()
        after_id = (request.query_params.get("after_id") or "").strip()
        if severity in {"info", "medium", "high", "critical"}:
            queryset = queryset.filter(severity=severity)
        if alerts_only in {"1", "true", "yes"}:
            queryset = queryset.exclude(alert_types=[])
        if repository:
            queryset = queryset.filter(repository=repository[:512])
        if after_id.isdigit():
            # Incremental stream: only rows persisted after the last seen id.
            queryset = queryset.filter(id__gt=int(after_id)).order_by("id")
        page = self.paginate_queryset(queryset)
        serializer = GitHubFindingSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def repositories(self, request, pk=None):
        """Repo rollup for progressive UI (Details expands files per repo)."""
        from django.db.models import Count, Q, Sum

        scan = self.get_object()
        rows = (
            scan.findings.values("repository", "owner", "repository_url")
            .annotate(
                file_count=Count("id"),
                match_total=Sum("keyword_matches"),
                alert_count=Count("id", filter=~Q(alert_types=[])),
                non_text_count=Count("id", filter=Q(is_text_file=False)),
                text_count=Count("id", filter=Q(is_text_file=True)),
            )
            # Hide weak repos that only have a single .txt hit.
            .exclude(non_text_count=0, text_count=1, file_count=1)
            # Repos with real secret alerts first.
            .order_by(
                "-alert_count",
                "-non_text_count",
                "-match_total",
                "-file_count",
                "repository",
            )
        )
        page = self.paginate_queryset(rows)
        serializer = GitHubRepositorySummarySerializer(page or rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class Last30DaysResearchViewSet(
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Topic research across Reddit/X/Polymarket/web (last ~30 days)."""

    queryset = Last30DaysResearch.objects.select_related("created_by").all()
    serializer_class = Last30DaysResearchSerializer
    permission_classes = [IsStaffUser]
    filterset_fields = ("status", "depth")
    search_fields = ("topic",)
    ordering_fields = ("created_at", "item_count", "duration_ms")
    ordering = ("-created_at", "-id")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user and user.is_authenticated and not user.is_superuser:
            return qs.filter(created_by=user)
        return qs

    def get_throttles(self):
        self.throttle_scope = (
            "last30days_create" if getattr(self, "action", None) == "create" else None
        )
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        if not last30days_configured():
            return Response(
                {
                    "detail": "Last30Days module is not configured "
                    "(missing vendor script or LAST30DAYS_ENABLED=false)."
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = Last30DaysCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        stale_minutes = max(
            5,
            int(getattr(settings, "LAST30DAYS_STALE_MINUTES", 25) or 25),
        )
        stale_cutoff = timezone.now() - timedelta(minutes=stale_minutes)
        Last30DaysResearch.objects.filter(
            status__in=(
                Last30DaysResearch.Status.QUEUED,
                Last30DaysResearch.Status.RUNNING,
            ),
            updated_at__lt=stale_cutoff,
        ).update(
            status=Last30DaysResearch.Status.FAILED,
            active_slot=None,
            error_message="Research exceeded the execution window.",
            completed_at=timezone.now(),
        )
        active = Last30DaysResearch.objects.filter(
            status__in=(
                Last30DaysResearch.Status.QUEUED,
                Last30DaysResearch.Status.RUNNING,
            )
        ).first()
        if active is not None:
            visible_id = active.id if (request.user.is_superuser or active.created_by_id == request.user.id) else None
            return Response(
                {
                    "detail": "A last30days research job is already queued or running.",
                    "research_id": visible_id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        sources = data.get("sources") or default_sources()
        try:
            research = Last30DaysResearch.objects.create(
                topic=data["topic"],
                depth=data.get("depth") or Last30DaysResearch.Depth.QUICK,
                lookback_days=data.get("lookback_days")
                or int(getattr(settings, "LAST30DAYS_DEFAULT_DAYS", 30) or 30),
                max_results=data.get("max_results")
                or int(getattr(settings, "LAST30DAYS_MAX_RESULTS", 40) or 40),
                sources=list(sources),
                created_by=request.user,
            )
        except IntegrityError:
            active = Last30DaysResearch.objects.filter(active_slot=True).first()
            visible_id = (
                active.id
                if active and (request.user.is_superuser or active.created_by_id == request.user.id)
                else None
            )
            return Response(
                {
                    "detail": "A last30days research job is already queued or running.",
                    "research_id": visible_id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            task = run_last30days_research_task.delay(research.id)
        except Exception:  # noqa: BLE001
            research.status = Last30DaysResearch.Status.FAILED
            research.error_message = "Task queue unavailable."
            research.completed_at = timezone.now()
            research.save(
                update_fields=[
                    "status",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            return Response(
                {
                    "detail": "Unable to queue last30days research.",
                    "research_id": research.id,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                **Last30DaysResearchSerializer(research).data,
                "task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"])
    def status(self, request):
        from apps.integrations.last30days.runner import resolve_web_backend
        from apps.integrations.web_reader.channels.reddit import reddit_search_configured
        from apps.integrations.web_reader.channels.x_twitter import x_twitter_configured
        from apps.integrations.web_reader.wigolo import wigolo_configured

        return Response(
            {
                "configured": last30days_configured(),
                "default_sources": default_sources(),
                "default_days": int(
                    getattr(settings, "LAST30DAYS_DEFAULT_DAYS", 30) or 30
                ),
                "web_backend": getattr(settings, "LAST30DAYS_WEB_BACKEND", "") or "",
                "resolved_web_backend": resolve_web_backend(),
                "x_configured": x_twitter_configured(),
                "reddit_configured": reddit_search_configured(),
                "wigolo_configured": wigolo_configured(),
                "deep_brief_enabled": bool(
                    getattr(settings, "LAST30DAYS_BRIEF_ENABLED", True)
                ),
                "brief_enabled": bool(
                    getattr(settings, "LAST30DAYS_BRIEF_ENABLED", True)
                ),
            }
        )

    def destroy(self, request, *args, **kwargs):
        research = self.get_object()
        if research.status in {
            Last30DaysResearch.Status.QUEUED,
            Last30DaysResearch.Status.RUNNING,
        }:
            return Response(
                {"detail": "Cannot delete a queued or running research job."},
                status=status.HTTP_409_CONFLICT,
            )
        research_id = research.id
        research.delete()
        return Response({"deleted": [research_id]}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def deep_brief(self, request, pk=None):
        """Re-synthesize findings-grounded Vietnamese trend brief."""
        from apps.integrations.tasks import last30days_deep_brief_task

        research = self.get_object()
        if not bool(getattr(settings, "LAST30DAYS_BRIEF_ENABLED", True)):
            return Response(
                {"detail": "Research brief synthesis is disabled."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if research.status in {
            Last30DaysResearch.Status.QUEUED,
            Last30DaysResearch.Status.RUNNING,
        }:
            return Response(
                {"detail": "Wait until research finishes before requesting a brief."},
                status=status.HTTP_409_CONFLICT,
            )
        if not research.item_count:
            return Response(
                {"detail": "No findings to synthesize into a brief."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        task = last30days_deep_brief_task.delay(research.id)
        return Response(
            {
                "research_id": research.id,
                "task_id": task.id,
                "status": "queued",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="to-notebook")
    def to_notebook(self, request, pk=None):
        """Create a NEW Notebook AI notebook and queue all research finding URLs."""
        from apps.integrations.ai.notebook_export import (
            NotebookExportError,
            export_last30days_to_notebook,
        )

        research = self.get_object()
        if research.status in {
            Last30DaysResearch.Status.QUEUED,
            Last30DaysResearch.Status.RUNNING,
        }:
            return Response(
                {
                    "detail": "Đợi nghiên cứu xong rồi mới thêm vào Notebook AI.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if research.status == Last30DaysResearch.Status.FAILED and not research.item_count:
            return Response(
                {"detail": "Nghiên cứu thất bại — không có nguồn để thêm."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = export_last30days_to_notebook(research)
        except NotebookExportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Không xuất Notebook được: {exc}"[:220]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)

    @action(detail=True, methods=["get"])
    def findings(self, request, pk=None):
        from apps.integrations.last30days.service import findings_within_lookback_q

        research = self.get_object()
        # Hard age filter so stale rows (pre-fix or undated collectors) never surface.
        queryset = research.findings.filter(findings_within_lookback_q(research))
        source = (request.query_params.get("source") or "").strip().lower()
        after_id = (request.query_params.get("after_id") or "").strip()
        if source:
            queryset = queryset.filter(source=source[:64])
        if after_id.isdigit():
            queryset = queryset.filter(id__gt=int(after_id)).order_by("id")
        page = self.paginate_queryset(queryset)
        serializer = Last30DaysFindingSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
