"""Wigolo sidecar client — local-first multi-engine search + fetch.

REST surface: POST {WIGOLO_URL}/v1/search|fetch|research
See https://github.com/KnockOutEZ/wigolo
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.integrations.searx.client import is_github_host_url

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 45.0
_FETCH_TIMEOUT = 90.0
_RESEARCH_TIMEOUT = 300.0


def wigolo_enabled() -> bool:
    return bool(getattr(settings, "WIGOLO_ENABLED", True))


def wigolo_base_url() -> str:
    return (getattr(settings, "WIGOLO_URL", "") or "").strip().rstrip("/")


def wigolo_configured() -> bool:
    return wigolo_enabled() and bool(wigolo_base_url())


def wigolo_fetch_enabled() -> bool:
    return wigolo_configured() and bool(
        getattr(settings, "WIGOLO_FETCH_ENABLED", True)
    )


def _api_token() -> str:
    return (getattr(settings, "WIGOLO_API_TOKEN", "") or "").strip()


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _timeout(default: float) -> float:
    return float(getattr(settings, "WIGOLO_TIMEOUT_SEC", default) or default)


_WIGOLO_MODES = frozenset({"fallback", "always", "off"})


def normalize_wigolo_mode(raw: str | None) -> str:
    mode = str(raw or "fallback").strip().lower()
    return mode if mode in _WIGOLO_MODES else "fallback"


def should_call_wigolo(
    *,
    mode: str | None = None,
    kept_hits: int = 0,
    min_hits: int | None = None,
    force: bool = False,
    configured: bool | None = None,
    purpose: str = "osint",
) -> bool:
    """
    Gate Wigolo like Exa — prefer free/cheap channels first.

    Modes (WIGOLO_OSINT_MODE / WIGOLO_LEAK_MODE / WIGOLO_DOCUMENT_MODE):
      - fallback (default): call only when kept_hits < min_hits
      - always: call whenever configured
      - off: never call
    """
    if configured is None:
        configured = wigolo_configured()
    if not configured:
        return False

    purpose_key = (purpose or "osint").strip().lower()
    if purpose_key == "leak":
        default_mode = getattr(settings, "WIGOLO_LEAK_MODE", "fallback")
        default_min = getattr(settings, "WIGOLO_LEAK_MIN_HITS", 5)
    elif purpose_key == "document":
        default_mode = getattr(settings, "WIGOLO_DOCUMENT_MODE", "fallback")
        default_min = getattr(settings, "WIGOLO_DOCUMENT_MIN_HITS", 3)
    else:
        default_mode = getattr(settings, "WIGOLO_OSINT_MODE", "fallback")
        default_min = getattr(settings, "WIGOLO_OSINT_MIN_HITS", 5)

    resolved = normalize_wigolo_mode(mode if mode is not None else default_mode)
    if resolved == "off":
        return False
    if force or resolved == "always":
        return True
    floor = int(min_hits if min_hits is not None else default_min or 5)
    return int(kept_hits or 0) < max(0, floor)


def _post(tool: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    base = wigolo_base_url()
    if not base:
        return {}
    url = f"{base}/v1/{tool}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(url, json=payload, headers=_headers())
            if response.status_code >= 400:
                logger.warning(
                    "wigolo %s HTTP %s: %s",
                    tool,
                    response.status_code,
                    (response.text or "")[:240],
                )
                return {}
            data = response.json()
            return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("wigolo %s failed: %s", tool, exc)
        return {}


def build_wigolo_queries(keyword: str, *, max_queries: int | None = None) -> list[str]:
    """Parallel query variants (mirrors Exa pack style, without Exa credit)."""
    raw = " ".join((keyword or "").split()).strip().strip('"').strip("'")
    if not raw:
        return []
    cap = max(
        1,
        min(
            int(
                max_queries
                if max_queries is not None
                else getattr(settings, "WIGOLO_QUERY_COUNT", 2) or 2
            ),
            6,
        ),
    )
    variants = [
        raw,
        f"Latest news on {raw}",
        f"{raw} breach OR leak OR ransomware",
        f"{raw} cybersecurity OR threat",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for q in variants:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= cap:
            break
    return out


def _norm_result(item: dict[str, Any]) -> dict[str, Any] | None:
    url = str(item.get("url") or "").strip()
    if not url.startswith("http"):
        return None
    if is_github_host_url(url):
        return None
    title = str(item.get("title") or url)[:512]
    snippet = str(
        item.get("snippet")
        or item.get("excerpt")
        or item.get("content")
        or item.get("text")
        or ""
    )[:4000]
    score = item.get("relevance_score")
    if score is None:
        ev = item.get("evidence_score")
        if isinstance(ev, dict):
            score = ev.get("final")
    published = ""
    fresh = item.get("freshness_signal")
    if isinstance(fresh, dict):
        published = str(fresh.get("published") or fresh.get("published_date") or "")
    if not published:
        published = str(item.get("published") or item.get("published_date") or "")
    return {
        "title": title,
        "url": url[:2048],
        "content": snippet,
        "engine": "wigolo",
        "score": score,
        "published": published[:128],
        "evidence_score": item.get("evidence_score")
        if isinstance(item.get("evidence_score"), dict)
        else {},
    }


def search_wigolo(
    query: str | list[str],
    *,
    limit: int = 10,
    category: str = "news",
    time_range: str | None = "month",
    search_depth: str | None = None,
    include_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Multi-engine search via Wigolo; returns NewsCrawler hit dicts.

    Prefer query *arrays* for parallel breadth (Wigolo fans variants out).
    Default depth comes from WIGOLO_SEARCH_DEPTH (deep recommended).
    """
    if not wigolo_configured():
        return []
    max_results = max(1, min(int(limit or 10), 20))
    if isinstance(query, str):
        q_payload: str | list[str] = query
    else:
        q_payload = [str(q).strip() for q in query if str(q).strip()][:10]
        if not q_payload:
            return []
        if len(q_payload) == 1:
            q_payload = q_payload[0]
    depth = (
        search_depth
        if search_depth
        else str(getattr(settings, "WIGOLO_SEARCH_DEPTH", "deep") or "deep")
    )
    body: dict[str, Any] = {
        "query": q_payload,
        "max_results": max_results,
        "category": category or "general",
        "search_depth": depth,
    }
    if time_range:
        body["time_range"] = time_range
    if include_domains:
        body["include_domains"] = list(include_domains)[:20]
    # Deep searches need a longer HTTP budget.
    timeout = _timeout(_DEFAULT_TIMEOUT)
    if depth == "deep":
        timeout = max(timeout, 75.0)
    data = _post("search", body, timeout=timeout)
    rows = data.get("results") if isinstance(data.get("results"), list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        hit = _norm_result(row)
        if not hit:
            continue
        key = hit["url"]
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
        if len(out) >= max_results:
            break
    return out


def discover_wigolo_hits(keyword: str, *, limit: int = 15) -> list[dict[str, Any]]:
    """OSINT/leak discovery: fan out query variants, dedupe by URL."""
    if not wigolo_configured():
        return []
    queries = build_wigolo_queries(keyword)
    if not queries:
        return []
    # Always pass a query array when possible → parallel breadth inside Wigolo.
    hits = search_wigolo(
        queries,
        limit=limit,
        category=str(getattr(settings, "WIGOLO_CATEGORY", "news") or "news"),
        time_range=str(getattr(settings, "WIGOLO_TIME_RANGE", "month") or "month")
        or None,
        search_depth=str(getattr(settings, "WIGOLO_SEARCH_DEPTH", "deep") or "deep"),
    )
    return hits[: max(1, min(int(limit or 15), 40))]


def discover_wigolo_document_hits(
    keyword: str, *, limit: int = 10, filetype: str = "pdf"
) -> list[dict[str, Any]]:
    """Document-scan fallback: filetype-biased web search via Wigolo."""
    if not wigolo_configured():
        return []
    ft = (filetype or "pdf").strip().lstrip(".").lower() or "pdf"
    raw = " ".join((keyword or "").split()).strip()
    if not raw:
        return []
    queries = [
        f"{raw} filetype:{ft}",
        f"{raw} {ft}",
        f'"{raw}" {ft} report OR whitepaper OR analysis',
    ]
    # Docs lookups: deep + prefer PDF-ish hosts when known.
    hits = search_wigolo(
        queries[:3],
        limit=limit,
        category="docs",
        time_range=str(getattr(settings, "WIGOLO_DOCUMENT_TIME_RANGE", "year") or "year")
        or None,
        search_depth="deep",
        include_domains=_document_include_domains(),
    )
    # Prefer URLs that look like documents.
    ranked: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for hit in hits:
        url = (hit.get("url") or "").lower()
        path = urlparse(url).path
        if path.endswith(f".{ft}") or f".{ft}?" in url or f"/{ft}" in path:
            ranked.append(hit)
        else:
            rest.append(hit)
    return (ranked + rest)[: max(1, min(int(limit or 10), 30))]


def _document_include_domains() -> list[str] | None:
    raw = (getattr(settings, "WIGOLO_DOCUMENT_INCLUDE_DOMAINS", "") or "").strip()
    if not raw:
        return None
    return [d.strip() for d in raw.split(",") if d.strip()][:20]


def fetch_wigolo(
    url: str,
    *,
    max_chars: int | None = None,
    render_js: str | bool | None = "auto",
) -> dict[str, Any]:
    """
    Fetch clean markdown/text for a URL via Wigolo tiered router.

    Returns {ok, text, title, error, backend}.
    """
    target = (url or "").strip()
    if not wigolo_fetch_enabled():
        return {
            "ok": False,
            "text": "",
            "title": "",
            "error": "wigolo fetch disabled",
            "backend": "wigolo",
        }
    if not target.startswith("http"):
        return {
            "ok": False,
            "text": "",
            "title": "",
            "error": "invalid url",
            "backend": "wigolo",
        }
    cap = max(
        2000,
        min(
            int(
                max_chars
                if max_chars is not None
                else getattr(settings, "WIGOLO_FETCH_MAX_CHARS", 12000) or 12000
            ),
            100_000,
        ),
    )
    if render_js is None:
        js_mode = "auto"
    elif isinstance(render_js, bool):
        js_mode = "always" if render_js else "never"
    else:
        js_mode = str(render_js).strip().lower() or "auto"
        if js_mode in {"1", "yes", "on", "true"}:
            js_mode = "always"
        elif js_mode in {"0", "no", "off", "false"}:
            js_mode = "never"
        elif js_mode not in {"auto", "always", "never"}:
            js_mode = "auto"
    data = _post(
        "fetch",
        {
            "url": target,
            "render_js": js_mode,
            "max_content_chars": cap,
        },
        timeout=_timeout(_FETCH_TIMEOUT),
    )
    if not data:
        return {
            "ok": False,
            "text": "",
            "title": "",
            "error": "empty response",
            "backend": "wigolo",
        }
    # Honest failure labels from Wigolo.
    err = str(data.get("error") or data.get("error_reason") or "")
    if err or data.get("blocked_by_challenge"):
        return {
            "ok": False,
            "text": "",
            "title": str(data.get("title") or "")[:512],
            "error": err or "blocked_by_challenge",
            "backend": "wigolo",
        }
    text = str(
        data.get("markdown") or data.get("text") or data.get("content") or ""
    ).strip()
    title = str(data.get("title") or "")[:512]
    if not text:
        return {
            "ok": False,
            "text": "",
            "title": title,
            "error": "empty body",
            "backend": "wigolo",
        }
    from apps.integrations.web_reader.article_text import extract_article_text

    cleaned = extract_article_text(text, title_hint=title, max_chars=cap)
    body = str(cleaned.get("text") or "")
    if not body:
        return {
            "ok": False,
            "text": "",
            "title": title or str(cleaned.get("title") or ""),
            "error": "empty body after clean",
            "backend": "wigolo",
        }
    return {
        "ok": True,
        "text": body,
        "title": (title or str(cleaned.get("title") or ""))[:512],
        "error": "",
        "backend": "wigolo",
    }


def fetch_url_resilient(url: str, *, max_chars: int | None = None) -> dict[str, Any]:
    """Jina/httpx first (better news body), then Wigolo, then Searx snippet.

    Always returns cleaned plain text (no images/nav/ads). On captcha/challenge
    or chrome/blocked interstitial, escalate to Wigolo auto → force JS
    (Chromium) before giving up. Never invent content.
    """
    from apps.integrations.web_reader.article_text import (
        extract_article_text,
        looks_like_fetch_block,
        looks_like_page_chrome,
    )

    target = (url or "").strip()
    if not target.startswith("http"):
        return {
            "ok": False,
            "text": "",
            "title": "",
            "error": "invalid url",
            "backend": "none",
        }
    cap = max(
        2000,
        min(
            int(
                max_chars
                if max_chars is not None
                else getattr(settings, "WIGOLO_FETCH_MAX_CHARS", 12000) or 12000
            ),
            100_000,
        ),
    )
    last: dict[str, Any] = {"ok": False, "error": "no attempt", "backend": "none"}

    def _pack(ok: bool, text: str, title: str, backend: str, error: str = "", **extra):
        cleaned = extract_article_text(text, title_hint=title, max_chars=cap)
        body = str(cleaned.get("text") or "")
        ttl = (title or str(cleaned.get("title") or ""))[:512]
        # Captcha / challenge interstitial must not count as a successful body.
        if looks_like_fetch_block(body, error=error) or looks_like_page_chrome(body):
            return {
                "ok": False,
                "text": body,
                "title": ttl,
                "error": error or "blocked_or_chrome",
                "backend": backend,
                **extra,
            }
        if ok and len(body) >= 120:
            return {
                "ok": True,
                "text": body,
                "title": ttl,
                "error": "",
                "backend": backend,
                **extra,
            }
        return {
            "ok": False,
            "text": body,
            "title": ttl,
            "error": error or "empty after clean",
            "backend": backend,
            **extra,
        }

    # 1) Jina / httpx — usually best full-text for news sites
    try:
        from apps.integrations.web_reader.reader import read_url, web_reader_enabled

        if web_reader_enabled():
            result = read_url(target)
            packed = _pack(
                bool(getattr(result, "ok", False)),
                str(getattr(result, "text", "") or ""),
                "",
                str(getattr(result, "backend", "") or "jina"),
                error=str(getattr(result, "error", "") or "reader empty")[:120],
            )
            if packed.get("ok"):
                return packed
            last = packed
    except Exception as exc:  # noqa: BLE001
        last = {
            "ok": False,
            "text": "",
            "title": "",
            "error": f"reader: {exc}"[:120],
            "backend": "reader",
        }

    # 2) Wigolo: auto first, then force JS/Chromium on miss or captcha/challenge.
    # Wigolo's schema accepts auto|always|never (not true|false).
    if wigolo_fetch_enabled():
        for js in ("auto", "always"):
            try:
                last = fetch_wigolo(target, max_chars=cap, render_js=js)
            except Exception as exc:  # noqa: BLE001
                last = {
                    "ok": False,
                    "text": "",
                    "title": "",
                    "error": str(exc)[:120],
                    "backend": "wigolo",
                }
                continue
            text = str(last.get("text") or "").strip()
            err = str(last.get("error") or "")
            if (
                last.get("ok")
                and len(text) >= 120
                and not looks_like_fetch_block(text, error=err)
                and not looks_like_page_chrome(text)
            ):
                last = {**last, "text": text[:cap], "backend": f"wigolo:{js}"}
                return last
            # Soft failure / captcha → try next tier (force JS) instead of break.
            last = {
                **last,
                "ok": False,
                "error": err or "wigolo_empty",
                "backend": f"wigolo:{js}",
            }

    # 3) Searx: recover title/snippet when full fetch fails (supports briefing digests)
    searx_hit = _searx_snippet_for_url(target)
    if searx_hit:
        snip = str(searx_hit.get("content") or searx_hit.get("snippet") or "")
        title = str(searx_hit.get("title") or "")[:300]
        cleaned = extract_article_text(snip, title_hint=title, max_chars=min(cap, 4000))
        body = str(cleaned.get("text") or "")
        if len(body) >= 80 and not looks_like_fetch_block(body):
            return {
                "ok": True,
                "text": body,
                "title": title or str(cleaned.get("title") or ""),
                "error": "",
                "backend": "searx",
                "partial": True,
            }

    return last if isinstance(last, dict) else {
        "ok": False,
        "text": "",
        "title": "",
        "error": "empty response",
        "backend": "wigolo",
    }


def _searx_snippet_for_url(url: str) -> dict[str, Any] | None:
    """Best-effort Searx hit matching this URL (or host+path keywords)."""
    try:
        from apps.integrations.searx.client import search_searx, searx_configured
    except Exception:  # noqa: BLE001
        return None
    if not searx_configured():
        return None
    target = (url or "").strip()
    if not target.startswith("http"):
        return None
    queries = [f'url:"{target}"', target]
    try:
        from urllib.parse import urlparse

        parsed = urlparse(target)
        path = (parsed.path or "").strip("/")
        if path:
            queries.append(f"{parsed.netloc} {path.replace('/', ' ')[:80]}")
    except Exception:  # noqa: BLE001
        pass
    for q in queries[:2]:
        try:
            hits = search_searx(q, limit=5) or []
        except Exception:  # noqa: BLE001
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            hu = str(hit.get("url") or "")
            if hu.rstrip("/") == target.rstrip("/") or target in hu or hu in target:
                return hit
            content = str(hit.get("content") or hit.get("snippet") or "")
            if len(content) >= 120:
                # Accept strongest non-exact match from first query only.
                return hit
    return None


def research_wigolo(
    question: str,
    *,
    depth: str = "standard",
    max_sources: int | None = None,
) -> dict[str, Any]:
    """Optional deep brief — Wigolo research tool (does not replace last30days)."""
    q = " ".join((question or "").split()).strip()
    if not q or not wigolo_configured():
        return {"ok": False, "error": "wigolo not configured or empty question"}
    body: dict[str, Any] = {
        "question": q,
        "depth": depth if depth in {"quick", "standard", "comprehensive"} else "standard",
    }
    if max_sources:
        body["max_sources"] = max(3, min(int(max_sources), 40))
    data = _post("research", body, timeout=_timeout(_RESEARCH_TIMEOUT))
    if not data:
        return {"ok": False, "error": "empty research response"}
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    # Prefer synthesized markdown/text when present.
    markdown = str(
        data.get("markdown")
        or data.get("answer")
        or data.get("report")
        or ""
    ).strip()
    if not markdown and brief:
        parts: list[str] = []
        highlights = brief.get("highlights") if isinstance(brief.get("highlights"), list) else []
        findings = (
            brief.get("key_findings")
            if isinstance(brief.get("key_findings"), list)
            else []
        )
        if highlights:
            parts.append("## Highlights")
            parts.extend(f"- {h}" for h in highlights[:12] if h)
        if findings:
            parts.append("## Key findings")
            parts.extend(f"- {f}" for f in findings[:20] if f)
        markdown = "\n".join(parts).strip()
    return {
        "ok": bool(markdown or brief),
        "markdown": markdown,
        "brief": brief,
        "raw": data,
        "error": "" if (markdown or brief) else "no brief content",
    }


def doctor_wigolo() -> dict[str, Any]:
    ok = wigolo_configured()
    detail = wigolo_base_url() or "Set WIGOLO_URL to enable"
    health: dict[str, Any] = {}
    if ok:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{wigolo_base_url()}/health")
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        health = payload
                    detail = f"{wigolo_base_url()} status={health.get('status', 'ok')}"
                else:
                    ok = False
                    detail = f"health HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            ok = False
            detail = f"unreachable: {exc}"[:160]
    return {
        "ok": ok,
        "configured": wigolo_configured(),
        "enabled": wigolo_enabled(),
        "fetch_enabled": wigolo_fetch_enabled(),
        "detail": detail,
        "health": health,
    }
