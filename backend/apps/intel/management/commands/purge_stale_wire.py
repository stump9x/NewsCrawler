"""Enforce the configured Wire age and total-item retention limits."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.intel.models import FeedSource
from apps.intel.retention import trim_wire_overflow, wire_storage_queryset


class Command(BaseCommand):
    help = (
        "Delete Wire items older than the configured retention window and "
        "cap storage to the newest configured item count. "
        "Covers NEWS/CERT/RANSOMWARE/X with Wire-shaped payloads."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-today",
            action="store_true",
            help="Delete every non-Vietnam Wire RSS item (keep only Vietnam ≤30d).",
        )
        parser.add_argument(
            "--reset-feed-cache",
            action="store_true",
            help="Clear conditional RSS caches so feeds re-download on next sweep.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        general_days = int(getattr(settings, "WIRE_MAX_AGE_DAYS", 30) or 30)
        vietnam_days = int(
            getattr(settings, "WIRE_VIETNAM_MAX_AGE_DAYS", general_days)
            or general_days
        )
        max_items = max(
            1, int(getattr(settings, "WIRE_MAX_ITEMS", 5000) or 5000)
        )
        vietnam_cut = now - timedelta(days=vietnam_days)

        base = wire_storage_queryset()

        # Drop Vietnam stories outside the 30-day window.
        vn_old = base.filter(tags__slug="vietnam", published_at__lt=vietnam_cut)
        vn_old_count = vn_old.distinct().count()
        vn_old.distinct().delete()

        if options["from_today"]:
            # Keep only Vietnam-tagged items inside the month window.
            stale = base.exclude(tags__slug="vietnam")
        else:
            cut = now - timedelta(days=general_days)
            stale = base.exclude(tags__slug="vietnam").filter(published_at__lt=cut)

        stale_count = stale.distinct().count()
        stale.distinct().delete()

        overflow_count = trim_wire_overflow(max_items=max_items)

        cache_cleared = 0
        if options["reset_feed_cache"]:
            cache_cleared = FeedSource.objects.filter(is_active=True).update(
                http_etag="",
                http_last_modified="",
                last_body_sha256="",
                processing_version=0,
                sitemap_last_scanned_at=None,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Purged vietnam_old={vn_old_count} non_vietnam={stale_count} "
                f"overflow={overflow_count} max_items={max_items} "
                f"feed_cache_reset={cache_cleared}"
            )
        )
