"""Reddit: cookie search (discover) + deep enrich via .json."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from django.conf import settings

from apps.integrations.web_reader.phrase import contains_phrase
from apps.integrations.web_reader.reader import ReadResult

logger = logging.getLogger(__name__)

_TIMEOUT = 25.0
_SEARCH_URL = "https://www.reddit.com/search.json"
_REDDIT_HOSTS = frozenset(
    {"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com"}
)


def reddit_enrich_enabled() -> bool:
    return bool(getattr(settings, "REDDIT_ENRICH_ENABLED", True))


def reddit_cookie() -> str:
    return (getattr(settings, "REDDIT_COOKIE", "") or "").strip()


def reddit_cookie_ready() -> bool:
    """True when a usable session cookie string is present."""
    cookie = reddit_cookie()
    if not cookie:
        return False
    lower = cookie.lower()
    return "reddit_session=" in lower or cookie.startswith("eyJ")


def reddit_search_enabled() -> bool:
    return bool(getattr(settings, "REDDIT_SEARCH_ENABLED", True))


def reddit_search_configured() -> bool:
    return reddit_search_enabled() and reddit_cookie_ready()


def doctor_reddit_search() -> dict[str, Any]:
    enabled = reddit_search_enabled()
    cookie_ok = reddit_cookie_ready()
    ok = enabled and cookie_ok
    if not enabled:
        detail = "disabled"
    elif not cookie_ok:
        detail = "missing REDDIT_COOKIE (export full header; need reddit_session)"
    else:
        detail = "ready"
    return {
        "id": "reddit_search",
        "label": "Reddit cookie search",
        "role": "discover",
        "ok": ok,
        "configured": ok,
        "detail": detail,
    }


def doctor_reddit() -> dict[str, Any]:
    enabled = reddit_enrich_enabled()
    cookie = reddit_cookie_ready()
    return {
        "id": "reddit_enrich",
        "label": "Reddit post/comment enrich",
        "role": "enrich",
        "ok": enabled,
        "configured": enabled,
        "detail": (
            "cookie set"
            if cookie
            else "public JSON (set REDDIT_COOKIE if 403/429)"
        ),
    }


def is_reddit_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in _REDDIT_HOSTS)


def _json_url(url: str) -> str | None:
    parsed = urlparse((url or "").strip())
    if not is_reddit_url(url):
        return None
    path = parsed.path or "/"
    if path.endswith(".json"):
        return urlunparse(("https", "www.reddit.com", path, "", parsed.query, ""))
    path = path.rstrip("/") + ".json"
    return urlunparse(("https", "www.reddit.com", path, "", "raw_json=1", ""))


def _cookie_header() -> str:
    raw = reddit_cookie()
    if not raw:
        return ""
    if raw.startswith("eyJ") and "reddit_session=" not in raw.lower():
        return f"reddit_session={raw}"
    return raw


def _headers(*, want_json: bool = True) -> dict[str, str]:
    headers = {
        "User-Agent": getattr(
            settings,
            "REDDIT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ),
        "Accept": "application/json" if want_json else "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    cookie = _cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _permalink_url(data: dict[str, Any]) -> str:
    permalink = str(data.get("permalink") or "").strip()
    if permalink:
        if permalink.startswith("http"):
            return permalink[:2048]
        return urljoin("https://www.reddit.com", permalink)[:2048]
    url = str(data.get("url") or "").strip()
    return url[:2048]


def search_reddit(
    query: str, *, limit: int = 15, time: str | None = None
) -> list[dict[str, Any]]:
    """
    Search Reddit posts via search.json (cookie session recommended from cloud IPs).

    Returns Searx-shaped hits: {title, url, content, engine}.
    ``time`` overrides ``REDDIT_SEARCH_TIME`` (hour|day|week|month|year|all).
    When an explicit lookback ``time`` is set (not ``all``), do not fall back to ``t=all``.
    """
    if not reddit_search_configured():
        return []
    term = " ".join((query or "").split()).strip()
    if not term:
        return []
    # Pasted post URL → single hit for persist/enrich.
    if is_reddit_url(term) and "/comments/" in term:
        return [
            {
                "title": f"Reddit URL: {term[:80]}",
                "url": term.split("?")[0].rstrip("/")[:2048],
                "content": "",
                "engine": "reddit_search",
                "score": None,
            }
        ]
    # Prefer recall for discovery, then rank newest locally.
    # Collect from quoted + unquoted (do not stop at first non-empty batch —
    # quoted often returns irrelevant children that all fail the phrase filter).
    fetch_n = max(limit, min(limit * 3, 50))
    sort = str(getattr(settings, "REDDIT_SEARCH_SORT", "relevance") or "relevance").strip().lower()
    if sort not in {"new", "relevance", "hot", "top", "comments"}:
        sort = "relevance"
    if time is not None:
        time_win = str(time or "all").strip().lower()
    else:
        time_win = str(getattr(settings, "REDDIT_SEARCH_TIME", "all") or "all").strip().lower()
    if time_win not in {"hour", "day", "week", "month", "year", "all"}:
        time_win = "all"
    # Callers that pass an explicit lookback must not widen to t=all.
    allow_all_fallback = time is None or time_win == "all"

    search_attempts: list[tuple[str, str, str]] = []
    quoted = f'"{term[:280]}"' if " " in term else term[:300]
    search_attempts.append((quoted, sort, time_win))
    if " " in term:
        search_attempts.append((term[:300], sort, time_win))
    if allow_all_fallback and (sort != "relevance" or time_win != "all"):
        search_attempts.append((quoted, "relevance", "all"))
        if " " in term:
            search_attempts.append((term[:300], "relevance", "all"))
    elif not allow_all_fallback and sort != "relevance":
        # Still try relevance within the same time window.
        search_attempts.append((quoted, "relevance", time_win))
        if " " in term:
            search_attempts.append((term[:300], "relevance", time_win))

    children_by_id: dict[str, dict[str, Any]] = {}
    last_http_error: str | None = None
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            for q, sort_i, time_i in search_attempts:
                response = client.get(
                    _SEARCH_URL,
                    params={
                        "q": q,
                        "sort": sort_i,
                        "t": time_i,
                        "limit": fetch_n,
                        "type": "link",
                        "raw_json": "1",
                    },
                    headers=_headers(),
                )
                if response.status_code in {401, 403}:
                    logger.warning(
                        "Reddit search auth failed HTTP %s — refresh REDDIT_COOKIE",
                        response.status_code,
                    )
                    return []
                if response.status_code == 429:
                    logger.warning("Reddit search rate-limited (429)")
                    last_http_error = "rate_limited"
                    continue
                response.raise_for_status()
                payload = response.json()
                listing = payload.get("data") if isinstance(payload, dict) else None
                batch = listing.get("children") if isinstance(listing, dict) else None
                if not isinstance(batch, list):
                    continue
                for child in batch:
                    if not isinstance(child, dict):
                        continue
                    data = child.get("data") if isinstance(child.get("data"), dict) else {}
                    cid = str(data.get("name") or data.get("id") or data.get("permalink") or "")
                    if not cid or cid in children_by_id:
                        continue
                    children_by_id[cid] = child
    except httpx.HTTPError as exc:
        logger.warning("Reddit search request failed: %s", exc)
        if not children_by_id:
            return []
    except ValueError as exc:
        logger.warning("Reddit search JSON decode failed: %s", exc)
        if not children_by_id:
            return []

    children = list(children_by_id.values())
    if not children:
        if last_http_error:
            logger.info("Reddit search empty after errors: %s", last_http_error)
        return []

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for child in children:
        if not isinstance(child, dict):
            continue
        data = child.get("data") if isinstance(child.get("data"), dict) else {}
        url = _permalink_url(data)
        if not url or url in seen:
            continue
        title = str(data.get("title") or "Reddit post")[:512]
        sub = str(data.get("subreddit_name_prefixed") or data.get("subreddit") or "")
        selftext = str(data.get("selftext") or "")[:3000]
        # Phrase must appear in title or post body (not only outbound link URL).
        if not contains_phrase(f"{title}\n{selftext}", term):
            continue
        seen.add(url)
        content = f"{sub}\n{selftext}".strip()[:4000]
        hits.append(
            {
                "title": f"{sub}: {title}" if sub else title,
                "url": url,
                "content": content,
                "engine": "reddit_search",
                "score": data.get("score"),
                "published": str(data.get("created_utc") or "")[:32],
            }
        )

    # Prefer newest among phrase-matched hits.
    def _pub_ts(row: dict[str, Any]) -> float:
        try:
            return float(row.get("published") or 0)
        except (TypeError, ValueError):
            return 0.0

    hits.sort(key=_pub_ts, reverse=True)
    return hits[:limit]


def _flatten_listing(node: Any, out: list[str], *, depth: int = 0) -> None:
    if depth > 14 or len(out) > 250:
        return
    if isinstance(node, list):
        for item in node:
            _flatten_listing(item, out, depth=depth + 1)
        return
    if not isinstance(node, dict):
        return
    data = node.get("data") if isinstance(node.get("data"), dict) else node
    if isinstance(data, dict):
        for key in ("title", "selftext", "body"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
        children = data.get("children")
        if isinstance(children, list):
            for child in children:
                _flatten_listing(child, out, depth=depth + 1)
        replies = data.get("replies")
        if replies:
            _flatten_listing(replies, out, depth=depth + 1)


def read_reddit(url: str) -> ReadResult:
    """Fetch post + nested comment text via Reddit JSON (for secret detection)."""
    if not reddit_enrich_enabled():
        return ReadResult(False, "reddit", "", "reddit enrich disabled")
    target = _json_url(url)
    if not target:
        return ReadResult(False, "reddit", "", "not a reddit url")
    # Ask for a large comment tree when possible.
    if "limit=" not in target:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}limit=500"
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = client.get(target, headers=_headers())
            if response.status_code in {401, 403, 429}:
                return ReadResult(
                    False,
                    "reddit",
                    "",
                    f"HTTP {response.status_code} — set full REDDIT_COOKIE (secondary account)",
                )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        return ReadResult(False, "reddit", "", str(exc)[:200])
    except ValueError as exc:
        return ReadResult(False, "reddit", "", f"json error: {exc}"[:200])

    parts: list[str] = []
    _flatten_listing(payload, parts)
    text = "\n\n".join(dict.fromkeys(parts))[:200_000]
    if not text.strip():
        return ReadResult(False, "reddit", "", "empty reddit payload")
    return ReadResult(True, "reddit", text)
