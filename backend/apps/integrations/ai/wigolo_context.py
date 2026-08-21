"""Wigolo open-web evidence for AI briefings (retrieval only; Groq still synthesizes)."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "after",
        "before",
        "about",
        "china",
        "chinese",
        "military",
        "defence",
        "defense",
        "news",
        "report",
        "update",
    }
)


def wigolo_briefing_enabled() -> bool:
    if not bool(getattr(settings, "WIGOLO_BRIEFING_ENABLED", True)):
        return False
    from apps.integrations.web_reader.wigolo import wigolo_configured

    return wigolo_configured()


def _max_hits() -> int:
    return max(2, min(int(getattr(settings, "WIGOLO_BRIEFING_MAX_HITS", 8) or 8), 15))


def _mode() -> str:
    mode = str(getattr(settings, "WIGOLO_BRIEFING_MODE", "search") or "search").lower()
    return mode if mode in {"search", "research", "off"} else "search"


def _theme_queries_from_titles(titles: list[str], *, limit: int = 3) -> list[str]:
    """Build a few search queries from Wire titles (lexical, no LLM)."""
    from collections import Counter

    bag: Counter[str] = Counter()
    for title in titles:
        tokens = re.findall(r"[A-Za-zÀ-ỹ一-鿿]{3,}", title or "")
        for tok in tokens:
            key = tok.casefold()
            if key in _STOP or len(key) < 3:
                continue
            bag[key] += 1
    themes = [w for w, _ in bag.most_common(8)]
    if not themes:
        return ["Indo-Pacific military developments last 24 hours"]
    # Prefer multi-word phrases from top titles.
    queries: list[str] = []
    for title in titles[:6]:
        compact = " ".join((title or "").split())
        if len(compact) >= 12:
            queries.append(compact[:120])
        if len(queries) >= limit:
            break
    if len(queries) < limit and themes:
        queries.append(" ".join(themes[:4]))
    return queries[:limit] or ["China PLA maritime military news"]


def collect_wigolo_search_evidence(
    queries: list[str] | str,
    *,
    limit: int | None = None,
    time_range: str = "week",
    category: str = "news",
) -> list[dict[str, Any]]:
    """Multi-query Wigolo search → normalized evidence rows."""
    if not wigolo_briefing_enabled() or _mode() == "off":
        return []
    from apps.integrations.web_reader.wigolo import search_wigolo

    qlist = (
        [queries]
        if isinstance(queries, str)
        else [str(q).strip() for q in (queries or []) if str(q).strip()]
    )
    if not qlist:
        return []
    cap = limit if limit is not None else _max_hits()
    try:
        hits = search_wigolo(
            qlist[:4] if len(qlist) > 1 else qlist,
            limit=cap,
            category=category,
            time_range=time_range,
            search_depth=str(
                getattr(settings, "WIGOLO_BRIEFING_SEARCH_DEPTH", "deep") or "deep"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("wigolo briefing search failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for hit in hits:
        title = str(hit.get("title") or "").strip()
        url = str(hit.get("url") or "").strip()
        snippet = str(hit.get("content") or hit.get("snippet") or "").strip()
        if not url or not (title or snippet):
            continue
        out.append(
            {
                "title": title[:300],
                "url": url[:2048],
                "snippet": snippet[:800],
                "published": str(hit.get("published") or "")[:64],
                "score": hit.get("score"),
            }
        )
    return out[:cap]


def collect_wigolo_research_brief(
    question: str, *, depth: str = "quick"
) -> dict[str, Any]:
    """Optional deeper Wigolo research (slower). Returns markdown + meta."""
    if not wigolo_briefing_enabled() or _mode() != "research":
        return {"ok": False, "skipped": True}
    from apps.integrations.web_reader.wigolo import research_wigolo

    q = " ".join((question or "").split()).strip()
    if len(q) < 4:
        return {"ok": False, "error": "empty question"}
    try:
        return research_wigolo(q, depth=depth if depth in {"quick", "standard"} else "quick")
    except Exception as exc:  # noqa: BLE001
        logger.warning("wigolo briefing research failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


def format_wigolo_evidence_block(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return ""
    lines = [
        "",
        "Open-web bổ sung (chỉ đối chiếu với Dòng tin; không bịa thêm):",
    ]
    for idx, row in enumerate(evidence, start=1):
        title = row.get("title") or "(untitled)"
        url = row.get("url") or ""
        snip = row.get("snippet") or ""
        pub = row.get("published") or ""
        lines.append(f"{idx}) {title}")
        if pub:
            lines.append(f"   ngày: {pub}")
        if snip:
            lines.append(f"   trích: {snip[:320]}")
        if url:
            lines.append(f"   url: {url}")
    lines.append(
        "Chỉ dùng open-web để củng cố hoặc làm rõ tin Dòng tin. "
        "Nếu mâu thuẫn với Wire, nêu rõ và giữ độ tin cậy thấp."
    )
    return "\n".join(lines)


def gather_briefing_web_context(
    *,
    keyword: str | None = None,
    threat_titles: list[str] | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    """
    Best-effort open-web context for AI briefings.

    Prefer fast multi-engine search. Research mode only when explicitly configured
    (keyword digests benefit most).
    """
    if not wigolo_briefing_enabled() or _mode() == "off":
        return {"enabled": False, "evidence": [], "research": None}

    time_range = "day" if window_hours <= 36 else ("week" if window_hours <= 200 else "month")
    evidence: list[dict[str, Any]] = []
    research: dict[str, Any] | None = None

    if keyword:
        kw = " ".join(keyword.split()).strip()
        queries = [
            kw,
            f"{kw} military OR PLA OR navy OR coast guard",
            f"{kw} Indo-Pacific OR South China Sea OR Taiwan",
        ]
        evidence = collect_wigolo_search_evidence(
            queries[:3], time_range=time_range, category="news"
        )
        if _mode() == "research" and len(evidence) < 3:
            research = collect_wigolo_research_brief(
                f"Recent military/defense developments related to: {kw}",
                depth="quick",
            )
    else:
        titles = [t for t in (threat_titles or []) if t]
        queries = _theme_queries_from_titles(titles, limit=3)
        # Daily brief: keep search cheap; skip research by default.
        evidence = collect_wigolo_search_evidence(
            queries, time_range=time_range, category="news"
        )

    return {
        "enabled": True,
        "mode": _mode(),
        "evidence": evidence,
        "research": research if research and research.get("ok") else None,
        "query_count": len(evidence),
    }
