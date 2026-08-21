"""SearxNG HTTP client — privacy-respecting metasearch (Watcher-aligned).

Security notes (reviewer):
- Base URL comes ONLY from settings/env (never from request body).
- Query length and result count are capped.
- Engines are allowlisted; arbitrary engine strings are rejected.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 25.0
MAX_QUERY_LEN = 200
MAX_RESULTS = 40

# Open-web leak hunting — GitHub is handled by GitHub Scanner, not Searx.
DEFAULT_ENGINES = (
    "duckduckgo",
    "brave",
    "bing",
    "gitlab",
    "bitbucket",
    "npm",
    "stackoverflow",
    "qwant",
    "ahmia",
)
# Engines never used for open-web (dedicated modules elsewhere).
EXCLUDED_OPEN_WEB_ENGINES = frozenset({"github"})
ALLOWED_ENGINES = frozenset(
    {
        *DEFAULT_ENGINES,
        "google",
        "wikipedia",
        "startpage",
        "apkmirror",
        "gentoo",
        "askubuntu",
        "yandex",
        "semantic scholar",
        "mojeek",
    }
)


def searx_configured() -> bool:
    return bool((getattr(settings, "SEARXNG_URL", "") or "").strip())


def searx_base_url() -> str:
    return (getattr(settings, "SEARXNG_URL", "") or "").strip().rstrip("/")


def _validate_configured_base(base: str) -> bool:
    """Reject non-http(s) schemes — SSRF hygiene for misconfigured env."""
    parsed = urlparse(base)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def build_search_term(keyword: str, *, is_regex: bool = False) -> str:
    """Mirror Watcher quoting rules for exact-ish SearxNG matches."""
    term = (keyword or "").strip()
    if not term:
        return ""
    if is_regex:
        return term
    if any(ch in term for ch in ("%", "@", "&", "+", "=")):
        return term
    if not (term.startswith('"') and term.endswith('"')):
        return f'"{term}"'
    return term


def _normalize_engines(engines: str | list[str] | None) -> str:
    if engines is None:
        default = getattr(settings, "SEARXNG_ENGINES", "") or ""
        if default.strip():
            engines = default
        else:
            selected = list(DEFAULT_ENGINES)
            return ",".join(selected)
    if isinstance(engines, str):
        selected = [e.strip().lower() for e in engines.split(",") if e.strip()]
    else:
        selected = [str(e).strip().lower() for e in engines if str(e).strip()]

    filtered = [
        e
        for e in selected
        if e in ALLOWED_ENGINES and e not in EXCLUDED_OPEN_WEB_ENGINES
    ]
    if not filtered:
        filtered = list(DEFAULT_ENGINES)
    return ",".join(filtered)


def is_github_host_url(url: str) -> bool:
    """True for github.com / gist / raw.githubusercontent (use GitHub Scanner instead)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return (
        host == "github.com"
        or host.endswith(".github.com")
        or host == "githubusercontent.com"
        or host.endswith(".githubusercontent.com")
    )


def search_searx(
    query: str,
    *,
    engines: str | list[str] | None = None,
    limit: int = 20,
    exact: bool = True,
    time_range: str | None = None,
    strict_engines: bool = False,
) -> list[dict[str, Any]]:
    """
    Query SearxNG JSON API. Returns normalized hits:
    {title, url, content, engine, score?, published?}
    """
    detailed = search_searx_detailed(
        query,
        engines=engines,
        limit=limit,
        exact=exact,
        time_range=time_range,
        strict_engines=strict_engines,
    )
    return detailed["hits"]


def search_searx_detailed(
    query: str,
    *,
    engines: str | list[str] | None = None,
    limit: int = 20,
    exact: bool = True,
    time_range: str | None = None,
    strict_engines: bool = False,
) -> dict[str, Any]:
    """Like search_searx, plus unresponsive_engines metadata from SearxNG."""
    empty: dict[str, Any] = {
        "hits": [],
        "unresponsive_engines": [],
        "requested_engines": "",
    }
    base = searx_base_url()
    if not base or not _validate_configured_base(base):
        return empty

    raw = (query or "").strip()
    if not raw:
        return empty
    term = build_search_term(raw) if exact else raw
    term = term[: MAX_QUERY_LEN + 2]  # allow surrounding quotes

    limit = max(1, min(int(limit or 20), MAX_RESULTS))
    engine_param = _normalize_engines(engines)
    endpoint = urljoin(base + "/", "search")

    # Explicit empty string means "no time filter". None falls back to settings.
    if time_range is None:
        tr = (getattr(settings, "SEARX_TIME_RANGE", "") or "").strip().lower()
    else:
        tr = str(time_range).strip().lower()
    if tr not in {"day", "week", "month", "year"}:
        tr = ""

    params: dict[str, Any] = {"q": term, "format": "json", "engines": engine_param}
    if tr:
        params["time_range"] = tr

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("SearxNG request failed: %s", exc)
        return empty
    except ValueError as exc:
        logger.warning("SearxNG JSON decode failed: %s", exc)
        return empty

    unresponsive = []
    if isinstance(payload, dict):
        raw_unresponsive = payload.get("unresponsive_engines") or []
        if isinstance(raw_unresponsive, list):
            unresponsive = raw_unresponsive

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return {
            "hits": [],
            "unresponsive_engines": unresponsive,
            "requested_engines": engine_param,
        }

    requested = {e.strip().lower() for e in engine_param.split(",") if e.strip()}
    seen: set[str] = set()
    hits: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        engine_name = str(item.get("engine") or item.get("engines") or "")[:64]
        # Open-web must not surface GitHub — dedicated GitHub Scanner owns that.
        if is_github_host_url(url) or "github" in engine_name.lower():
            continue
        if strict_engines and requested and not _engine_matches_request(
            engine_name, requested
        ):
            # Searx often silently substitutes another engine when the requested
            # one is suspended — treat those as misses for the caller.
            continue
        seen.add(url)
        hits.append(
            {
                "title": str(item.get("title") or url)[:512],
                "url": url[:2048],
                "content": str(item.get("content") or item.get("snippet") or "")[:4000],
                "engine": engine_name,
                "score": item.get("score"),
                "published": str(
                    item.get("publishedDate")
                    or item.get("published_date")
                    or item.get("date")
                    or ""
                )[:128],
            }
        )
        if len(hits) >= limit:
            break
    return {
        "hits": hits,
        "unresponsive_engines": unresponsive,
        "requested_engines": engine_param,
    }


def _engine_matches_request(hit_engine: str, requested: set[str]) -> bool:
    he = (hit_engine or "").casefold()
    if not he:
        return False
    aliases = {
        "google": ("google", "google cse"),
        "bing": ("bing",),
        "brave": ("brave",),
        "duckduckgo": ("duckduckgo", "ddg"),
        "startpage": ("startpage",),
        "yandex": ("yandex",),
        "semantic scholar": ("semantic scholar", "semanticscholar"),
    }
    for req in requested:
        names = aliases.get(req, (req,))
        if any(name in he for name in names):
            return True
    return False
