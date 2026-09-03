"""X/Twitter defense accounts → Trạm tin tức as RSS-shaped rows."""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.integrations.web_reader.channels.x_twitter import (
    fetch_x_user_posts,
    x_twitter_configured,
)
from apps.workers.feed_dates import parse_feed_datetime
from apps.workers.services import is_wire_relevant

logger = logging.getLogger(__name__)

# No BreachSentinel account list is inherited. Administrators may explicitly
# configure reviewed military/defense accounts through X_WIRE_ACCOUNTS.
DEFAULT_X_WIRE_ACCOUNTS: tuple[str, ...] = ()

_URL_RE = re.compile(r"https?://\S+", re.I)


def _wire_enabled() -> bool:
    return bool(getattr(settings, "X_WIRE_ENABLED", True)) and x_twitter_configured()


def x_wire_accounts() -> list[str]:
    raw = getattr(settings, "X_WIRE_ACCOUNTS", "") or ""
    custom = [a.lstrip("@").strip() for a in str(raw).replace("|", ",").split(",")]
    custom = [a for a in custom if a]
    if custom:
        # Preserve order, drop dupes (case-insensitive).
        seen: set[str] = set()
        out: list[str] = []
        for name in custom:
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out
    seen_def: set[str] = set()
    out_def: list[str] = []
    for name in DEFAULT_X_WIRE_ACCOUNTS:
        key = name.casefold()
        if key in seen_def:
            continue
        seen_def.add(key)
        out_def.append(name)
    return out_def


def _is_important_post(title: str, summary: str) -> bool:
    """Use the same scope as RSS, including non-military strategic policies."""
    return is_wire_relevant({"title": title, "summary": summary, "category": "news"})


def _clean_wire_title(screen: str, text: str) -> str:
    """Compact title for Wire + cheaper/more accurate Google Translate."""
    body = _URL_RE.sub("", text or "").strip()
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        body = (text or "").strip()[:120]
    # Prefer content over "@user: …" prefix noise for title_vi quality.
    title = body[:180] if body else f"@{screen} update"
    return title[:512]


def _hit_to_item(
    hit: dict[str, Any],
    *,
    screen: str,
    oldest,
    seen: set[str],
) -> dict[str, Any] | None:
    url = str(hit.get("url") or "").strip()
    text = str(hit.get("content") or hit.get("title") or "").strip()
    if not url or not text or url in seen:
        return None
    published = parse_feed_datetime(str(hit.get("published") or ""))
    if published is None:
        return None
    if published < oldest:
        return None
    handle = str(hit.get("screen_name") or screen).lstrip("@").strip() or screen
    title = _clean_wire_title(handle, text)
    if not _is_important_post(title, text):
        return None
    seen.add(url)
    return {
        "title": title,
        "link": url[:2048],
        "summary": text[:5000],
        "published": published.isoformat(),
        "feed": f"x:{handle}",
        "feed_url": f"https://x.com/{handle}",
        "category": "news",
        "discovery": "x-wire",
        "engine": "x_twitter",
        "x_handle": handle,
    }


def discover_x_wire_items(
    *,
    limit_per_account: int | None = None,
    accounts: list[str] | None = None,
    now=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pull military/defense posts from explicitly configured X accounts."""
    if not _wire_enabled():
        return [], {
            "skipped": True,
            "reason": "x_wire_disabled_or_unconfigured",
            "accounts": 0,
            "fetched": 0,
        }

    if limit_per_account is None:
        limit_per_account = int(getattr(settings, "X_WIRE_LIMIT_PER_ACCOUNT", 8) or 8)
    per = max(1, min(int(limit_per_account or 8), 20))
    max_age = int(getattr(settings, "X_WIRE_MAX_AGE_DAYS", 7) or 7)
    current = now or timezone.now()
    oldest = current - timedelta(days=max(1, min(max_age, 30)))
    pause_ms = max(0, int(getattr(settings, "X_WIRE_PAUSE_MS", 400) or 0))

    handles = accounts if accounts is not None else x_wire_accounts()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_account: dict[str, Any] = {}
    errors: list[str] = []

    for idx, handle in enumerate(handles):
        detail = fetch_x_user_posts(handle, limit=per)
        hits = detail.get("hits") or []
        err = detail.get("error")
        if err and err not in {"no_hits"}:
            errors.append(f"{handle}: {err}")
        kept = 0
        for hit in hits:
            row = _hit_to_item(hit, screen=handle, oldest=oldest, seen=seen)
            if row:
                items.append(row)
                kept += 1
        per_account[handle] = {
            "hits": len(hits),
            "kept": kept,
            "error": err,
        }
        if pause_ms and idx < len(handles) - 1:
            time.sleep(pause_ms / 1000.0)

    items.sort(key=lambda r: str(r.get("published") or ""), reverse=True)
    return items, {
        "skipped": False,
        "accounts": len(handles),
        "fetched": len(items),
        "per_account": per_account,
        "errors": errors[:12],
    }
