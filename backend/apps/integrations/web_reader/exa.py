"""Exa semantic search — aligned with https://docs.exa.ai/reference/search-api-guide-for-coding-agents

Default pattern (coding-agent / CTI retrieval):
  type=auto, contents.highlights=true, inspect results[] directly.
Do not combine highlights+text unless EXA_INCLUDE_TEXT is explicitly enabled.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.integrations.searx.client import is_github_host_url

logger = logging.getLogger(__name__)

EXA_ENDPOINT = "https://api.exa.ai/search"
DEFAULT_TIMEOUT = 35.0
MAX_RESULTS = 25
_ALLOWED_TYPES = frozenset(
    {"auto", "fast", "instant", "deep-lite", "deep", "deep-reasoning"}
)
# excludeDomains + date filters → 400 with these categories (Exa docs).
_NO_FILTER_CATEGORIES = frozenset({"company", "people"})


def exa_configured() -> bool:
    return bool((getattr(settings, "EXA_API_KEY", "") or "").strip())


_EXA_MODES = frozenset({"fallback", "always", "off"})


def normalize_exa_mode(raw: str | None) -> str:
    mode = str(raw or "fallback").strip().lower()
    return mode if mode in _EXA_MODES else "fallback"


def should_call_exa(
    *,
    mode: str | None = None,
    kept_hits: int = 0,
    min_hits: int | None = None,
    force: bool = False,
    configured: bool | None = None,
    purpose: str = "osint",
) -> bool:
    """
    Credit-frugal gate for Exa API calls.

    Modes (EXA_OSINT_MODE / EXA_LEAK_MODE):
      - fallback (default): call only when other channels kept fewer than min_hits
      - always: call whenever configured
      - off: never call (force is ignored — set mode to always/fallback to spend credits)

    force=True (OSINT use_exa): overrides the fallback threshold when mode is not off.
    """
    if configured is None:
        configured = exa_configured()
    if not configured:
        return False

    purpose_key = (purpose or "osint").strip().lower()
    if purpose_key == "leak":
        default_mode = getattr(settings, "EXA_LEAK_MODE", "fallback")
        default_min = getattr(settings, "EXA_LEAK_MIN_HITS", 5)
    else:
        default_mode = getattr(settings, "EXA_OSINT_MODE", "fallback")
        default_min = getattr(settings, "EXA_OSINT_MIN_HITS", 5)

    resolved = normalize_exa_mode(mode if mode is not None else default_mode)
    if resolved == "off":
        return False
    if force or resolved == "always":
        return True

    threshold = (
        int(min_hits)
        if min_hits is not None
        else max(0, int(default_min or 0))
    )
    return int(kept_hits or 0) < threshold


def build_exa_queries(keyword: str, *, max_queries: int | None = None) -> list[str]:
    """
    Natural-language queries (Exa neural search — avoid Boolean/dork syntax).
    """
    kw = " ".join((keyword or "").split()).strip()
    if not kw:
        return []
    if len(kw) >= 2 and kw[0] == '"' and kw[-1] == '"':
        kw = kw[1:-1].strip()
    if not kw:
        return []
    cap = max(
        1,
        min(
            int(
                max_queries
                if max_queries is not None
                else getattr(settings, "EXA_QUERY_COUNT", 1) or 1
            ),
            4,
        ),
    )
    pack = [
        f"Latest news on data breaches {kw}",
        f"{kw} ransomware attack credential leak password dump",
        f"{kw} leaked database exposed API key secret",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for query in pack:
        if query not in seen:
            seen.add(query)
            out.append(query[:400])
        if len(out) >= cap:
            break
    return out


def _search_type() -> str:
    raw = str(getattr(settings, "EXA_SEARCH_TYPE", "auto") or "auto").strip().lower()
    return raw if raw in _ALLOWED_TYPES else "auto"


def _exclude_domains() -> list[str]:
    raw = getattr(settings, "EXA_EXCLUDE_DOMAINS", "") or ""
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip().lower() for x in raw]
    else:
        items = [p.strip().lower() for p in str(raw).split(",")]
    defaults = ["github.com", "www.github.com", "gist.github.com"]
    out: list[str] = []
    seen: set[str] = set()
    for host in [*defaults, *items]:
        host = host.strip().lower().lstrip(".")
        if not host or host in seen:
            continue
        seen.add(host)
        out.append(host)
    return out[:40]


def _include_domains() -> list[str]:
    raw = getattr(settings, "EXA_INCLUDE_DOMAINS", "") or ""
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip().lower() for x in raw]
    else:
        items = [p.strip().lower() for p in str(raw).split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for host in items:
        host = host.strip().lower().lstrip(".")
        if not host or host in seen:
            continue
        seen.add(host)
        out.append(host)
    return out[:40]


def _resolve_category(category: str | None) -> str:
    cat = (
        str(category).strip().lower()
        if category is not None
        else str(getattr(settings, "EXA_CATEGORY", "") or "").strip().lower()
    )
    if cat in {
        "news",
        "company",
        "people",
        "research paper",
        "tweet",
        "personal site",
        "financial report",
    }:
        return cat
    return ""


def _hit_content(item: dict[str, Any]) -> str:
    """Prefer highlights (token-efficient), then text / summary."""
    parts: list[str] = []
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        for h in highlights:
            if isinstance(h, str) and h.strip():
                parts.append(h.strip())
            elif isinstance(h, dict):
                text = str(h.get("text") or h.get("snippet") or "").strip()
                if text:
                    parts.append(text)
    if parts:
        return "\n".join(parts)[:4000]

    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()[:4000]
    if isinstance(text, dict):
        nested = str(text.get("text") or "").strip()
        if nested:
            return nested[:4000]

    summary = item.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()[:4000]
    return ""


def _normalize_hit(item: dict[str, Any]) -> dict[str, Any] | None:
    url = str(item.get("url") or "").strip()
    if not url or is_github_host_url(url):
        return None
    host = (urlparse(url).hostname or "").lower()
    for blocked in _exclude_domains():
        if host == blocked or host.endswith("." + blocked):
            return None
    content = _hit_content(item)
    title = str(item.get("title") or url)[:512]
    published = str(
        item.get("publishedDate") or item.get("published_date") or item.get("date") or ""
    )[:128]
    score = item.get("score")
    return {
        "title": title,
        "url": url[:2048],
        "content": content,
        "engine": "exa",
        "score": score,
        "published": published,
    }


def _build_contents(
    *,
    phrase: str | None,
    term: str,
    purpose: str,
) -> dict[str, Any]:
    """
    Official default: contents.highlights = true.
    Leak hunts guide highlights toward the keyword; Wire keeps boolean true.
    """
    contents: dict[str, Any] = {}
    if bool(getattr(settings, "EXA_HIGHLIGHTS", True)):
        guide = " ".join((phrase or "").split()).strip()[:200]
        force_guide = bool(getattr(settings, "EXA_HIGHLIGHTS_GUIDE", False))
        if purpose == "leak" and guide:
            contents["highlights"] = {"query": guide}
        elif force_guide and guide:
            contents["highlights"] = {"query": guide}
        else:
            contents["highlights"] = True
    if bool(getattr(settings, "EXA_INCLUDE_TEXT", False)):
        text_chars = max(
            500,
            min(int(getattr(settings, "EXA_TEXT_MAX_CHARS", 2000) or 2000), 20000),
        )
        contents["text"] = {"maxCharacters": text_chars, "verbosity": "compact"}

    max_age = getattr(settings, "EXA_MAX_AGE_HOURS", None)
    if max_age is not None and str(max_age).strip() != "":
        try:
            hours = int(max_age)
            if hours >= -1:
                contents["maxAgeHours"] = hours
        except (TypeError, ValueError):
            pass

    return contents or {"highlights": True}


def _rank_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer dated + higher Exa score (then keep relative order)."""
    from apps.integrations.web_reader.recency import hit_recency_ts

    indexed = list(enumerate(hits))

    def _key(pair: tuple[int, dict[str, Any]]) -> tuple:
        idx, hit = pair
        score = hit.get("score")
        try:
            score_f = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_f = 0.0
        return (hit_recency_ts(hit), score_f, -idx)

    indexed.sort(key=_key, reverse=True)
    return [h for _, h in indexed]


def search_exa(
    query: str,
    *,
    limit: int = 10,
    phrase: str | None = None,
    category: str | None = None,
    include_domains: list[str] | None = None,
    recency_days: int | None = None,
    purpose: str = "leak",
    require_phrase: bool | None = None,
) -> list[dict[str, Any]]:
    """Return Searx-shaped hits from Exa (results + highlights)."""
    return search_exa_detail(
        query,
        limit=limit,
        phrase=phrase,
        category=category,
        include_domains=include_domains,
        recency_days=recency_days,
        purpose=purpose,
        require_phrase=require_phrase,
    ).get("hits") or []


def search_exa_detail(
    query: str,
    *,
    limit: int = 10,
    phrase: str | None = None,
    category: str | None = None,
    include_domains: list[str] | None = None,
    recency_days: int | None = None,
    purpose: str = "leak",
    require_phrase: bool | None = None,
) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "hits": [],
        "error": None,
        "configured": exa_configured(),
    }
    key = (getattr(settings, "EXA_API_KEY", "") or "").strip()
    if not key:
        empty["error"] = "not_configured"
        return empty
    term = " ".join((query or "").split()).strip()
    if not term:
        empty["error"] = "empty_query"
        return empty

    purpose = (purpose or "leak").strip().lower()
    if purpose not in {"leak", "wire", "site"}:
        purpose = "leak"

    # Profiles tuned for NewsCrawler channels.
    if purpose == "wire" and category is None:
        category = "news"
    if purpose == "site" and category is None:
        category = "news"

    limit = max(1, min(int(limit or 10), MAX_RESULTS))
    # Over-fetch then filter/rank so phrase / github cuts don't starve results.
    fetch_n = limit if purpose == "wire" else min(MAX_RESULTS, max(limit, limit + 4))

    cat = _resolve_category(category)
    filters_ok = cat not in _NO_FILTER_CATEGORIES

    body: dict[str, Any] = {
        "query": term[:400],
        "type": _search_type(),
        "numResults": fetch_n,
        "contents": _build_contents(phrase=phrase, term=term, purpose=purpose),
    }
    if cat:
        body["category"] = cat

    if filters_ok:
        exclude = _exclude_domains()
        if exclude:
            body["excludeDomains"] = exclude
        days = recency_days
        if days is None:
            if purpose == "wire":
                days = int(getattr(settings, "EXA_WIRE_MAX_AGE_DAYS", 30) or 30)
            else:
                days = int(getattr(settings, "EXA_RECENCY_DAYS", 90) or 0)
        days = int(days or 0)
        if days > 0:
            start = datetime.now(timezone.utc) - timedelta(days=min(days, 3650))
            body["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    include = include_domains if include_domains is not None else _include_domains()
    if include:
        body["includeDomains"] = [
            str(h).strip().lower() for h in include if str(h).strip()
        ][:40]

    # Leak hunts: anchor keyword in page text when short (reduces semantic drift).
    must = " ".join((phrase or "").split()).strip()
    use_include_text = (
        require_phrase
        if require_phrase is not None
        else (
            purpose == "leak"
            and bool(getattr(settings, "EXA_REQUIRE_PHRASE", True))
            and bool(must)
        )
    )
    if use_include_text and must and len(must) <= 64:
        if not must.lower().startswith("latest news"):
            body["includeText"] = [must[:64]]

    timeout = float(getattr(settings, "EXA_TIMEOUT", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    if _search_type().startswith("deep"):
        timeout = max(timeout, 60.0)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                EXA_ENDPOINT,
                headers={
                    "x-api-key": key,
                    "Content-Type": "application/json",
                    "User-Agent": "NewsCrawler/1.0",
                },
                json=body,
            )
            if response.status_code in {401, 403}:
                empty["error"] = f"HTTP {response.status_code} — check EXA_API_KEY"
                return empty
            if response.status_code == 429:
                empty["error"] = "rate_limited"
                return empty
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Exa search failed: %s", exc)
        empty["error"] = str(exc)[:160]
        return empty
    except ValueError as exc:
        logger.warning("Exa JSON decode failed: %s", exc)
        empty["error"] = "bad_json"
        return empty

    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        empty["error"] = "no_results"
        return empty

    from apps.integrations.web_reader.phrase import contains_phrase

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        hit = _normalize_hit(item)
        if not hit:
            continue
        url = hit["url"]
        if url in seen:
            continue
        # Local phrase check for leak OSINT (highlights/title must mention keyword).
        if purpose == "leak" and must and len(must) >= 2:
            blob = f"{hit.get('title') or ''}\n{hit.get('content') or ''}"
            if blob.strip() and not contains_phrase(blob, must):
                continue
        seen.add(url)
        hits.append(hit)

    hits = _rank_hits(hits)[:limit]
    return {"hits": hits, "error": None, "configured": True}


def discover_exa_hits(keyword: str, *, limit: int = 15) -> list[dict[str, Any]]:
    """Run 1–N NL Exa queries for a watch/OSINT keyword; dedupe by URL."""
    if not exa_configured():
        return []
    queries = build_exa_queries(keyword)
    if not queries:
        return []
    per = max(3, min(limit, MAX_RESULTS) // max(1, len(queries)) + 2)
    phrase = " ".join((keyword or "").split()).strip().strip('"')
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for query in queries:
        for hit in search_exa(
            query,
            limit=per,
            phrase=phrase,
            purpose="leak",
        ):
            url = hit.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(hit)
            if len(out) >= limit:
                return _rank_hits(out)
    return _rank_hits(out)


def doctor_exa() -> dict[str, Any]:
    ok = exa_configured()
    mode = normalize_exa_mode(getattr(settings, "EXA_OSINT_MODE", "fallback"))
    return {
        "configured": ok,
        "ok": ok,
        "detail": (
            f"type={_search_type()} mode={mode} contents=highlights"
            f"{'+text' if getattr(settings, 'EXA_INCLUDE_TEXT', False) else ''}"
            if ok
            else "Set EXA_API_KEY to enable"
        ),
    }
