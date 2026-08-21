"""Watcher-style keyword leak hunting via SearxNG (+ optional Exa) → DataLeak."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from apps.intel.models import AlertNotification, DataLeak, WatchRule
from apps.intel.watching import match_leak_against_rules
from apps.integrations.searx.client import (
    is_github_host_url,
    searx_configured,
    search_searx,
)
from apps.integrations.web_reader.enrich import enrich_enabled, enrich_leak_from_url
from apps.integrations.web_reader.exa import discover_exa_hits, exa_configured
from apps.integrations.web_reader.query_packs import (
    build_leak_query_pack,
    query_packs_enabled,
)
from apps.integrations.web_reader.phrase import (
    filter_hits_by_phrase,
    open_web_hit_has_phrase,
)
from apps.integrations.web_reader.recency import sort_hits_newest_first


logger = logging.getLogger(__name__)


def _source_from_hit(hit: dict[str, Any]) -> str:
    engine = str(hit.get("engine") or "").lower()
    host = (urlparse(str(hit.get("url") or "")).hostname or "").lower()
    mapping = (
        (("github",), DataLeak.Source.GITHUB),
        (("gitlab",), DataLeak.Source.GITLAB),
        (("bitbucket",), DataLeak.Source.BITBUCKET),
        (("stackoverflow", "stackexchange"), DataLeak.Source.STACKOVERFLOW),
        (("npm",), DataLeak.Source.NPM),
        (("reddit",), DataLeak.Source.OTHER),
        (("twitter", "x.com", "x_twitter"), DataLeak.Source.OTHER),
        (("reddit_search",), DataLeak.Source.OTHER),
        (("exa",), DataLeak.Source.SEARX),
        (("wigolo",), DataLeak.Source.SEARX),
        (("pastebin", "paste"), DataLeak.Source.PASTEBIN),
    )
    for keys, source in mapping:
        if any(k in engine or k in host for k in keys):
            return source
    return DataLeak.Source.SEARX


def _leak_type_from_source(source: str) -> str:
    if source in {
        DataLeak.Source.GITHUB,
        DataLeak.Source.GITLAB,
        DataLeak.Source.BITBUCKET,
        DataLeak.Source.NPM,
    }:
        return DataLeak.LeakType.SOURCE_CODE
    if source == DataLeak.Source.PASTEBIN:
        return DataLeak.LeakType.PASTE
    if source == DataLeak.Source.STACKOVERFLOW:
        return DataLeak.LeakType.OTHER
    return DataLeak.LeakType.OTHER


def _merge_hits(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for group in groups:
        for hit in group:
            url = str(hit.get("url") or "").strip()
            if not url or url in seen:
                continue
            if is_github_host_url(url):
                continue
            seen.add(url)
            merged.append(hit)
    return merged


def merge_hits_balanced(
    *groups: list[dict[str, Any]],
    limit: int = 40,
) -> list[dict[str, Any]]:
    """
    Round-robin merge channels, then rank newest published first.
    """
    queues: list[list[dict[str, Any]]] = [list(g) for g in groups if g]
    if not queues:
        return []
    limit = max(1, int(limit or 40))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    while queues and len(out) < limit * 2:
        # Over-collect then trim after recency sort so new cookie hits are not cut.
        next_queues: list[list[dict[str, Any]]] = []
        for queue in queues:
            while queue:
                hit = queue.pop(0)
                url = str(hit.get("url") or "").strip()
                if not url or url in seen:
                    continue
                if is_github_host_url(url):
                    continue
                seen.add(url)
                out.append(hit)
                if queue:
                    next_queues.append(queue)
                break
            else:
                continue
            if len(out) >= limit * 2:
                break
        queues = next_queues
    return sort_hits_newest_first(out)[:limit]


def discover_leak_hits(
    keyword: str,
    *,
    limit: int = 15,
    use_packs: bool | None = None,
) -> list[dict[str, Any]]:
    """Run Searx (+ gated Exa + X/Reddit cookie search) across keyword / pack."""
    from apps.integrations.web_reader.channels.reddit import (
        reddit_search_configured,
        search_reddit,
    )
    from apps.integrations.web_reader.channels.x_twitter import (
        search_x_twitter,
        x_twitter_configured,
    )
    from apps.integrations.web_reader.exa import should_call_exa

    packs_on = query_packs_enabled() if use_packs is None else bool(use_packs)
    queries = build_leak_query_pack(keyword) if packs_on else [keyword]
    if not queries:
        queries = [keyword]
    per_query = max(3, limit // max(1, len(queries)))
    groups: list[list[dict[str, Any]]] = []
    # Cheap channels first; Exa only when thin / always / forced by mode.
    for query in queries:
        exact = not packs_on
        if searx_configured():
            groups.append(search_searx(query, limit=per_query, exact=exact))
    if x_twitter_configured():
        groups.append(search_x_twitter(keyword, limit=min(limit, 20)))
    if reddit_search_configured():
        groups.append(search_reddit(keyword, limit=min(limit, 25)))

    provisional = filter_hits_by_phrase(
        merge_hits_balanced(*groups, limit=max(1, limit * 2)),
        keyword,
    )
    from apps.integrations.web_reader.wigolo import (
        discover_wigolo_hits,
        should_call_wigolo,
        wigolo_configured,
    )

    if should_call_wigolo(
        purpose="leak",
        kept_hits=len(provisional),
        configured=wigolo_configured(),
    ):
        groups.append(discover_wigolo_hits(keyword, limit=min(max(limit, 12), 20)))
        provisional = filter_hits_by_phrase(
            merge_hits_balanced(*groups, limit=max(1, limit * 2)),
            keyword,
        )
    if should_call_exa(
        purpose="leak",
        kept_hits=len(provisional),
        configured=exa_configured(),
    ):
        groups.append(discover_exa_hits(keyword, limit=min(max(limit, 12), 20)))

    merged = merge_hits_balanced(*groups, limit=max(1, limit * 2))
    return filter_hits_by_phrase(merged, keyword)


def ingest_searx_hits(
    hits: list[dict[str, Any]],
    *,
    keyword: str,
    rule: WatchRule | None = None,
    recipient=None,
    enrich: bool | None = None,
) -> dict[str, int]:
    created = 0
    duplicates = 0
    created_ids: list[int] = []
    do_enrich = enrich_enabled() if enrich is None else bool(enrich)
    sync_enrich = bool(getattr(settings, "SEARX_LEAK_ENRICH_SYNC", False))
    enrich_budget = max(
        0, min(int(getattr(settings, "SEARX_LEAK_ENRICH_BUDGET", 8) or 8), 40)
    )

    for hit in hits:
        url = str(hit.get("url") or "").strip()[:2048]
        if not url:
            continue
        if is_github_host_url(url):
            continue
        if not open_web_hit_has_phrase(hit, keyword):
            continue
        if DataLeak.objects.filter(source_url=url).exists():
            duplicates += 1
            continue
        source = _source_from_hit(hit)
        title = str(hit.get("title") or f"Open-web hit: {keyword}")[:512]
        description = (
            f"Open-web hit for keyword '{keyword}'.\n\n"
            f"{str(hit.get('content') or '')}"
        )[:5000]
        engine = str(hit.get("engine") or "")
        if engine == "x_twitter":
            channel = "x_twitter"
        elif engine == "reddit_search":
            channel = "reddit_search"
        elif engine == "exa":
            channel = "exa"
        else:
            channel = "searx"
        leak = DataLeak.objects.create(
            title=title,
            description=description,
            leak_type=_leak_type_from_source(source),
            severity=DataLeak.Severity.MEDIUM,
            status=DataLeak.Status.NEW,
            source=source,
            source_url=url,
            discovered_at=timezone.now(),
            metadata={
                "searx": channel == "searx",
                "exa": channel == "exa",
                "x_twitter": channel == "x_twitter",
                "reddit_search": channel == "reddit_search",
                "keyword": keyword,
                "engine": engine,
                "score": hit.get("score"),
                "rule_id": rule.id if rule else None,
                "channel": channel,
            },
            created_by=recipient,
        )
        created += 1
        created_ids.append(leak.id)
        match_leak_against_rules(leak)
        if rule:
            AlertNotification.objects.create(
                rule=rule,
                title=f"Open-web leak hit: {keyword}",
                message=f"{title}\n{url}",
                severity=AlertNotification.Severity.MEDIUM,
                leak=leak,
                recipient=recipient or rule.created_by,
            )

    if do_enrich and created_ids:
        if sync_enrich:
            for leak_id in created_ids[:enrich_budget]:
                leak = DataLeak.objects.filter(pk=leak_id).first()
                if leak:
                    enrich_leak_from_url(leak, keyword=keyword)
        else:
            from apps.integrations.tasks import enrich_searx_leak

            for leak_id in created_ids[:enrich_budget]:
                enrich_searx_leak.delay(leak_id)

    return {
        "created": created,
        "duplicates": duplicates,
        "processed": created + duplicates,
        "enrich_queued": min(len(created_ids), enrich_budget) if do_enrich else 0,
    }


def scan_leak_keywords_via_searx(*, limit_per_keyword: int = 15) -> dict[str, Any]:
    """
    Proactively search Watch Rules tagged for leak/Searx monitoring.

    Uses SearxNG and optional Exa; expands keywords via query packs when enabled.
    """
    if not searx_configured() and not exa_configured():
        from apps.integrations.web_reader.channels.reddit import reddit_search_configured
        from apps.integrations.web_reader.channels.x_twitter import x_twitter_configured

        if not x_twitter_configured() and not reddit_search_configured():
            return {
                "skipped": True,
                "reason": "no_discovery_channel",
                "rules_scanned": 0,
                "created": 0,
            }

    rules = (
        WatchRule.objects.filter(is_active=True)
        .filter(
            target__in=[
                WatchRule.Target.SEARX,
                WatchRule.Target.LEAKS,
            ]
        )
        .order_by("keyword")
    )

    total_created = 0
    total_duplicates = 0
    scanned = 0
    for rule in rules:
        scanned += 1
        hits = discover_leak_hits(rule.keyword, limit=limit_per_keyword)
        stats = ingest_searx_hits(
            hits,
            keyword=rule.keyword,
            rule=rule,
            recipient=rule.created_by,
        )
        total_created += stats["created"]
        total_duplicates += stats["duplicates"]
        logger.info(
            "Open-web sweep keyword=%s hits=%s created=%s enrich_queued=%s",
            rule.keyword,
            len(hits),
            stats["created"],
            stats.get("enrich_queued", 0),
        )

    from apps.integrations.web_reader.channels.reddit import reddit_search_configured
    from apps.integrations.web_reader.channels.x_twitter import x_twitter_configured

    return {
        "skipped": False,
        "rules_scanned": scanned,
        "created": total_created,
        "duplicates": total_duplicates,
        "query_packs": query_packs_enabled(),
        "exa": exa_configured(),
        "x_twitter": x_twitter_configured(),
        "reddit_search": reddit_search_configured(),
    }
