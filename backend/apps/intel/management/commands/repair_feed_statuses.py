"""Normalize successful checks and requeue transient feed failures."""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.intel.models import FeedSource
from apps.workers.feeds.clients import _is_terminal_feed_error


class Command(BaseCommand):
    help = "Map not_modified to ok and reactivate retryable RSS errors."

    def handle(self, *args, **options):
        normalized = FeedSource.objects.filter(last_status="not_modified").update(
            last_status="ok",
            last_error="",
            consecutive_failures=0,
        )
        retry_limit = max(
            1,
            int(getattr(settings, "FEED_DELETE_AFTER_FAILURES", 3) or 3),
        )
        reactivated = 0
        errors = FeedSource.objects.filter(
            last_status="error",
            is_active=False,
            consecutive_failures__lt=retry_limit,
        ).only("id", "last_error")
        for feed in errors.iterator(chunk_size=200):
            if _is_terminal_feed_error(feed.last_error):
                continue
            reactivated += FeedSource.objects.filter(pk=feed.pk).update(is_active=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Feed statuses repaired · normalized_ok={normalized} "
                f"reactivated={reactivated}"
            )
        )
