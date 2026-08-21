"""Storage retention helpers for The Wire."""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q, QuerySet

from apps.intel.models import Threat


def wire_storage_queryset() -> QuerySet[Threat]:
    """Return news-like rows that belong to The Wire storage pool."""
    return (
        Threat.objects.filter(
            source__in=(
                Threat.Source.NEWS,
                Threat.Source.CERT,
                Threat.Source.RANSOMWARE,
                Threat.Source.X,
            ),
            wire_relevant=True,
        )
        .filter(
            Q(raw_payload__has_key="feed_source")
            | Q(raw_payload__has_key="discovery")
            | Q(raw_payload__has_key="feed")
        )
        .distinct()
    )


def trim_wire_overflow(
    *,
    max_items: int | None = None,
    batch_size: int = 250,
) -> int:
    """Delete oldest Wire rows in small batches until the configured cap is met."""
    limit = max(
        1,
        int(
            max_items
            if max_items is not None
            else getattr(settings, "WIRE_MAX_ITEMS", 5000) or 5000
        ),
    )
    chunk_size = max(1, int(batch_size))
    queryset = wire_storage_queryset()
    overflow = max(0, queryset.count() - limit)
    deleted_total = 0

    while overflow > 0:
        delete_ids = list(
            queryset.order_by("published_at", "id").values_list("id", flat=True)[
                : min(chunk_size, overflow)
            ]
        )
        if not delete_ids:
            break
        Threat.objects.filter(id__in=delete_ids).delete()
        deleted_total += len(delete_ids)
        overflow -= len(delete_ids)

    return deleted_total
