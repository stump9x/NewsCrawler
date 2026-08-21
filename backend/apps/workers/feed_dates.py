"""Parse RSS/Atom timestamps and enforce Wire freshness window."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_feed_datetime(value: str | None) -> Optional[datetime]:
    """
    Parse common feed date formats into an aware UTC datetime.

    Supports ISO-8601 (Atom) and RFC 2822 / RFC 822 (RSS pubDate).
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    dt = parse_datetime(raw)
    if dt is None:
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError, OverflowError):
            dt = None

    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


def extract_datetime_from_url(url: str | None) -> Optional[datetime]:
    """Infer publish date from common CMS URL patterns when RSS omits pubDate."""
    raw = str(url or "").strip()
    if not raw:
        return None

    patterns = (
        # China Daily: /a/201712/12/...
        r"/a/(20\d{2})(\d{2})/(\d{2})(?:/|$)",
        # /2026/07/21/...
        r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)",
        # /2026-07-21/...
        r"/(20\d{2})-(\d{1,2})-(\d{1,2})(?:/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw)
        if not match:
            continue
        year, month, day = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        try:
            return timezone.make_aware(datetime(year, month, day), dt_timezone.utc)
        except ValueError:
            return None

    compact = re.search(r"/(20\d{6})(?:/|$)", raw)
    if compact:
        token = compact.group(1)
        try:
            return timezone.make_aware(
                datetime(int(token[:4]), int(token[4:6]), int(token[6:8])),
                dt_timezone.utc,
            )
        except ValueError:
            return None
    return None


def resolve_item_published(item: dict[str, Any] | None) -> Optional[datetime]:
    """Resolve an item publish time from feed fields, then URL fallback."""
    payload = item or {}
    for key in ("published", "updated", "pubDate", "pubdate", "date"):
        parsed = parse_feed_datetime(str(payload.get(key) or ""))
        if parsed is not None:
            return parsed
    return extract_datetime_from_url(
        str(payload.get("link") or payload.get("url") or "")
    )


def clamp_published_at(
    published: datetime | None,
    *,
    now: datetime | None = None,
    max_future: timedelta | None = None,
) -> datetime:
    """
    Normalize publish time for Wire storage/sort.

    - Missing → now (first-seen)
    - Far-future (clock skew / bad feed) → now
    """
    ref = now or timezone.now()
    skew = max_future if max_future is not None else timedelta(hours=1)
    if published is None:
        return ref
    if published > ref + skew:
        return ref
    return published


def is_within_max_age(
    published: datetime | None,
    *,
    max_age_days: int,
    now: datetime | None = None,
) -> bool:
    """True when published falls inside [now - max_age, now + 1 day] (clock skew)."""
    if published is None:
        # Undated items must not bypass the retention window.
        return False
    if max_age_days <= 0:
        return True
    ref = now or timezone.now()
    age_seconds = (ref - published).total_seconds()
    # Reject far-future stamps (feed clock skew beyond 1 day).
    if age_seconds < -86400:
        return False
    return age_seconds <= max_age_days * 86400
