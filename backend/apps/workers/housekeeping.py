"""Safe Wire retention housekeeping (purge stale items + generic tags)."""

from __future__ import annotations

from io import StringIO
from typing import Any

from django.conf import settings
from django.core.management import call_command


def run_wire_housekeeping(*, reset_feed_cache: bool = False) -> dict[str, Any]:
    """
    Safe retention cleanup aligned with Wire age windows.

    - Purge Wire rows older than 30 days and cap storage to the newest 5,000
    - Drop leftover generic news/rss tags
    - Never runs --from-today (would wipe recent non-VN history)
    - Never resets feed HTTP cache unless explicitly requested
    """
    if not bool(getattr(settings, "WIRE_HOUSEKEEPING_ENABLED", True)):
        return {"skipped": True, "reason": "wire_housekeeping_disabled"}

    purge_out = StringIO()
    tags_out = StringIO()
    purge_args: list[str] = []
    if reset_feed_cache:
        purge_args.append("--reset-feed-cache")
    call_command("purge_stale_wire", *purge_args, stdout=purge_out)
    call_command("cleanup_wire_tags", stdout=tags_out)
    return {
        "skipped": False,
        "purge": purge_out.getvalue().strip(),
        "tags": tags_out.getvalue().strip(),
    }
