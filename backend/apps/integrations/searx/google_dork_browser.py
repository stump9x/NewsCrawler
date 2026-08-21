"""Chromium Google-dork SERP scraper — links only, never downloads PDFs.

Uses Playwright to open Google search the same way a human would with a dork
like ``\"information warfare\" filetype:pdf``, then extracts organic result
metadata (title, url, snippet, relative date). PDF file bodies are never
fetched; we only keep the result URLs for later ingest.

Anti-CAPTCHA posture:
- Persistent browser profile (stable fingerprint / cookies)
- Long inter-query pacing + single shared browser
- Abort immediately on /sorry/ or captcha UI → Redis cooldown → Searx fallback
- Block heavy assets; never navigate off Google SERP onto PDF URLs
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

_BROWSER_LOCK = threading.Lock()
_PACE_LOCK = threading.Lock()
_LAST_QUERY_MONOTONIC = 0.0
_BROWSER_EXECUTOR_LOCK = threading.Lock()
_BROWSER_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_BROWSER_THREAD_PREFIX = "nc-google-dork"

_PLAYWRIGHT = None
_BROWSER = None
_CONTEXT = None

T = TypeVar("T")

_CAPTCHA_HINTS = (
    "/sorry/",
    "unusual traffic",
    "detected unusual traffic",
    "g-recaptcha",
    "captcha-form",
    "Our systems have detected",
)

_RELATIVE_DATE_RE = re.compile(
    r"(?i)\b(\d+)\s*(minute|minutes|hour|hours|day|days|week|weeks|month|months)\s+ago\b"
)


def google_browser_enabled() -> bool:
    return bool(getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER", True))


def google_browser_delay_sec() -> float:
    return max(3.0, float(getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_DELAY_SEC", 12.0) or 12.0))


def google_browser_timeout_ms() -> int:
    return max(5000, int(getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_TIMEOUT_MS", 25000) or 25000))


def google_browser_profile_dir() -> str:
    return (
        getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_PROFILE", "")
        or "/tmp/nc-google-browser-profile"
    ).strip()


def google_browser_headless() -> bool:
    return bool(getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_HEADLESS", True))


def _tbs_for_time_range(time_range: str) -> str:
    mapping = {
        "day": "qdr:d",
        "week": "qdr:w",
        "month": "qdr:m",
        "year": "qdr:y",
    }
    return mapping.get((time_range or "").strip().lower(), "qdr:m")


def build_google_dork_search_url(query: str, *, time_range: str = "month", num: int = 10) -> str:
    """Build a classic Google web search URL for an exact dork query (Past month by default)."""
    q = " ".join((query or "").split())
    if not q:
        return ""
    params = [
        f"q={quote_plus(q)}",
        "hl=en",
        "gl=us",
        f"num={max(5, min(20, int(num or 10)))}",
        "pws=0",
        "filter=0",
    ]
    tbs = _tbs_for_time_range(time_range)
    if tbs:
        params.append(f"tbs={tbs}")
    return "https://www.google.com/search?" + "&".join(params)


def unwrap_google_href(href: str) -> str:
    """Resolve Google redirect ``/url?q=…`` to the destination URL."""
    text = (href or "").strip()
    if not text:
        return ""
    if text.startswith("/url?"):
        text = "https://www.google.com" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.netloc.endswith("google.com") and parsed.path.startswith("/url"):
        qs = parse_qs(parsed.query)
        for key in ("q", "url"):
            values = qs.get(key) or []
            if values:
                return unquote(values[0]).strip()
    if parsed.scheme in {"http", "https"}:
        host = (parsed.netloc or "").lower()
        if "google." in host:
            return ""
        return text
    return ""


def page_looks_like_captcha(html: str, url: str = "") -> bool:
    blob = f"{url}\n{html or ''}".casefold()
    return any(hint in blob for hint in _CAPTCHA_HINTS)


def _document_url_rank(url: str) -> int:
    """Higher = more likely a real PDF/document file URL (not a site homepage)."""
    text = (url or "").strip().lower()
    if not text.startswith("http"):
        return -1
    path = ""
    try:
        path = (urlparse(text).path or "").lower()
    except ValueError:
        path = ""
    score = 0
    if re.search(r"\.pdf(?:$|[?#])", text) or ".pdf" in path:
        score += 100
    if "/pdf/" in path or path.endswith("/pdf"):
        score += 80
    if "filetype=pdf" in text or "type=pdf" in text:
        score += 40
    if "/media/document" in path or "/content/dam" in path:
        score += 50
    # Breadcrumb / section roots Google shows next to the PDF badge.
    if path in {"", "/"} or path.count("/") <= 1:
        score -= 40
    if re.search(r"/(?:articles?|news|story|blog)/", path) and ".pdf" not in path:
        score -= 80
    return score


def _pick_best_document_href(anchors) -> tuple[str, Any]:
    """
    From SERP result anchors, prefer the direct PDF/document href.

    Google often puts a site/section link first and the real ``.pdf`` on the
    title ``<a>`` (or vice versa). Never keep a bare section URL when a PDF
    sibling exists — e.g. prefer
    ``https://www.mod.go.jp/asdf/en/20260702e.pdf`` over ``…/asdf``.
    """
    best_url = ""
    best_anchor = None
    best_score = -10_000
    for anchor in anchors or []:
        raw = ""
        try:
            raw = anchor.get("href") or ""
        except Exception:  # noqa: BLE001
            continue
        href = unwrap_google_href(raw)
        if not href:
            continue
        score = _document_url_rank(href)
        try:
            if anchor.select_one("h3") is not None or (
                getattr(anchor, "name", "") == "a"
                and anchor.find("h3") is not None
            ):
                score += 15
            parent_classes = " ".join(anchor.parent.get("class", []) if anchor.parent else [])
            if "yuRUbf" in parent_classes or anchor.find_parent(class_="yuRUbf"):
                score += 8
        except Exception:  # noqa: BLE001
            pass
        if score > best_score:
            best_score = score
            best_url = href
            best_anchor = anchor
    # Require at least a mild document signal — reject pure HTML section links.
    if best_score < 40:
        return "", None
    return best_url, best_anchor


def parse_google_serp_html(html: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """
    Extract organic Google results from SERP HTML.

    Links only — never follows or downloads the destination PDF.
    Prefers direct ``.pdf`` / ``/pdf/`` hrefs over site breadcrumbs.
    """
    if not html:
        return []
    # Lazy import so unit tests without bs4 still can mock this function.
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return _parse_google_serp_regex(html, limit=limit)

    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Prefer classic result blocks; fall back to title anchors under #search.
    blocks = soup.select("div.g, div[data-sokoban-container], div.MjjYud")
    if not blocks:
        # Synthetic one-anchor "blocks" so each result stays independent.
        blocks = [
            a
            for a in soup.select("#search a[href]")
            if a.select_one("h3") is not None or a.find("h3") is not None
        ] or list(soup.select("#search a[href]"))

    for block in blocks:
        # Collect every outbound anchor in the card; pick the best PDF-like one.
        anchors = []
        if getattr(block, "name", "") == "a":
            anchors = [block]
        elif hasattr(block, "select"):
            anchors = list(block.select("a[href]"))
        href, anchor = _pick_best_document_href(anchors)
        if not href or href in seen:
            continue
        title = ""
        if anchor is not None:
            title = " ".join(anchor.stripped_strings)[:512]
            if not title and hasattr(block, "select_one"):
                heading = block.select_one("h3")
                title = " ".join(heading.stripped_strings)[:512] if heading else ""
        if not title and hasattr(block, "select_one"):
            heading = block.select_one("h3")
            title = " ".join(heading.stripped_strings)[:512] if heading else href
        snippet = ""
        published = ""
        if hasattr(block, "select_one"):
            sn_el = block.select_one("div.VwiC3b, span.aCOpRe, div[data-sncf], div.IsZvec")
            if sn_el:
                snippet = " ".join(sn_el.stripped_strings)[:2000]
            date_el = block.select_one("span.LEwnzc, span.YrbPuc, span.f")
            if date_el:
                published = " ".join(date_el.stripped_strings)[:120]
        if not published:
            match = _RELATIVE_DATE_RE.search(snippet)
            if match:
                published = match.group(0)
        seen.add(href)
        out.append(
            {
                "title": title or href,
                "url": href,
                "content": snippet,
                "published": published,
                "engine": "google_browser",
                "score": None,
                "time_filtered": True,
            }
        )
        if len(out) >= max(1, limit):
            break
    return out


def _parse_google_serp_regex(html: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Minimal fallback parser when BeautifulSoup is unavailable — PDF URLs only."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'href="(/url\?q=[^"&]+[^"]*|https?://(?!www\.google\.)[^"]+)"',
        html or "",
        re.I,
    ):
        href = unwrap_google_href(match.group(1))
        if not href or href in seen:
            continue
        if _document_url_rank(href) < 40:
            continue
        seen.add(href)
        out.append(
            {
                "title": href,
                "url": href,
                "content": "",
                "published": "",
                "engine": "google_browser",
                "score": None,
                "time_filtered": True,
            }
        )
        if len(out) >= max(1, limit):
            break
    return out


def _mark_browser_captcha(reason: str = "captcha") -> None:
    try:
        from apps.core.task_lock import _redis_client

        ttl = max(
            300,
            int(getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_CAPTCHA_TTL_SEC", 2700) or 2700),
        )
        _redis_client().setex("bs:docscan:engine_cool:google_browser", ttl, reason)
        # Also cool Searx google — same destination often shares the block.
        _redis_client().setex("bs:docscan:engine_cool:google", ttl, reason)
    except Exception as exc:  # noqa: BLE001
        logger.warning("google_browser captcha mark failed: %s", exc)


def google_browser_on_cooldown() -> bool:
    try:
        from apps.core.task_lock import _redis_client

        return bool(_redis_client().get("bs:docscan:engine_cool:google_browser"))
    except Exception:  # noqa: BLE001
        return False


def _pace_google_browser() -> None:
    global _LAST_QUERY_MONOTONIC
    delay = google_browser_delay_sec()
    with _PACE_LOCK:
        now = time.monotonic()
        wait = _LAST_QUERY_MONOTONIC + delay - now
        if wait > 0:
            time.sleep(wait)
        _LAST_QUERY_MONOTONIC = time.monotonic()


def _close_browser_unlocked() -> None:
    global _PLAYWRIGHT, _BROWSER, _CONTEXT
    for obj_name in ("_CONTEXT", "_BROWSER"):
        obj = globals().get(obj_name)
        if obj is not None:
            try:
                obj.close()
            except Exception:  # noqa: BLE001
                pass
            globals()[obj_name] = None
    if _PLAYWRIGHT is not None:
        try:
            _PLAYWRIGHT.stop()
        except Exception:  # noqa: BLE001
            pass
        _PLAYWRIGHT = None


def close_google_browser() -> None:
    """Shut down Chromium on the dedicated browser thread."""
    _run_on_browser_thread(_close_google_browser_impl)


def _close_google_browser_impl() -> None:
    with _BROWSER_LOCK:
        _close_browser_unlocked()


def _browser_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Single dedicated thread — Playwright sync must not touch Celery workers."""
    global _BROWSER_EXECUTOR
    with _BROWSER_EXECUTOR_LOCK:
        if _BROWSER_EXECUTOR is None:
            _BROWSER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=_BROWSER_THREAD_PREFIX,
            )
        return _BROWSER_EXECUTOR


def _run_on_browser_thread(
    fn: Callable[..., T],
    /,
    *args: Any,
    timeout: float | None = None,
    **kwargs: Any,
) -> T:
    """
    Run Playwright work off the Celery/Django thread.

    Playwright's sync API installs an asyncio loop on its calling thread. If that
    is a Celery ForkPoolWorker, Django then raises SynchronousOnlyOperation for
    every ORM call afterward. Isolating Chromium fixes the worker permanently.
    """
    if threading.current_thread().name.startswith(_BROWSER_THREAD_PREFIX):
        return fn(*args, **kwargs)
    wait_s = timeout
    if wait_s is None:
        wait_s = max(
            45.0,
            (google_browser_timeout_ms() / 1000.0) + google_browser_delay_sec() + 30.0,
        )
    future = _browser_executor().submit(lambda: fn(*args, **kwargs))
    return future.result(timeout=wait_s)


def _ensure_context():
    """Lazy-start a persistent Chromium context (shared across one worker)."""
    global _PLAYWRIGHT, _BROWSER, _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT
    from playwright.sync_api import sync_playwright

    import os

    profile = google_browser_profile_dir()
    os.makedirs(profile, exist_ok=True)
    _PLAYWRIGHT = sync_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "headless": google_browser_headless(),
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    }
    exe = (getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_EXECUTABLE", "") or "").strip()
    if exe:
        launch_kwargs["executable_path"] = exe
    _BROWSER = _PLAYWRIGHT.chromium.launch(**launch_kwargs)
    _CONTEXT = _BROWSER.new_context(
        locale="en-US",
        timezone_id="America/New_York",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1365, "height": 900},
        java_script_enabled=True,
    )
    # Soften webdriver flag.
    _CONTEXT.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return _CONTEXT


def build_bing_dork_search_url(query: str, *, time_range: str = "month", num: int = 10) -> str:
    """Bing web search URL with phrase dork (+ optional freshness)."""
    from urllib.parse import urlencode

    q = " ".join((query or "").split())
    if not q:
        return ""
    # ez1=day ez2=week ez3=month (Bing freshness chips).
    ez = {"day": "ez1", "week": "ez2", "month": "ez3"}.get(
        (time_range or "").strip().lower(), ""
    )
    params: dict[str, str] = {
        "q": q,
        "setlang": "en-US",
        "count": str(max(5, min(20, int(num or 10)))),
    }
    # Freshness filter often returns zero filetype:pdf hits — keep off by default.
    if ez and bool(getattr(settings, "DOCUMENT_SCAN_BING_BROWSER_FRESHNESS", False)):
        params["filters"] = f'ex1:"{ez}"'
    return "https://www.bing.com/search?" + urlencode(params)


def unwrap_bing_href(href: str) -> str:
    """Resolve Bing ``/ck/a?!&u=a1…`` tracking links to the destination URL."""
    import base64

    text = (href or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    host = (parsed.netloc or "").lower()
    if "bing.com" in host and (parsed.path.startswith("/ck/") or "u=" in (parsed.query or "")):
        qs = parse_qs(parsed.query)
        raw = (qs.get("u") or [""])[0]
        if raw.startswith("a1"):
            payload = raw[2:]
            payload += "=" * (-len(payload) % 4)
            try:
                decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode(
                    "utf-8", errors="replace"
                )
                if decoded.startswith("http"):
                    return decoded.strip()
            except Exception:  # noqa: BLE001
                return ""
        return ""
    if parsed.scheme in {"http", "https"}:
        if "bing." in host or "microsoft.com" in host:
            return ""
        return text
    return ""


def parse_bing_serp_html(html: str, *, limit: int = 10) -> list[dict[str, Any]]:
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for li in soup.select("li.b_algo"):
        candidates: list[str] = []
        for a in li.select("a[href]"):
            href = unwrap_bing_href(a.get("href") or "")
            if href:
                candidates.append(href)
        cite = li.select_one("cite")
        if cite:
            cite_text = " ".join(cite.stripped_strings).strip()
            # Bing cite may show the real PDF path even when the title link is HTML.
            if cite_text.startswith("http"):
                candidates.append(cite_text)
            elif ".pdf" in cite_text.casefold():
                # Relative-looking cite: join with host from best candidate if any.
                pass
        href = ""
        best = -10_000
        for cand in candidates:
            rank = _document_url_rank(cand)
            if rank > best:
                best = rank
                href = cand
        if best < 40 or not href or href in seen:
            continue
        anchor = li.select_one("h2 a[href]") or li.select_one("a[href]")
        title = " ".join(anchor.stripped_strings)[:512] if anchor else href
        sn_el = li.select_one("div.b_caption p, p")
        snippet = " ".join(sn_el.stripped_strings)[:2000] if sn_el else ""
        published = ""
        match = _RELATIVE_DATE_RE.search(snippet)
        if match:
            published = match.group(0)
        seen.add(href)
        out.append(
            {
                "title": title or href,
                "url": href,
                "content": snippet,
                "published": published,
                "engine": "bing_browser",
                "score": None,
                "time_filtered": True,
            }
        )
        if len(out) >= max(1, limit):
            break
    return out


def _search_serp_browser(
    *,
    engine: str,
    search_url: str,
    query: str,
    limit: int,
    parse_html,
) -> dict[str, Any]:
    empty = {
        "hits": [],
        "captcha": False,
        "error": "",
        "query": query,
        "search_url": search_url,
        "engine": engine,
    }
    if not search_url:
        return empty
    cool_key = f"bs:docscan:engine_cool:{engine}"
    try:
        from apps.core.task_lock import _redis_client

        if _redis_client().get(cool_key):
            return {**empty, "error": "cooldown", "captcha": True}
    except Exception:  # noqa: BLE001
        pass

    _pace_google_browser()
    with _BROWSER_LOCK:
        try:
            context = _ensure_context()
            page = context.new_page()
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_()
                ),
            )
            try:
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=google_browser_timeout_ms(),
                )
                try:
                    page.wait_for_timeout(800)
                except Exception:  # noqa: BLE001
                    pass
                html = page.content()
                final_url = page.url or search_url
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

            if page_looks_like_captcha(html, final_url):
                logger.warning("%s captcha query=%r url=%s", engine, query, final_url)
                try:
                    from apps.core.task_lock import _redis_client

                    ttl = max(
                        300,
                        int(
                            getattr(
                                settings,
                                "DOCUMENT_SCAN_GOOGLE_BROWSER_CAPTCHA_TTL_SEC",
                                2700,
                            )
                            or 2700
                        ),
                    )
                    _redis_client().setex(cool_key, ttl, "captcha")
                    if engine == "google_browser":
                        _redis_client().setex(
                            "bs:docscan:engine_cool:google", ttl, "captcha"
                        )
                except Exception:  # noqa: BLE001
                    pass
                _close_browser_unlocked()
                return {
                    **empty,
                    "captcha": True,
                    "error": "captcha",
                }

            hits = parse_html(html, limit=limit)
            logger.info("%s query=%r hits=%s", engine, query, len(hits))
            return {
                "hits": hits,
                "captcha": False,
                "error": "",
                "query": query,
                "search_url": search_url,
                "engine": engine,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s search failed: %s", engine, exc)
            try:
                _close_browser_unlocked()
            except Exception:  # noqa: BLE001
                pass
            return {**empty, "error": str(exc)[:300]}


def search_google_dork_browser(
    query: str,
    *,
    limit: int = 10,
    time_range: str = "month",
) -> dict[str, Any]:
    """
    Run one Google dork in Chromium and return SERP hits (URLs only).

    Returns ``{hits, captcha, error, query, search_url}``.
    """
    q = " ".join((query or "").split())
    empty = {"hits": [], "captcha": False, "error": "", "query": q, "search_url": ""}
    if not q:
        return empty
    if not google_browser_enabled():
        return {**empty, "error": "disabled"}
    if google_browser_on_cooldown():
        return {**empty, "error": "cooldown", "captcha": True}

    return _run_on_browser_thread(
        _search_google_dork_browser_impl,
        q,
        limit=limit,
        time_range=time_range,
    )


def _search_google_dork_browser_impl(
    query: str,
    *,
    limit: int = 10,
    time_range: str = "month",
) -> dict[str, Any]:
    search_url = build_google_dork_search_url(query, time_range=time_range, num=limit)
    return _search_serp_browser(
        engine="google_browser",
        search_url=search_url,
        query=query,
        limit=limit,
        parse_html=parse_google_serp_html,
    )


def search_bing_dork_browser(
    query: str,
    *,
    limit: int = 10,
    time_range: str = "month",
) -> dict[str, Any]:
    """Chromium Bing phrase-dork fallback when Google SERP is captcha'd."""
    q = " ".join((query or "").split())
    empty = {"hits": [], "captcha": False, "error": "", "query": q, "search_url": ""}
    if not q:
        return empty
    if not google_browser_enabled():
        return {**empty, "error": "disabled"}
    return _run_on_browser_thread(
        _search_bing_dork_browser_impl,
        q,
        limit=limit,
        time_range=time_range,
    )


def _search_bing_dork_browser_impl(
    query: str,
    *,
    limit: int = 10,
    time_range: str = "month",
) -> dict[str, Any]:
    search_url = build_bing_dork_search_url(query, time_range=time_range, num=limit)
    return _search_serp_browser(
        engine="bing_browser",
        search_url=search_url,
        query=query,
        limit=limit,
        parse_html=parse_bing_serp_html,
    )


def search_dork_browser(
    query: str,
    *,
    limit: int = 10,
    time_range: str = "month",
) -> dict[str, Any]:
    """Prefer Google Chromium; on captcha/empty fall back to Bing Chromium."""
    q = " ".join((query or "").split())
    empty = {"hits": [], "captcha": False, "error": "", "query": q, "search_url": ""}
    if not q:
        return empty
    if not google_browser_enabled():
        return {**empty, "error": "disabled"}
    # Google cooling down → Bing only (still isolated from Celery thread).
    if google_browser_on_cooldown():
        return _run_on_browser_thread(
            _search_bing_dork_browser_impl,
            q,
            limit=limit,
            time_range=time_range,
        )
    return _run_on_browser_thread(
        _search_dork_browser_impl,
        q,
        limit=limit,
        time_range=time_range,
    )


def _search_dork_browser_impl(
    query: str,
    *,
    limit: int = 10,
    time_range: str = "month",
) -> dict[str, Any]:
    google = _search_google_dork_browser_impl(
        query, limit=limit, time_range=time_range
    )
    if google.get("hits"):
        return google
    # Google cooldown is checked on the outer wrapper; still try Bing here.
    bing = _search_bing_dork_browser_impl(query, limit=limit, time_range=time_range)
    if bing.get("hits"):
        return bing
    if google.get("captcha"):
        return {**google, "fallback": "bing_empty"}
    return bing if bing.get("error") else google
