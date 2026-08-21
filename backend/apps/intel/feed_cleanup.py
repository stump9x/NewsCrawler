"""Deactivate duplicate RSS feeds; optionally purge broken ones."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

from apps.intel.models import FeedSource


def normalize_feed_url(url: str) -> str:
    """Canonical key for duplicate detection (host/path, ignore www + trailing slash)."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/") or ""
    query = parsed.query or ""
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"
    return urlunparse((scheme, netloc, path, "", query, ""))


def _feed_rank(feed: FeedSource) -> tuple:
    """Higher is better — prefer ok, lower confidence, more items, newer fetch."""
    status_score = {"ok": 3, "": 1, "error": 0}.get(feed.last_status or "", 1)
    fetched = feed.last_fetched_at.timestamp() if feed.last_fetched_at else 0.0
    return (
        1 if feed.is_active else 0,
        status_score,
        -int(feed.confidence or 5),
        int(feed.last_item_count or 0),
        fetched,
        -feed.pk,
    )


def cleanup_feed_sources(
    *, dry_run: bool = False, purge_errors: bool = False
) -> dict[str, Any]:
    """
    Deactivate duplicate normalized URLs. When purge_errors=True, delete all
    feeds with last_status=error (already proven broken).
    """
    errors_deleted = 0
    if purge_errors:
        error_qs = FeedSource.objects.filter(last_status="error")
        errors_deleted = error_qs.count()
        if errors_deleted and not dry_run:
            error_qs.delete()

    active_qs = FeedSource.objects.filter(is_active=True)
    by_norm: dict[str, list[FeedSource]] = {}
    for feed in active_qs:
        key = normalize_feed_url(feed.url)
        if not key:
            continue
        by_norm.setdefault(key, []).append(feed)

    duplicate_ids: list[int] = []
    for group in by_norm.values():
        if len(group) < 2:
            continue
        ranked = sorted(group, key=_feed_rank, reverse=True)
        duplicate_ids.extend(extra.pk for extra in ranked[1:])

    duplicates_deactivated = len(duplicate_ids)
    if duplicate_ids and not dry_run:
        FeedSource.objects.filter(pk__in=duplicate_ids).update(is_active=False)

    active_now = FeedSource.objects.filter(is_active=True).count()
    total_now = FeedSource.objects.count()
    if dry_run:
        active_now = active_now - duplicates_deactivated
        if purge_errors:
            # error rows may be active or inactive; subtract deleted from total only
            total_now = total_now - errors_deleted
            active_now = FeedSource.objects.filter(is_active=True).exclude(
                last_status="error"
            ).count() - duplicates_deactivated

    return {
        "errors_deleted": errors_deleted if purge_errors else 0,
        "duplicates_deactivated": duplicates_deactivated,
        "active": active_now,
        "total": total_now if not dry_run else FeedSource.objects.count() - (errors_deleted if purge_errors else 0),
    }
