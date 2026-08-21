"""Rank open-web hits with newest first."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


def parse_published_ts(value: Any) -> float | None:
    """
    Best-effort parse of published/created fields from Searx, Reddit, X, Exa.
    Returns Unix epoch seconds (UTC) or None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        # Reddit created_utc is seconds; ms if huge
        if ts > 1e12:
            ts /= 1000.0
        if 1e8 < ts < 2e10:
            return ts
        return None
    text = str(value).strip()
    if not text:
        return None
    # Pure numeric string (Reddit utc)
    try:
        return parse_published_ts(float(text))
    except ValueError:
        pass
    # ISO-8601
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    # RFC 2822 (X created_at style)
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def hit_recency_ts(hit: dict[str, Any]) -> float:
    """Higher = newer. Undated hits sort last (0)."""
    for key in ("published", "publishedDate", "created_at", "created_utc", "date"):
        ts = parse_published_ts(hit.get(key))
        if ts is not None:
            return ts
    return 0.0


def sort_hits_newest_first(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable-ish sort: newest dated first, undated keep relative order at bottom."""
    indexed = list(enumerate(hits))
    indexed.sort(
        key=lambda pair: (hit_recency_ts(pair[1]), -pair[0]),
        reverse=True,
    )
    return [hit for _, hit in indexed]
