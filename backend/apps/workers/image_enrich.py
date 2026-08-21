"""Backfill cover images for Wire items whose RSS feed shipped no image.

Some first-party feeds (government/military press rooms, text-only think-tank
blogs) omit images from their RSS. For those items we fetch the article page's
public social-preview metadata (og:image / twitter:image) — head only, never
the article body — to recover a cover image.

Each processed item is stamped with ``raw_payload["image_lookup"]`` so we never
re-hit the same URL on subsequent sweeps:
    - "rss"   image already came from the feed (no fetch performed)
    - "og"    recovered an og:image/twitter:image
    - "none"  page had no usable preview image
    - "error" fetch failed (transient; eligible for a later retry sweep)
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from apps.intel.models import Threat

logger = logging.getLogger(__name__)


def _has_image(payload: dict[str, Any]) -> bool:
    return bool(str(payload.get("image_url") or "").strip())


def backfill_wire_images(
    *, limit: int = 80, retry_errors: bool = False
) -> dict[str, int]:
    """Populate cover images for wire items missing one.

    Returns counts: ``{"scanned", "fetched", "found", "missing", "errors"}``.
    """
    if not bool(getattr(settings, "WIRE_OG_IMAGE_ENABLED", True)):
        return {"skipped": 1, "reason": "wire_og_image_disabled"}  # type: ignore[dict-item]

    from apps.workers.feeds.clients import fetch_og_image

    qs = (
        Threat.objects.filter(wire_relevant=True)
        .exclude(source_url="")
        .only("id", "source_url", "raw_payload")
        .order_by("-published_at", "-id")
    )
    if retry_errors:
        qs = qs.exclude(raw_payload__image_lookup__in=["rss", "og", "none"])
    else:
        qs = qs.exclude(raw_payload__has_key="image_lookup")

    scanned = fetched = found = missing = errors = 0

    for threat in qs.iterator():
        if fetched >= limit:
            break
        scanned += 1
        payload = dict(threat.raw_payload or {})

        # Feed already provided an image — just stamp it so we skip it next time.
        if _has_image(payload):
            payload["image_lookup"] = "rss"
            threat.raw_payload = payload
            threat.save(update_fields=["raw_payload", "updated_at"])
            continue

        fetched += 1
        try:
            image_url = fetch_og_image(threat.source_url)
        except Exception:  # noqa: BLE001 - enrichment must never break a sweep
            logger.exception("og:image lookup crashed for threat %s", threat.id)
            image_url = ""
            payload["image_lookup"] = "error"
            errors += 1
        else:
            if image_url:
                payload["image_url"] = image_url[:2048]
                payload["image_lookup"] = "og"
                found += 1
            else:
                payload["image_lookup"] = "none"
                missing += 1

        threat.raw_payload = payload
        threat.save(update_fields=["raw_payload", "updated_at"])

    return {
        "scanned": scanned,
        "fetched": fetched,
        "found": found,
        "missing": missing,
        "errors": errors,
    }
