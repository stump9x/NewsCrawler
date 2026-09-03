"""Per-source collectors for progressive last30days research.

Uses vendored adapters (HN / Polymarket) plus NewsCrawler's own Reddit + X
clients. Topics use a TopicPlan (lexicon + optional Groq) and hits are
filtered with strict phrase matching (no Biển Đông ↔ biến động).
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone as dt_timezone
from functools import lru_cache
from typing import Any, Callable

import httpx
from django.conf import settings
from django.utils.dateparse import parse_date, parse_datetime

from .paths import vendor_root
from .topic_expand import (
    TopicPlan,
    expand_topic_aliases,
    filter_items_for_topic,
    preferred_english_query,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _ensure_vendor_path() -> None:
    scripts = str(vendor_root() / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _window(days: int) -> tuple[str, str]:
    days = max(1, min(int(days or 30), 90))
    to_d = date.today()
    from_d = to_d - timedelta(days=days)
    return from_d.isoformat(), to_d.isoformat()


def _parse_item_published(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        if 1e8 < ts < 1e10:
            return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
        return None
    text = str(raw).strip()
    if not text:
        return None
    dt = parse_datetime(text)
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt
    try:
        ts = float(text)
        if ts > 1e12:
            ts /= 1000.0
        if 1e8 < ts < 1e10:
            return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    except ValueError:
        pass
    d = parse_date(text[:10]) if len(text) >= 10 else parse_date(text)
    if d is not None:
        return datetime(d.year, d.month, d.day, tzinfo=dt_timezone.utc)
    return None


def filter_items_by_lookback(
    items: list[dict[str, Any]], days: int
) -> list[dict[str, Any]]:
    """Drop items with a known publish time older than the lookback window."""
    days = max(1, min(int(days or 30), 90))
    cutoff = datetime.now(tz=dt_timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for item in items:
        published = _parse_item_published(
            item.get("published_at") or item.get("date") or item.get("published")
        )
        if published is not None and published < cutoff:
            continue
        out.append(item)
    return out


def reddit_time_for_days(days: int) -> str:
    days = max(1, min(int(days or 30), 90))
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


def _norm_vendor_item(item: dict[str, Any], source: str) -> dict[str, Any]:
    url = (
        item.get("url")
        or item.get("hn_url")
        or item.get("permalink")
        or item.get("link")
        or ""
    )
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if not url:
        url = meta.get("hn_url") or meta.get("url") or ""
    title = (
        item.get("title")
        or item.get("snippet")
        or item.get("body")
        or item.get("text")
        or "(untitled)"
    )
    eng = item.get("engagement") if isinstance(item.get("engagement"), dict) else {}
    return {
        "source": source,
        "title": str(title)[:512],
        "url": str(url)[:2048] if url else "",
        "author": str(item.get("author") or item.get("by") or "")[:255],
        "snippet": str(
            item.get("snippet") or item.get("body") or item.get("why_relevant") or ""
        )[:2000],
        "published_at": item.get("published_at") or item.get("date") or "",
        "local_rank_score": float(
            item.get("relevance") or item.get("local_rank_score") or 0
        ),
        "local_relevance": float(item.get("relevance") or 0),
        "engagement_score": float(item.get("engagement_score") or 0),
        "freshness": float(item.get("freshness") or 0),
        "engagement": eng,
        "item_id": item.get("id") or item.get("item_id") or "",
        "metadata": meta or ({"hn_url": item.get("hn_url")} if item.get("hn_url") else {}),
    }


def _norm_nc_hit(hit: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": source,
        "title": str(hit.get("title") or "(untitled)")[:512],
        "url": str(hit.get("url") or "")[:2048],
        "author": str(hit.get("author") or "")[:255],
        "snippet": str(hit.get("content") or hit.get("snippet") or "")[:2000],
        "published_at": hit.get("published") or hit.get("published_at") or "",
        "local_rank_score": float(hit.get("score") or 0.5),
        "local_relevance": 0.5,
        "engagement_score": 0,
        "freshness": 0,
        "engagement": {},
        "item_id": "",
        "metadata": {"engine": hit.get("engine") or source},
    }


def _dedupe_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("url") or "").strip()
        key = url or f"{item.get('source')}:{item.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def collect_hackernews(
    topic: str, *, days: int, depth: str, plan: TopicPlan | None = None
) -> list[dict[str, Any]]:
    _ensure_vendor_path()
    from lib import hackernews as hn  # type: ignore
    from lib import http as vendor_http  # type: ignore

    from_d, to_d = _window(days)
    eng = plan.english if plan else preferred_english_query(topic)
    aliases = list(plan.aliases) if plan else expand_topic_aliases(topic, limit=4)
    queries: list[str] = []
    for q in (eng, *aliases):
        if q and q not in queries:
            queries.append(q)

    count = {"quick": 15, "default": 30, "deep": 60}.get(depth, 15)
    merged: list[dict[str, Any]] = []

    for q in queries[:3]:
        if re.search(r"[A-Za-z].*\s+[A-Za-z]", q) and not re.search(r"[À-ỹ一-鿿]", q):
            from_ts = hn._date_to_unix(from_d)
            to_ts = hn._date_to_unix(to_d) + 86400
            from urllib.parse import urlencode

            params = {
                "query": f'"{q}"',
                "tags": "story",
                "numericFilters": f"created_at_i>{from_ts},created_at_i<{to_ts}",
                "hitsPerPage": str(count * 2),
            }
            url = f"{hn.ALGOLIA_SEARCH_URL}?{urlencode(params)}"
            try:
                raw = vendor_http.request("GET", url, timeout=30)
            except Exception as exc:  # noqa: BLE001
                logger.warning("HN phrase search failed for %s: %s", q, exc)
                raw = hn.search_hackernews(q, from_d, to_d, depth=depth)
        else:
            raw = hn.search_hackernews(q, from_d, to_d, depth=depth)

        items = hn.parse_hackernews_response(raw, query=q)
        if not items and (raw.get("hits") or []):
            items = hn.parse_hackernews_response(raw, query="")
        merged.extend(_norm_vendor_item(it, "hackernews") for it in items)

    return filter_items_by_lookback(
        filter_items_for_topic(
            _dedupe_by_url(merged), topic, trust_english_query=eng, plan=plan
        ),
        days,
    )


def collect_polymarket(
    topic: str, *, days: int, depth: str, plan: TopicPlan | None = None
) -> list[dict[str, Any]]:
    _ensure_vendor_path()
    from lib import polymarket as pm  # type: ignore

    from_d, to_d = _window(days)
    eng = plan.english if plan else preferred_english_query(topic)
    merged: list[dict[str, Any]] = []
    for q in (eng, topic):
        if not q:
            continue
        raw = pm.search_polymarket(q, from_d, to_d, depth=depth)
        items = pm.parse_polymarket_response(raw, topic=q)
        merged.extend(_norm_vendor_item(it, "polymarket") for it in items)
    return filter_items_by_lookback(
        filter_items_for_topic(
            _dedupe_by_url(merged), topic, trust_english_query=eng, plan=plan
        ),
        days,
    )


def collect_reddit(
    topic: str, *, days: int, depth: str, plan: TopicPlan | None = None
) -> list[dict[str, Any]]:
    eng = plan.english if plan else preferred_english_query(topic)
    queries = list(plan.aliases[:5]) if plan else expand_topic_aliases(topic, limit=5)
    if eng not in queries:
        queries = [eng, *queries]

    merged: list[dict[str, Any]] = []
    time_win = reddit_time_for_days(days)
    try:
        from apps.integrations.web_reader.channels.reddit import (
            reddit_search_configured,
            search_reddit,
        )

        if reddit_search_configured():
            limit = {"quick": 12, "default": 20, "deep": 30}.get(depth, 20)
            for q in queries[:3]:
                hits = search_reddit(q, limit=limit, time=time_win)
                merged.extend(_norm_nc_hit(h, "reddit") for h in hits)
            if merged:
                return filter_items_by_lookback(
                    filter_items_for_topic(_dedupe_by_url(merged), topic, plan=plan),
                    days,
                )
            logger.info("NewsCrawler reddit empty/403 — falling back to keyless")
    except Exception as exc:  # noqa: BLE001
        logger.warning("NewsCrawler reddit search failed: %s", exc)

    try:
        _ensure_vendor_path()
        from lib import reddit_public  # type: ignore

        from_d, to_d = _window(days)
        for q in queries[:2]:
            items = reddit_public.search_reddit_public(q, from_d, to_d, depth=depth)
            merged.extend(_norm_vendor_item(it, "reddit") for it in (items or []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("vendor reddit_public failed: %s", exc)
    return filter_items_by_lookback(
        filter_items_for_topic(_dedupe_by_url(merged), topic, plan=plan), days
    )


def collect_x(
    topic: str, *, days: int, depth: str, plan: TopicPlan | None = None
) -> list[dict[str, Any]]:
    from apps.integrations.web_reader.channels.x_twitter import (
        search_x_twitter_detail,
        x_twitter_configured,
    )

    if not x_twitter_configured():
        raise RuntimeError("X not configured (need X_AUTH_TOKEN + X_CT0)")

    eng = plan.english if plan else preferred_english_query(topic)
    queries = list(plan.aliases[:6]) if plan else expand_topic_aliases(topic, limit=6)
    ordered: list[str] = []
    for q in (eng, *queries, topic):
        if q and q not in ordered:
            ordered.append(q)

    limit = {"quick": 12, "default": 20, "deep": 30}.get(depth, 20)
    merged: list[dict[str, Any]] = []
    errors: list[str] = []
    for q in ordered[:4]:
        detail = search_x_twitter_detail(q, limit=limit)
        if detail.get("error") and not detail.get("hits"):
            errors.append(f"{q}: {detail['error']}")
            continue
        merged.extend(_norm_nc_hit(h, "x") for h in (detail.get("hits") or []))

    filtered = filter_items_by_lookback(
        filter_items_for_topic(_dedupe_by_url(merged), topic, plan=plan), days
    )
    if not filtered and errors and not merged:
        raise RuntimeError(errors[0])
    return filtered


def collect_web(
    topic: str, *, days: int, depth: str, plan: TopicPlan | None = None
) -> list[dict[str, Any]]:
    """Open-web grounding via Wigolo (progressive last30days path)."""
    from apps.integrations.web_reader.wigolo import (
        search_wigolo,
        wigolo_configured,
    )

    if not wigolo_configured():
        raise RuntimeError("Wigolo not configured (set WIGOLO_URL)")

    eng = plan.english if plan else preferred_english_query(topic)
    aliases = list(plan.aliases[:4]) if plan else expand_topic_aliases(topic, limit=4)
    queries: list[str] = []
    for q in (eng, *aliases, topic):
        if q and q not in queries:
            queries.append(q)

    limit = {"quick": 8, "default": 12, "deep": 18}.get(depth, 12)
    time_range = "month"
    if days <= 3:
        time_range = "day"
    elif days <= 10:
        time_range = "week"
    elif days > 40:
        time_range = "year"

    hits = search_wigolo(
        queries[:3],
        limit=limit,
        category="news",
        time_range=time_range,
        search_depth="deep" if depth == "deep" else "balanced",
    )
    merged = [_norm_nc_hit(h, "web") for h in hits]
    return filter_items_by_lookback(
        filter_items_for_topic(_dedupe_by_url(merged), topic, plan=plan), days
    )


def _newsnow_source_ids() -> list[str]:
    """Return the small, useful NewsNow source set (never the whole directory)."""
    raw = getattr(settings, "TREND_NEWSNOW_SOURCE_IDS", "cls-hot,weibo,zhihu,bilibili,hupu,v2ex")
    values = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    out: list[str] = []
    for value in values:
        source_id = str(value or "").strip().lower()
        if source_id and re.fullmatch(r"[a-z0-9_-]{2,32}", source_id) and source_id not in out:
            out.append(source_id)
    return out[:12]


def collect_newsnow(
    topic: str, *, days: int, depth: str, plan: TopicPlan | None = None
) -> list[dict[str, Any]]:
    """Fetch NewsNow's public ranked feeds, then apply NewsCrawler topic gates.

    NewsNow aggregates many entertainment and lifestyle feeds, so filtering is
    intentionally done after fetching and before persistence. Only the selected
    source IDs are requested to keep the trend board focused and inexpensive.
    """
    if not bool(getattr(settings, "TREND_NEWSNOW_ENABLED", True)):
        return []
    base = str(
        getattr(settings, "TREND_NEWSNOW_BASE_URL", "https://newsnow.busiyi.world")
        or ""
    ).strip().rstrip("/")
    if not base:
        return []
    source_ids = _newsnow_source_ids()
    if not source_ids:
        return []
    timeout = max(2.0, min(float(getattr(settings, "TREND_NEWSNOW_TIMEOUT_SEC", 8) or 8), 20.0))
    limit = {"quick": 10, "default": 16, "deep": 24}.get(depth, 10)
    merged: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"}) as client:
        for source_id in source_ids:
            try:
                response = client.get(f"{base}/api/s", params={"id": source_id})
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("NewsNow source %s unavailable: %s", source_id, exc)
                continue
            rows = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            for item in rows[:limit]:
                if not isinstance(item, dict):
                    continue
                extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
                published = item.get("pubDate") or extra.get("date") or item.get("date") or ""
                url = item.get("url") or item.get("mobileUrl") or ""
                title = item.get("title") or item.get("name") or ""
                if not title:
                    continue
                hot = item.get("score") or item.get("hot") or extra.get("hot") or extra.get("score") or 0
                try:
                    rank_score = float(hot)
                except (TypeError, ValueError):
                    rank_score = 0.0
                merged.append(
                    {
                        "source": f"newsnow:{source_id}",
                        "title": str(title)[:512],
                        "url": str(url)[:2048],
                        "author": str(item.get("author") or "")[:255],
                        "snippet": str(item.get("description") or item.get("content") or "")[:2000],
                        "published_at": published,
                        "local_rank_score": rank_score,
                        "local_relevance": 1.0,
                        "engagement_score": rank_score,
                        "freshness": 1.0,
                        "engagement": extra,
                        "item_id": item.get("id") or "",
                        "metadata": {"provider": "newsnow", "source_id": source_id},
                    }
                )
    return filter_items_by_lookback(
        filter_items_for_topic(_dedupe_by_url(merged), topic, plan=plan), days
    )


COLLECTORS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "hackernews": collect_hackernews,
    "polymarket": collect_polymarket,
    "reddit": collect_reddit,
    "x": collect_x,
    "web": collect_web,
    "newsnow": collect_newsnow,
}


def available_collectors() -> list[str]:
    out = ["polymarket", "reddit"]
    if bool(getattr(settings, "TREND_NEWSNOW_ENABLED", True)) and _newsnow_source_ids():
        out.append("newsnow")
    try:
        from apps.integrations.web_reader.channels.x_twitter import x_twitter_configured

        if x_twitter_configured():
            out.append("x")
    except Exception:  # noqa: BLE001
        pass
    try:
        from apps.integrations.web_reader.wigolo import wigolo_configured
        from django.conf import settings

        if wigolo_configured() and bool(
            getattr(settings, "WIGOLO_LAST30DAYS_WEB", True)
        ):
            out.append("web")
    except Exception:  # noqa: BLE001
        pass
    return out


def collect_source(
    source: str,
    topic: str,
    *,
    days: int = 30,
    depth: str = "quick",
    plan: TopicPlan | None = None,
) -> list[dict[str, Any]]:
    fn = COLLECTORS.get(source)
    if not fn:
        raise ValueError(f"Unsupported source: {source}")
    return fn(topic, days=days, depth=depth, plan=plan)
