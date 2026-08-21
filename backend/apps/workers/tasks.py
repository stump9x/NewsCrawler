from __future__ import annotations

import logging

from celery import shared_task

from apps.workers.feeds.clients import fetch_cert_rss_feeds
from apps.workers.services import ingest_rss_items

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="workers.parse_stealer_log", max_retries=2)
def parse_stealer_log_task(
    self,
    content: str,
    leak_id: int | None = None,
    stealer_family: str | None = None,
    create_leak: bool = False,
    leak_title: str = "Stealer log ingest",
) -> dict:
    """Disabled: stealer/evidence ingest is not part of NewsCrawler."""
    return {"skipped": True, "reason": "disabled_for_defense_project"}


@shared_task(bind=True, name="workers.ingest_cve_feed", max_retries=3)
def ingest_cve_feed(self, limit: int = 30) -> dict:
    # CVE / vulnerability feeds are not used by this defense Wire.
    return {"skipped": True, "reason": "disabled_for_defense_project", "fetched": 0}


@shared_task(bind=True, name="workers.ingest_ransomware_feed", max_retries=3)
def ingest_ransomware_feed(self, limit: int = 30) -> dict:
    # Ransomware victim feeds are not used by this defense Wire.
    return {"skipped": True, "reason": "disabled_for_defense_project", "fetched": 0}


@shared_task(bind=True, name="workers.ingest_cert_rss", max_retries=3)
def ingest_cert_rss(self, limit_per_feed: int = 15) -> dict:
    """Ingest all active defense RSS FeedSource rows."""
    from apps.core.task_lock import single_flight

    # Skip overlapping sweeps: one full catalog pass can exceed the beat interval.
    with single_flight("workers.ingest_cert_rss", ttl_sec=900) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            rss_items = fetch_cert_rss_feeds(limit_per_feed=limit_per_feed)
            backfill_items = []
            items = rss_items + backfill_items
            stats = ingest_rss_items(items, source_label="rss")
            stats["fetched"] = len(items)
            stats["rss_fetched"] = len(rss_items)
            stats["vietnam_backfill_fetched"] = len(backfill_items)
            stats["feeds"] = len({i.get("feed") for i in items if i.get("feed")})
            return stats
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest_cert_rss failed")
            raise self.retry(exc=exc, countdown=60)



@shared_task(bind=True, name="workers.ingest_forum_claims", max_retries=2)
def ingest_forum_claims(self, limit_per_feed: int = 25) -> dict:
    """Disabled: claim/dark-web news is not part of NewsCrawler."""
    return {"skipped": True, "reason": "disabled_for_defense_project", "fetched": 0}


@shared_task(bind=True, name="workers.ingest_zoneh_archive", max_retries=2)
def ingest_zoneh_archive(self, pages: int = 2) -> dict:
    """Disabled: defacement archive is not part of NewsCrawler."""
    return {"skipped": True, "reason": "disabled_for_defense_project", "fetched": 0}


@shared_task(bind=True, name="workers.wire_housekeeping", max_retries=1)
def wire_housekeeping_task(self, reset_feed_cache: bool = False) -> dict:
    """
    Daily safe retention: purge Wire items past age windows + cleanup generic tags.
    Does not touch Postgres volumes, Redis broker, or Docker images.
    """
    from apps.core.task_lock import single_flight
    from apps.workers.housekeeping import run_wire_housekeeping

    with single_flight("workers.wire_housekeeping", ttl_sec=3600) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            return run_wire_housekeeping(reset_feed_cache=bool(reset_feed_cache))
        except Exception as exc:  # noqa: BLE001
            logger.exception("wire_housekeeping failed")
            raise self.retry(exc=exc, countdown=300) from exc


@shared_task(bind=True, name="workers.backfill_wire_images", max_retries=1)
def backfill_wire_images_task(self, limit: int = 80, retry_errors: bool = False) -> dict:
    """Recover cover images (og:image) for wire items whose RSS shipped none."""
    from apps.core.task_lock import single_flight
    from apps.workers.image_enrich import backfill_wire_images

    with single_flight("workers.backfill_wire_images", ttl_sec=900) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            return backfill_wire_images(
                limit=int(limit), retry_errors=bool(retry_errors)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("backfill_wire_images failed")
            raise self.retry(exc=exc, countdown=300) from exc


@shared_task(name="workers.ingest_all_feeds")
def ingest_all_feeds(limit: int = 30) -> dict:
    """Fan-out helper for defense RSS only."""
    cert = ingest_cert_rss.delay(limit_per_feed=max(5, limit // 2))
    return {
        "cert_task_id": cert.id,
    }
