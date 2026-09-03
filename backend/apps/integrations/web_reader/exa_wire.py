"""Exa → Trạm tin tức: turn defense-news hits into RSS-shaped rows."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from apps.integrations.searx.site_discovery import UNSTABLE_INTEL_DOMAINS
from apps.integrations.web_reader.exa import exa_configured, search_exa
from apps.workers.feed_dates import parse_feed_datetime
from apps.core.wire_topics import WIRE_DISCOVERY_QUERIES, discovery_queries
from apps.workers.services import is_wire_relevant
# Defense queries Exa ranks well on (natural language, not Boolean dorks).
DEFAULT_WIRE_QUERIES = WIRE_DISCOVERY_QUERIES


def _wire_queries(*, now=None, max_queries=None) -> list[str]:
    raw = getattr(settings, "EXA_WIRE_QUERIES", "") or ""
    custom = [q.strip() for q in str(raw).split("|") if q.strip()]
    cap = max(1, min(int(getattr(settings, "EXA_WIRE_QUERY_COUNT", 2) or 2), 6))
    if max_queries is not None:
        cap = min(cap, max(1, int(max_queries)))
    if custom:
        return custom[:cap]
    current = now or timezone.now()
    # Existing calls are budgeted; rotate the focus instead of multiplying calls.
    return discovery_queries(count=cap, slot=int(current.timestamp()) // 3600)


def _wire_enabled() -> bool:
    return bool(getattr(settings, "EXA_WIRE_ENABLED", True)) and exa_configured()


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


def _hit_to_item(
    hit: dict[str, Any],
    *,
    feed: str,
    feed_url: str,
    discovery: str,
    oldest,
    seen: set[str],
) -> dict[str, Any] | None:
    url = str(hit.get("url") or "").strip()
    title = str(hit.get("title") or "").strip()
    if not url or not title or url in seen:
        return None
    published = parse_feed_datetime(str(hit.get("published") or ""))
    if published is None:
        return None
    if published < oldest:
        return None
    summary = str(hit.get("content") or "")[:5000]
    seen.add(url)
    return {
        "title": title[:512],
        "link": url[:2048],
        "summary": summary,
        "published": published.isoformat(),
        "feed": feed,
        "feed_url": feed_url,
        "category": "news",
        "discovery": discovery,
        "engine": "exa",
    }


def discover_exa_wire_news(
    *,
    limit: int | None = None,
    now=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Open-web defense news via Exa NL queries → Wire candidates."""
    if not _wire_enabled():
        return [], {
            "skipped": True,
            "reason": "exa_wire_disabled_or_unconfigured",
            "queries": 0,
            "skipped_undated": 0,
        }

    if limit is None:
        limit = int(getattr(settings, "EXA_WIRE_LIMIT", 8) or 8)
    limit = max(1, min(int(limit), 72))
    current = now or timezone.now()
    max_age = int(getattr(settings, "EXA_WIRE_MAX_AGE_DAYS", 30) or 30)
    oldest = current - timedelta(days=max(1, min(max_age, 90)))
    per_query = max(3, min(int(limit or 8), 12))
    queries = _wire_queries(now=current, max_queries=limit)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_undated = 0

    for index, query in enumerate(queries):
        # Reserve room for every selected subtopic; early broad hits must not
        # starve the remaining query. Noise never consumes the result budget.
        quota = limit // len(queries) + (1 if index < limit % len(queries) else 0)
        kept = 0
        hits = search_exa(
            query,
            limit=per_query,
            phrase=None,
            purpose="wire",
            category="news",
            recency_days=max_age,
            require_phrase=False,
        )
        for hit in hits:
            if not str(hit.get("published") or "").strip():
                skipped_undated += 1
                continue
            row = _hit_to_item(
                hit,
                feed="exa:wire",
                feed_url="https://exa.ai",
                discovery="exa-wire",
                oldest=oldest,
                seen=seen,
            )
            if row and is_wire_relevant(row):
                items.append(row)
                kept += 1
            if kept >= quota:
                break
            if len(items) >= limit:
                return items, {
                    "skipped": False,
                    "reason": "",
                    "queries": len(queries),
                    "skipped_undated": skipped_undated,
                }

    return items, {
        "skipped": False,
        "reason": "",
        "queries": len(queries),
        "skipped_undated": skipped_undated,
    }


def discover_exa_site_items(
    *,
    domains: list[str] | tuple[str, ...] | None = None,
    limit_per_domain: int | None = None,
    now=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exa includeDomains fallback for curated sites without stable RSS."""
    if not _wire_enabled():
        return [], {
            "skipped": True,
            "reason": "exa_wire_disabled_or_unconfigured",
            "domains_scanned": 0,
            "skipped_undated": 0,
            "skipped_cross_domain": 0,
        }

    if limit_per_domain is None:
        limit_per_domain = int(getattr(settings, "EXA_WIRE_LIMIT_PER_DOMAIN", 2) or 2)
    current = now or timezone.now()
    oldest = current - timedelta(days=30)
    selected = list(domains) if domains is not None else list(UNSTABLE_INTEL_DOMAINS)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_undated = 0
    skipped_cross_domain = 0
    scanned = 0

    for raw_domain in selected:
        domain = raw_domain.strip().lower().rstrip(".")
        if not domain:
            continue
        scanned += 1
        hits = search_exa(
            f"Latest military defense strategy operations procurement on {domain}",
            limit=max(1, min(int(limit_per_domain or 2), 8)),
            phrase=None,
            purpose="site",
            category="news",
            include_domains=[domain],
            recency_days=30,
            require_phrase=False,
        )
        for hit in hits:
            url = str(hit.get("url") or "").strip()
            if not _is_same_site(url, domain):
                skipped_cross_domain += 1
                continue
            if not str(hit.get("published") or "").strip():
                skipped_undated += 1
                continue
            row = _hit_to_item(
                hit,
                feed=f"exa:{domain}",
                feed_url=f"https://{domain}",
                discovery="exa-site",
                oldest=oldest,
                seen=seen,
            )
            if row:
                items.append(row)

    return items, {
        "skipped": False,
        "reason": "",
        "domains_scanned": scanned,
        "skipped_undated": skipped_undated,
        "skipped_cross_domain": skipped_cross_domain,
    }


def discover_exa_wire_items(
    *,
    limit: int | None = None,
    limit_per_domain: int | None = None,
    domains: list[str] | tuple[str, ...] | None = None,
    now=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Combine open-web CTI + curated-domain Exa discovery for The Wire."""
    if limit is None:
        limit = int(getattr(settings, "EXA_WIRE_LIMIT", 8) or 8)
    if limit_per_domain is None:
        limit_per_domain = int(getattr(settings, "EXA_WIRE_LIMIT_PER_DOMAIN", 2) or 2)
    news, news_meta = discover_exa_wire_news(limit=limit, now=now)
    sites, site_meta = discover_exa_site_items(
        domains=domains, limit_per_domain=limit_per_domain, now=now
    )
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in news + sites:
        url = row.get("link") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        merged.append(row)
    return merged, {
        "skipped": bool(news_meta.get("skipped") and site_meta.get("skipped")),
        "reason": news_meta.get("reason") or site_meta.get("reason") or "",
        "news": news_meta,
        "sites": site_meta,
        "fetched_news": len(news),
        "fetched_sites": len(sites),
    }
