"""Safe Searx fallback for curated sites without stable public RSS."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.utils import timezone

from apps.integrations.searx.client import searx_configured, search_searx
from apps.workers.feed_dates import parse_feed_datetime

UNSTABLE_INTEL_DOMAINS = (
    "haveibeenpwned.com",
    "leakcheck.io",
    "dehashed.com",
    "intelx.io",
    "databreachtoday.com",
    "privacyrights.org",
    "breachsense.com",
    "cybersecurityventures.com",
    "breach.news",
    "data.breach.news",
)


def _is_same_site(url: str, domain: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    expected = domain.lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and (host == expected or host.endswith(f".{expected}"))
    )


def discover_unstable_site_items(
    *,
    domains: list[str] | tuple[str, ...] | None = None,
    limit_per_domain: int = 5,
    now=None,
) -> tuple[list[dict[str, Any]], dict[str, int | bool | str]]:
    """
    Search curated domains and return only dated, same-domain candidates.

    Undated results are intentionally rejected: stamping search results as "now"
    would reintroduce stale news into the three-day Wire window.
    """
    if not searx_configured():
        return [], {
            "skipped": True,
            "reason": "searx_unconfigured",
            "domains_scanned": 0,
            "skipped_undated": 0,
            "skipped_cross_domain": 0,
        }

    current = now or timezone.now()
    oldest_candidate = current - timedelta(days=30)
    selected = domains or UNSTABLE_INTEL_DOMAINS
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_undated = 0
    skipped_cross_domain = 0

    for raw_domain in selected:
        domain = raw_domain.strip().lower().rstrip(".")
        if not domain:
            continue
        query = (
            f'site:{domain} ("data breach" OR "data leak" OR ransomware OR '
            f'"stolen data" OR "malware attack")'
        )
        hits = search_searx(
            query,
            # Bing currently works from common datacenter IPs where DDG/Brave
            # frequently return CAPTCHA/429. Same-domain checks still apply.
            engines="bing",
            limit=max(1, min(int(limit_per_domain or 5), 10)),
            exact=False,
        )
        for hit in hits:
            url = str(hit.get("url") or "").strip()
            if not _is_same_site(url, domain):
                skipped_cross_domain += 1
                continue
            published = parse_feed_datetime(str(hit.get("published") or ""))
            if published is None:
                skipped_undated += 1
                continue
            if published < oldest_candidate or url in seen:
                continue
            seen.add(url)
            items.append(
                {
                    "title": str(hit.get("title") or url)[:512],
                    "link": url[:2048],
                    "summary": str(hit.get("content") or "")[:5000],
                    "published": published.isoformat(),
                    "feed": f"searx:{domain}",
                    "feed_url": f"https://{domain}",
                    "category": "news",
                    "discovery": "searx-site",
                    "searx_engine": hit.get("engine"),
                }
            )

    return items, {
        "skipped": False,
        "reason": "",
        "domains_scanned": len(selected),
        "skipped_undated": skipped_undated,
        "skipped_cross_domain": skipped_cross_domain,
    }
