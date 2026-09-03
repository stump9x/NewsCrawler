"""Persist last30days research runs into Django models."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.integrations.models import Last30DaysFinding, Last30DaysResearch

from .collectors import available_collectors, collect_source
from .runner import default_sources
from .topic_expand import build_topic_plan

logger = logging.getLogger(__name__)


def last30days_configured() -> bool:
    return bool(getattr(settings, "LAST30DAYS_ENABLED", True))


def resolve_max_age_days(research: Last30DaysResearch | None = None, days: int | None = None) -> int:
    """Effective lookback/max-age window (1–90 days)."""
    configured = int(getattr(settings, "LAST30DAYS_MAX_AGE_DAYS", 0) or 0)
    if days is not None:
        base = int(days)
    elif research is not None:
        base = int(research.lookback_days or 30)
    else:
        base = int(getattr(settings, "LAST30DAYS_DEFAULT_DAYS", 30) or 30)
    if configured > 0:
        base = min(base, configured) if base else configured
    return max(1, min(int(base or 30), 90))


def lookback_cutoff(
    research: Last30DaysResearch | None = None,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> datetime:
    age_days = resolve_max_age_days(research, days)
    anchor = now or timezone.now()
    return anchor - timedelta(days=age_days)


def findings_within_lookback_q(
    research: Last30DaysResearch,
    *,
    now: datetime | None = None,
) -> Q:
    """Keep rows whose publish time (or discovery created_at) is inside the window."""
    cutoff = lookback_cutoff(research, now=now)
    return Q(published_at__gte=cutoff) | (
        Q(published_at__isnull=True) & Q(created_at__gte=cutoff)
    )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_unix_ts(raw: Any) -> datetime | None:
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # milliseconds
        ts /= 1000.0
    # Plausible Unix seconds (~1973–2286).
    if ts < 1e8 or ts > 1e10:
        return None
    return datetime.fromtimestamp(ts, tz=dt_timezone.utc)


def _parse_published(raw: Any):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return _parse_unix_ts(raw)
    text = str(raw).strip()
    if not text:
        return None
    dt = parse_datetime(text)
    if dt is not None:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, dt_timezone.utc)
        return dt
    unix_dt = _parse_unix_ts(text)
    if unix_dt is not None:
        return unix_dt
    d = parse_date(text[:10]) if len(text) >= 10 else parse_date(text)
    if d is not None:
        return datetime(d.year, d.month, d.day, tzinfo=dt_timezone.utc)
    return None


def _item_url(item: dict[str, Any]) -> str:
    for key in ("url", "permalink", "link", "hn_url"):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return _canonical_url(val)
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for key in ("hn_url", "url", "permalink"):
        val = meta.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return _canonical_url(val)
    item_id = str(item.get("item_id") or "").strip()
    source = str(item.get("source") or "").strip().lower()
    if source == "hackernews" and item_id.isdigit():
        return _canonical_url(f"https://news.ycombinator.com/item?id={item_id}")
    return ""


_TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "ref", "ref_src", "source", "share",
    }
)


def _canonical_url(raw: str) -> str:
    """Collapse tracking variants so one page is shown once across platforms."""
    text = str(raw or "").strip()
    if not text.startswith(("http://", "https://")):
        return text[:2048]
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return text[:2048]
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_QUERY_KEYS
        ]
    )
    return urlunparse(("https", host, path, "", query, ""))[:2048]


def _title_key(title: str) -> str:
    """Stable title fingerprint for cross-platform syndicated copies."""
    return re.sub(r"[^a-z0-9à-ỹđ]+", " ", str(title or "").casefold()).strip()


def _item_title(item: dict[str, Any]) -> str:
    for key in ("title", "snippet", "body", "text"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:512]
    return "(untitled)"


def _flatten_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    by_source = payload.get("items_by_source")
    if isinstance(by_source, dict):
        for source, rows in by_source.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                enriched = dict(row)
                enriched.setdefault("source", source)
                items.append(enriched)
    for key in ("items", "ranked_items", "candidates"):
        rows = payload.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    items.append(row)
    return items


def _engagement_blob(item: dict[str, Any]) -> dict[str, Any]:
    eng = item.get("engagement")
    if isinstance(eng, dict):
        return eng
    return {}


def persist_findings(
    research: Last30DaysResearch,
    payload: dict[str, Any],
    *,
    skip_existing_urls: bool = True,
    created_ids: list[int] | None = None,
) -> int:
    """Insert findings from a payload. Skips duplicate URLs when streaming.

    Returns the number of newly created findings. Pass ``created_ids`` to
    collect primary keys for async title translation.
    """
    items = _flatten_items(payload)
    clusters = payload.get("clusters") if isinstance(payload.get("clusters"), list) else []
    cluster_titles = {
        str(c.get("cluster_id") or ""): str(c.get("title") or "")[:512]
        for c in clusters
        if isinstance(c, dict)
    }

    existing_urls: set[str] = set()
    existing_titles: set[str] = set()
    if skip_existing_urls:
        existing_urls = set(
            _canonical_url(url)
            for url in research.findings.exclude(url="").values_list("url", flat=True)
        )
        existing_titles = {
            key
            for key in (
                _title_key(title)
                for title in research.findings.values_list("title", flat=True)
            )
            if len(key) >= 24
        }

    created = 0
    skipped_stale = 0
    seen_urls: set[str] = set(existing_urls)
    seen_titles: set[str] = set(existing_titles)
    cutoff = lookback_cutoff(research)
    for item in items:
        url = _item_url(item)
        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        source = str(item.get("source") or "unknown")[:64]
        title = _item_title(item)
        title_key = _title_key(title)
        eng = _engagement_blob(item)
        score = _as_float(
            item.get("local_rank_score")
            or item.get("engagement_score")
            or item.get("score")
            or 0
        )
        points = _as_int(eng.get("points") or eng.get("score") or eng.get("upvotes"))
        comments = _as_int(eng.get("comments") or eng.get("num_comments"))
        likes = _as_int(eng.get("likes") or eng.get("favorites"))
        composite = score * 1000 + points * 2 + comments + likes * 0.5

        author = str(item.get("author") or item.get("by") or "")[:255]
        snippet = str(item.get("snippet") or item.get("body") or "")[:2000]
        published_at = _parse_published(
            item.get("published_at") or item.get("date") or item.get("published")
        )
        # Hard age gate: never persist items older than the lookback window.
        if published_at is not None and published_at < cutoff:
            skipped_stale += 1
            continue
        # Syndicated copies often carry different platform URLs but the same
        # headline. Keep one representative item in the trend panel.
        if len(title_key) >= 24:
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
        host = ""
        if url:
            try:
                host = urlparse(url).netloc[:255]
            except Exception:  # noqa: BLE001
                host = ""

        cluster_id = str(item.get("cluster_id") or "")[:64]
        if not cluster_id and url:
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                ids = c.get("candidate_ids") or c.get("representative_ids") or []
                if any(_canonical_url(str(candidate)) == url for candidate in ids):
                    cluster_id = str(c.get("cluster_id") or "")[:64]
                    break

        row = Last30DaysFinding.objects.create(
            research=research,
            source=source,
            title=title,
            url=url,
            host=host,
            author=author,
            snippet=snippet,
            published_at=published_at,
            score=composite,
            engagement_score=_as_float(item.get("engagement_score")),
            relevance=_as_float(item.get("local_relevance") or item.get("relevance_hint")),
            freshness=_as_float(item.get("freshness")),
            cluster_id=cluster_id,
            cluster_title=cluster_titles.get(cluster_id, "")[:512],
            engagement=eng,
            metadata={
                k: item.get(k)
                for k in (
                    "item_id",
                    "container",
                    "date_confidence",
                    "metadata",
                    "engagement",
                )
                if k in item
            },
        )
        if created_ids is not None:
            created_ids.append(row.id)
        created += 1
    if skipped_stale:
        logger.info(
            "last30days research id=%s skipped %s stale items older than cutoff=%s",
            research.id,
            skipped_stale,
            cutoff.isoformat(),
        )
    return created


def _enqueue_title_translations(finding_ids: list[int]) -> None:
    if not finding_ids:
        return
    try:
        from .translate import enqueue_last30days_title_translations

        enqueue_last30days_title_translations(finding_ids)
    except Exception:  # noqa: BLE001
        logger.exception("last30days enqueue title translate failed")



def _set_progress(
    research: Last30DaysResearch,
    *,
    message: str,
    pct: int,
) -> None:
    research.progress = (message or "")[:255]
    research.progress_pct = max(0, min(100, int(pct)))
    research.save(update_fields=["progress", "progress_pct", "updated_at"])


def run_last30days_research(research: Last30DaysResearch) -> Last30DaysResearch:
    """Collect sources one-by-one; commit findings after each source."""
    research.status = Last30DaysResearch.Status.RUNNING
    research.started_at = timezone.now()
    research.error_message = ""
    research.progress = "Đang bắt đầu…"
    research.progress_pct = 0
    research.item_count = 0
    research.source_counts = {}
    research.errors_by_source = {}
    research.clusters = []
    research.raw_report = {"items_by_source": {}, "errors_by_source": {}}
    research.stderr_tail = ""
    research.brief_markdown = ""
    research.completed_at = None
    research.save(
        update_fields=[
            "status",
            "started_at",
            "error_message",
            "progress",
            "progress_pct",
            "item_count",
            "source_counts",
            "errors_by_source",
            "clusters",
            "raw_report",
            "stderr_tail",
            "brief_markdown",
            "completed_at",
            "updated_at",
        ]
    )
    research.findings.all().delete()

    requested = [
        str(s).strip().lower()
        for s in (research.sources or default_sources())
        if str(s).strip()
    ] or default_sources()
    # Drop GitHub (noisy false positives) and unknown tokens.
    known = set(available_collectors()) | {
        "hackernews",
        "polymarket",
        "reddit",
        "x",
        "web",
        "newsnow",
    }
    sources = [s for s in requested if s in known and s != "github"]
    # Auto-add Wigolo web grounding when enabled and not already listed.
    try:
        from django.conf import settings as dj_settings

        from apps.integrations.web_reader.wigolo import wigolo_configured

        if (
            wigolo_configured()
            and bool(getattr(dj_settings, "WIGOLO_LAST30DAYS_WEB", True))
            and "web" not in sources
        ):
            sources.append("web")
    except Exception:  # noqa: BLE001
        pass
    # NewsNow is a public ranked-feed source. Add it automatically when enabled
    # so existing LAST30DAYS_SOURCES settings gain the integration safely.
    if (
        bool(getattr(settings, "TREND_NEWSNOW_ENABLED", True))
        and "newsnow" in known
        and "newsnow" not in sources
    ):
        sources.append("newsnow")
    if not sources:
        sources = ["reddit", "x", "polymarket"]

    _set_progress(research, message="Đang hiểu chủ đề (Groq / từ điển)…", pct=2)
    plan = build_topic_plan(research.topic, use_groq=True)
    research.raw_report = {
        "items_by_source": {},
        "errors_by_source": {},
        "topic_plan": plan.as_dict(),
    }
    research.save(update_fields=["raw_report", "updated_at"])
    if plan.groq_used:
        _set_progress(
            research,
            message=f"Chủ đề ≈ {plan.english} — bắt đầu thu thập…",
            pct=5,
        )
    else:
        _set_progress(
            research,
            message=f"Chủ đề ≈ {plan.english} (lexicon) — bắt đầu thu thập…",
            pct=5,
        )

    started = time.monotonic()
    ok_sources = 0
    failed_sources = 0
    total = len(sources)

    for idx, source in enumerate(sources, start=1):
        pct_start = 5 + int(((idx - 1) / total) * 90)
        _set_progress(
            research,
            message=f"Đang lấy {source} ({idx}/{total})…",
            pct=pct_start,
        )
        try:
            items = collect_source(
                source,
                research.topic,
                days=research.lookback_days,
                depth=research.depth,
                plan=plan,
            )
        except Exception as exc:  # noqa: BLE001
            failed_sources += 1
            err = str(exc)[:500]
            errors = dict(research.errors_by_source or {})
            errors[source] = err
            research.errors_by_source = errors
            raw = dict(research.raw_report or {})
            raw.setdefault("errors_by_source", {})[source] = err
            research.raw_report = raw
            research.stderr_tail = (research.stderr_tail + f"\n[{source}] {err}")[-4000:]
            research.duration_ms = int((time.monotonic() - started) * 1000)
            research.save(
                update_fields=[
                    "errors_by_source",
                    "raw_report",
                    "stderr_tail",
                    "duration_ms",
                    "updated_at",
                ]
            )
            logger.warning(
                "last30days research id=%s source=%s failed: %s",
                research.id,
                source,
                err[:200],
            )
            continue

        ok_sources += 1
        new_ids: list[int] = []
        created = persist_findings(
            research,
            {"items_by_source": {source: items}, "clusters": []},
            skip_existing_urls=True,
            created_ids=new_ids,
        )
        _enqueue_title_translations(new_ids)
        try:
            from .enrich import enqueue_finding_enrichment

            enqueue_finding_enrichment(new_ids)
        except Exception:  # noqa: BLE001
            logger.exception("last30days enqueue enrich failed")
        counts = dict(research.source_counts or {})
        counts[source] = research.findings.filter(source=source).count()
        research.source_counts = counts
        research.item_count = research.findings.count()
        raw = dict(research.raw_report or {})
        raw.setdefault("items_by_source", {})[source] = items[:120]
        raw["topic_plan"] = plan.as_dict()
        research.raw_report = raw
        research.duration_ms = int((time.monotonic() - started) * 1000)
        pct_done = 5 + int((idx / total) * 90)
        research.progress = f"Xong {source} ({idx}/{total}) — {research.item_count} mục"
        research.progress_pct = pct_done
        research.save(
            update_fields=[
                "source_counts",
                "item_count",
                "raw_report",
                "duration_ms",
                "progress",
                "progress_pct",
                "updated_at",
            ]
        )
        logger.info(
            "last30days research id=%s source=%s +%s items total=%s pct=%s",
            research.id,
            source,
            created,
            research.item_count,
            pct_done,
        )

    research.duration_ms = int((time.monotonic() - started) * 1000)
    research.item_count = research.findings.count()
    research.completed_at = timezone.now()

    if ok_sources == 0:
        research.status = Last30DaysResearch.Status.FAILED
        research.progress = "Thất bại"
        research.progress_pct = 0
        research.error_message = (
            (research.errors_by_source and next(iter(research.errors_by_source.values())))
            or "All sources failed"
        )[:2000]
    elif failed_sources > 0 and research.item_count > 0:
        research.status = Last30DaysResearch.Status.PARTIAL
        research.progress = (
            f"Hoàn thành một phần — {ok_sources}/{total} nguồn OK, "
            f"{research.item_count} mục"
        )
        # Progress % reflects successful sources, not a fake 100%.
        research.progress_pct = max(1, int(round(100 * ok_sources / max(total, 1))))
    elif research.item_count == 0:
        research.status = Last30DaysResearch.Status.COMPLETED
        research.progress = "Hoàn thành — không có mục"
        research.progress_pct = 100
        research.error_message = "No items found in the lookback window."
    else:
        research.status = Last30DaysResearch.Status.COMPLETED
        research.progress = "Hoàn thành"
        research.progress_pct = 100

    research.save(
        update_fields=[
            "status",
            "error_message",
            "progress",
            "progress_pct",
            "duration_ms",
            "item_count",
            "completed_at",
            "updated_at",
        ]
    )

    # Multi-dimensional Vietnamese brief grounded in collected findings.
    if research.item_count > 0 and research.status in {
        Last30DaysResearch.Status.COMPLETED,
        Last30DaysResearch.Status.PARTIAL,
    }:
        try:
            from .brief import brief_enabled, synthesize_research_brief

            if brief_enabled():
                _set_progress(
                    research,
                    message="Đang tổng hợp báo cáo xu hướng…",
                    pct=min(99, max(research.progress_pct, 92)),
                )
                brief_out = synthesize_research_brief(research)
                research.refresh_from_db()
                if brief_out.get("ok"):
                    if research.status == Last30DaysResearch.Status.PARTIAL:
                        research.progress = (
                            f"Hoàn thành một phần — {ok_sources}/{total} nguồn OK, "
                            f"{research.item_count} mục · đã có báo cáo"
                        )
                        research.progress_pct = max(
                            1, int(round(100 * ok_sources / max(total, 1)))
                        )
                    else:
                        research.progress = "Hoàn thành · đã có báo cáo"
                        research.progress_pct = 100
                    research.save(
                        update_fields=["progress", "progress_pct", "updated_at"]
                    )
                logger.info(
                    "last30days brief research=%s ok=%s chars=%s provider=%s",
                    research.id,
                    brief_out.get("ok"),
                    brief_out.get("chars"),
                    brief_out.get("provider"),
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "last30days brief synthesis failed research=%s", research.id
            )

    logger.info(
        "last30days research id=%s status=%s items=%s ms=%s",
        research.id,
        research.status,
        research.item_count,
        research.duration_ms,
    )
    return research
