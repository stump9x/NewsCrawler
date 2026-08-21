from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import IsSuperUser
from rest_framework.response import Response

from .filters import (
    CompromisedCredentialFilter,
    DataLeakFilter,
    IndicatorFilter,
    ScannedDocumentFilter,
    ThreatActorFilter,
    ThreatFilter,
)
from .models import (
    AlertNotification,
    CompromisedCredential,
    DataLeak,
    DeletedDocumentScanKeyword,
    DocumentScanKeyword,
    FeedSource,
    Indicator,
    ScannedDocument,
    Tag,
    Threat,
    ThreatFavorite,
    ThreatActor,
    WatchRule,
)
from .serializers import (
    AlertNotificationSerializer,
    CompromisedCredentialSerializer,
    DataLeakSerializer,
    DocumentScanKeywordSerializer,
    FeedSourceSerializer,
    IndicatorSerializer,
    ScannedDocumentSerializer,
    TagSerializer,
    ThreatActorSerializer,
    ThreatSerializer,
    ThreatWireSerializer,
    WatchRuleSerializer,
)


def _read_or_superuser_permissions(view):
    """Global intelligence records are read-only for users; only admin writes."""
    if view.action in {"list", "retrieve"}:
        return [IsAuthenticated()]
    return [IsSuperUser()]

class TagViewSet(viewsets.ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    search_fields = ("name", "slug")
    filter_backends = (SearchFilter, OrderingFilter)
    ordering_fields = ("name", "created_at")
    ordering = ("name",)

    get_permissions = _read_or_superuser_permissions


class ThreatActorViewSet(viewsets.ModelViewSet):
    queryset = ThreatActor.objects.all()
    serializer_class = ThreatActorSerializer
    filterset_class = ThreatActorFilter
    search_fields = ("name", "description", "country")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = ("name", "created_at", "updated_at")
    ordering = ("name",)

    get_permissions = _read_or_superuser_permissions


class IndicatorViewSet(viewsets.ModelViewSet):
    queryset = Indicator.objects.prefetch_related("tags", "threat_actors").all()
    serializer_class = IndicatorSerializer
    filterset_class = IndicatorFilter
    search_fields = ("value", "description", "source")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = ("last_seen", "first_seen", "created_at", "confidence")
    ordering = ("-last_seen",)

    get_permissions = _read_or_superuser_permissions


class ThreatViewSet(viewsets.ModelViewSet):
    queryset = Threat.objects.prefetch_related(
        "tags", "indicators", "threat_actors"
    ).all()
    serializer_class = ThreatSerializer
    filterset_class = ThreatFilter
    search_fields = ("title", "summary", "source_url")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = (
        "published_at",
        "wire_priority",
        "severity",
        "evidence_score",
        "cvss_score",
        "created_at",
        "id",
    )
    # The Feeds is a publication timeline; id deterministically breaks ties.
    ordering = ("-published_at", "-id")

    def get_serializer_class(self):
        wire_requested = str(
            self.request.query_params.get("wire_feed") or ""
        ).lower() in {"1", "true", "yes"}
        if self.action in {"list", "favorites"} and wire_requested:
            return ThreatWireSerializer
        return super().get_serializer_class()

    def get_permissions(self):
        if self.action in {"list", "retrieve", "favorites", "favorite", "mindmap", "mindmap_analyze"}:
            return [IsAuthenticated()]
        return [IsSuperUser()]

    def get_queryset(self):
        queryset = super().get_queryset()
        wire_requested = str(
            self.request.query_params.get("wire_feed") or ""
        ).lower() in {"1", "true", "yes"}
        if wire_requested:
            # Wire cards do not render indicators or threat actors. Clearing
            # the broad class-level prefetch avoids two large joins per page.
            queryset = Threat.objects.prefetch_related("tags").all()
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return queryset
        from django.db.models import Prefetch

        favorites = ThreatFavorite.objects.filter(user=user)
        return queryset.prefetch_related(
            Prefetch(
                "favorites",
                queryset=favorites,
                to_attr="_current_user_favorites",
            )
        )

    def _user_wire_queryset(self, request):
        """Build the unfiltered canonical 30-day Wire timeline for ranks."""
        from .filters import ThreatFilter
        from apps.core.wire_filter_policy import apply_user_wire_policy

        # Rank is global to the user's current Wire corpus. UI filters must not
        # renumber a story, otherwise the same item disagrees across views.
        params = {"wire_feed": "true"}
        filtered = ThreatFilter(
            params, queryset=Threat.objects.all(), request=request
        ).qs.order_by("-published_at", "-id")
        return apply_user_wire_policy(filtered, request.user, prioritize=False)

    def _filtered_user_wire_queryset(self, request):
        """Apply the current UI filters while preserving canonical ordering."""
        from .filters import ThreatFilter
        from apps.core.wire_filter_policy import apply_user_wire_policy

        params = request.query_params.copy()
        params["wire_feed"] = "true"
        filtered = ThreatFilter(
            params, queryset=Threat.objects.all(), request=request
        ).qs.order_by("-published_at", "-id")
        return apply_user_wire_policy(filtered, request.user, prioritize=False)

    @staticmethod
    def _wire_rank_map(queryset):
        """Assign stable-in-window ranks from oldest to newest.

        The feed itself remains newest-first for usability. Numbering is based
        on the opposite chronological order so a newly ingested story receives
        the next highest number instead of renumbering existing stories.
        """
        ordered_ids = list(
            queryset.order_by("published_at", "id").values_list("id", flat=True)
        )
        return {
            item_id: index + 1
            for index, item_id in enumerate(ordered_ids)
        }

    def list(self, request, *args, **kwargs):
        wire_requested = str(
            request.query_params.get("wire_feed") or ""
        ).lower() in {"1", "true", "yes"}
        if not wire_requested:
            return super().list(request, *args, **kwargs)

        # Build the page and canonical ranks once. For the unfiltered timeline,
        # the rank map already contains the exact total, so pagination can skip
        # a second expensive COUNT over the 30-day policy queryset.
        queryset = self.filter_queryset(self.get_queryset())
        rank_map = self._wire_rank_map(self._user_wire_queryset(request))
        filter_keys = {"wire_feed", "page", "page_size", "ordering"}
        if not (set(request.query_params) - filter_keys):
            queryset._wire_fast_count = len(rank_map)
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        data = self.get_serializer(rows, many=True).data
        for row in data:
            row["wire_rank"] = rank_map.get(int(row["id"]))
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def filter_queryset(self, queryset):
        filtered = super().filter_queryset(queryset)
        wire_requested = str(
            self.request.query_params.get("wire_feed") or ""
        ).lower() in {"1", "true", "yes"}
        if not wire_requested:
            return filtered
        from apps.core.wire_filter_policy import apply_user_wire_policy

        # A Wire request always uses one canonical publication timeline, even
        # when a stale client sends an ordering parameter.
        filtered = filtered.order_by("-published_at", "-id")
        return apply_user_wire_policy(filtered, self.request.user, prioritize=False)

    @action(detail=False, methods=["get"], url_path="favorites")
    def favorites(self, request):
        """Return this user's bookmarks while retaining global Wire ranks."""
        full_wire = self._user_wire_queryset(request)
        rank_map = self._wire_rank_map(full_wire)
        filtered_wire = self._filtered_user_wire_queryset(request)
        queryset = filtered_wire.filter(favorites__user=request.user).distinct()
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        data = self.get_serializer(rows, many=True).data
        for row in data:
            row["wire_rank"] = rank_map.get(int(row["id"]))
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @action(detail=True, methods=["post", "delete"], url_path="favorite")
    def favorite(self, request, pk=None):
        """Add/remove one bookmark; only currently visible Wire stories qualify."""
        threat = self.get_object()
        if not self._user_wire_queryset(request).filter(pk=threat.pk).exists():
            return Response(
                {"detail": "Tin không còn thuộc Trạm tin tức trong 30 ngày gần nhất."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.method == "POST":
            favorite, created = ThreatFavorite.objects.get_or_create(
                user=request.user,
                threat=threat,
            )
            return Response(
                {"id": threat.pk, "is_favorite": True, "created": created},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        ThreatFavorite.objects.filter(user=request.user, threat=threat).delete()
        return Response({"id": threat.pk, "is_favorite": False})

    @action(detail=False, methods=["get"], url_path="mindmap")
    def mindmap(self, request):
        """Fast relationship graph; does not call an LLM."""
        from .mindmap import build_mindmap

        try:
            focus_id = int(request.query_params.get("focus_id") or 0) or None
            focus_rank = int(request.query_params.get("focus_rank") or 0) or None
            limit = int(request.query_params.get("limit") or 48)
            days = int(request.query_params.get("days") or 14)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Tham số focus_id, focus_rank, limit hoặc days không hợp lệ."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        search = " ".join((request.query_params.get("search") or "").split())[:160]
        return Response(
            build_mindmap(
                focus_id=focus_id,
                focus_rank=focus_rank,
                limit=limit,
                days=days,
                search=search,
                user=request.user,
            )
        )

    @action(detail=False, methods=["post"], url_path="mindmap-analyze")
    def mindmap_analyze(self, request):
        """Explicit paid-AI refinement for one focused article (cached 24h)."""
        from .mindmap import analyze_focus_with_ai

        try:
            focus_id = int(request.data.get("focus_id") or 0)
            limit = int(request.data.get("limit") or 36)
            days = int(request.data.get("days") or 30)
        except (TypeError, ValueError):
            focus_id = 0
        if focus_id <= 0:
            return Response(
                {"detail": "Cần chọn một tin trung tâm để phân tích AI."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return Response(
                analyze_focus_with_ai(focus_id=focus_id, limit=limit, days=days, user=request.user)
            )
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"AI chưa khả dụng; bản đồ quy tắc vẫn dùng được. {str(exc)[:160]}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class DataLeakViewSet(viewsets.ModelViewSet):
    queryset = DataLeak.objects.prefetch_related(
        "tags", "related_indicators", "credentials"
    ).all()
    serializer_class = DataLeakSerializer
    filterset_class = DataLeakFilter
    search_fields = (
        "title",
        "description",
        "affected_organization",
        "affected_domain",
    )
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = ("discovered_at", "severity", "record_count", "created_at")
    ordering = ("-discovered_at",)

    get_permissions = _read_or_superuser_permissions


class CompromisedCredentialViewSet(viewsets.ModelViewSet):
    queryset = CompromisedCredential.objects.select_related("leak").all()
    serializer_class = CompromisedCredentialSerializer
    filterset_class = CompromisedCredentialFilter
    search_fields = ("email", "username", "domain", "url")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = ("created_at", "infected_at", "domain")
    ordering = ("-created_at",)

    get_permissions = _read_or_superuser_permissions


class FeedSourceViewSet(viewsets.ModelViewSet):
    queryset = FeedSource.objects.all()
    serializer_class = FeedSourceSerializer
    search_fields = ("name", "url", "country", "notes")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("is_active", "category", "confidence", "country_code")
    ordering_fields = ("confidence", "name", "last_fetched_at", "created_at")
    ordering = ("confidence", "name")

    def get_permissions(self):
        # Reads: any authenticated analyst. Mutations: staff only (SSRF surface).
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsSuperUser()]


class DocumentScanKeywordViewSet(viewsets.ModelViewSet):
    queryset = DocumentScanKeyword.objects.all()
    serializer_class = DocumentScanKeywordSerializer
    search_fields = ("name", "keyword", "notes")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("is_active",)
    ordering_fields = ("priority", "name", "last_scanned_at", "created_at")
    ordering = ("-priority", "name")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsSuperUser()]

    def perform_create(self, serializer):
        instance = serializer.save()
        # Re-adding a phrase clears the "user deleted" memory so seed can keep it.
        DeletedDocumentScanKeyword.objects.filter(
            keyword=instance.keyword,
            filetypes=instance.filetypes or "pdf",
        ).delete()

    def perform_destroy(self, instance):
        DeletedDocumentScanKeyword.objects.update_or_create(
            keyword=instance.keyword,
            filetypes=instance.filetypes or "pdf",
            defaults={"name": instance.name or ""},
        )
        instance.delete()


class ScannedDocumentViewSet(viewsets.ModelViewSet):
    queryset = ScannedDocument.objects.select_related("keyword").all()
    serializer_class = ScannedDocumentSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filterset_class = ScannedDocumentFilter
    search_fields = ("title", "summary", "source_url", "file_path", "matched_keyword", "host")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = (
        "discovered_at",
        "published_at",
        "importance_score",
        "created_at",
        "id",
    )
    ordering = ("-published_at", "-id")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsSuperUser()]

    def perform_destroy(self, instance):
        from apps.integrations.searx.document_scan import block_scanned_document_url

        block_scanned_document_url(
            instance.source_url,
            title=instance.title or "",
            reason="user_deleted",
        )
        instance.delete()


class WatchRuleViewSet(viewsets.ModelViewSet):
    serializer_class = WatchRuleSerializer
    search_fields = ("name", "keyword")
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("is_active", "target")
    ordering = ("name",)

    def get_queryset(self):
        qs = WatchRule.objects.all()
        user = self.request.user
        if user and user.is_authenticated and not user.is_superuser:
            return qs.filter(created_by=user)
        return qs


class AlertNotificationViewSet(viewsets.ModelViewSet):
    serializer_class = AlertNotificationSerializer
    http_method_names = ["get", "patch", "head", "options"]
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ("is_read", "severity")
    ordering = ("-created_at",)

    def get_queryset(self):
        from django.db.models import Q

        qs = AlertNotification.objects.select_related(
            "rule", "threat", "leak", "document"
        )
        user = self.request.user
        if user and user.is_authenticated:
            if user.is_superuser:
                return qs.all()
            # System notifications are readable, but only a user's own
            # notification can be changed (for example, marked as read).
            if self.action in {"list", "retrieve"}:
                return qs.filter(Q(recipient=user) | Q(recipient__isnull=True))
            return qs.filter(recipient=user)
        return qs.none()
