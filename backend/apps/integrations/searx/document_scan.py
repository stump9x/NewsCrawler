"""Automatic military/defence PDF document discovery via SearxNG (Google-biased).

Builds queries like ``\"cyber warfare\" filetype:pdf`` (exact phrase dorks), keeps hits from the last
~1 month (Google ``Past month`` / ``tbs=qdr:m``) that look like real PDF files and pass an
importance gate, then surfaces them similarly to The Wire (list + path notifications).

Keyword sweeps run in parallel so one slow query does not block the rest.
Already-known PDF URLs and recently scanned keywords are skipped.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.intel.models import AlertNotification, DocumentScanKeyword, ScannedDocument
from apps.integrations.searx.client import (
    searx_configured,
    search_searx_detailed,
)
from apps.integrations.web_reader.recency import parse_published_ts


logger = logging.getLogger(__name__)

# Shared pacing gate so parallel keyword workers do not stampede Google/Searx.
_SEARX_PACE_LOCK = threading.Lock()
_SEARX_LAST_MONOTONIC = 0.0

# Defence / security signals that separate important PDFs from generic noise.
# Keep aligned with Wire military vocabulary — avoid bare "nuclear/strategy/plan".
_IMPORTANCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bcyber[\s\-]?war(?:fare)?\b",
        r"\bcyber[\s\-]?operations?\b",
        r"\bcyber[\s\-]?command\b",
        r"\belectronic[\s\-]?war(?:fare)?\b",
        r"\binformation[\s\-]?war(?:fare)?\b",
        r"\bnuclear\s+(?:deterrence|triad|weapon|forces?|posture)\b",
        r"\bmilitary\b",
        r"\bdefen[cs]e\b",
        r"\barmed\s+forces?\b",
        r"\bnaval\b",
        r"\bnavy\b",
        r"\barmy\b",
        r"\bair\s+force\b",
        r"\bspace\s+force\b",
        r"\bmissile\b",
        r"\bhypersonic\b",
        r"\buav\b",
        r"\bunmanned\s+(?:aerial|system|vehicle)\b",
        r"\bmilitary\s+doctrine\b",
        r"\bforce\s+posture\b",
        r"\bindo[\s\-]?pacific\b",
        r"\bsouth\s+china\s+sea\b",
        r"\btaiwan\s+strait\b",
        r"\bgray\s+zone\b",
        r"\baukus\b",
        r"\banti[\s\-]?submarine\b",
        r"\bc4isr\b",
        r"\bmaritime\s+security\b",
        r"\bjournal\s+of\s+military\b",
        r"\bmilitary\s+studies?\b",
        r"\bnato\b",
        r"\bpentagon\b",
        r"\bministry\s+of\s+defen[cs]e\b",
        r"\bpla(?:af|n)?\b",
        r"\boffice\s+of\s+naval\s+research\b",
        r"\bonr\b",
        r"\bwarfare\s+leads?\b",
        r"\bsecurity\s+cooperation\b",
        r"\bjoint\s+exercise\b",
        r"\bprocurement\b",
    )
)

# Hosts that strengthen defense PDF confidence (Wire-aligned publishers).
_DEFENSE_HOST_HINTS = (
    ".mil",
    "defense.gov",
    "defence.gov",
    "nato.int",
    "sipri.org",
    "iiss.org",
    "rand.org",
    "csis.org",
    "brookings.edu",
    "mod.go",
    "navy.mil",
    "army.mil",
    "af.mil",
    "usni.org",
    "westpoint.edu",
    "sjms.nu",
    "defense",
    "defence",
    "military",
)

# Academic / generic hosts — never enough alone to clear the importance bar.
_ACADEMIC_HOST_HINTS = (
    "arxiv.org",
    "researchgate",
    "academia.edu",
    "ssrn.com",
    "jstor.org",
    "tandfonline.com",
    "springer",
    "wiley",
    "ieee.org",
    "acm.org",
    "semanticscholar.org",
    "philpapers.org",
)

_TRUSTED_HOST_HINTS = _DEFENSE_HOST_HINTS

# Publishers that regularly post fresh defense PDFs — undated Bing/Yandex hits
# from these hosts may be soft-accepted when Google time filters are unavailable.
_FRESH_PUBLISHER_HOSTS = (
    "congress.gov",
    "crsreports.congress.gov",
    "defense.gov",
    "war.gov",
    "navy.mil",
    "army.mil",
    "af.mil",
    "nato.int",
    "rand.org",
    "csis.org",
    "csis-website-prod",
    "usni.org",
    "iiss.org",
    "sipri.org",
    "atlanticcouncil.org",
    "cnas.org",
    "mod.go.jp",
    "marines.mil",
    "brookings.edu",
)

# Prefer site-scoped dorks so unrestricted engines still surface current pubs.
_PUBLISHER_SITE_DORKS = (
    "congress.gov",
    "rand.org",
    "csis.org",
    "defense.gov",
    "nato.int",
    "usni.org",
)

_PDF_URL_RE = re.compile(r"\.pdf(?:$|[?#])", re.I)
_URL_PATH_DATE_RE = re.compile(
    r"/(?:((?:19|20)\d{2}))/(?:(0?[1-9]|1[0-2]))/(?:(0?[1-9]|[12]\d|3[01]))(?:/|$)"
)
_URL_YEAR_MONTH_RE = re.compile(r"/(?:((?:19|20)\d{2}))/(0?[1-9]|1[0-2])(?:/|$)")
_URL_MONTH_NAME_DATE_RE = re.compile(
    r"/((?:19|20)\d{2})/"
    r"(Jan(?:uary)?|Feb(?:uary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"(?:/(0?[1-9]|[12]\d|3[01]))?(?:/|$)",
    re.I,
)
_URL_FILENAME_DATE_RE = re.compile(
    r"(?:^|[/_\-])((?:19|20)\d{2})[-_]?((?:0[1-9]|1[0-2]))[-_]?((?:0[1-9]|[12]\d|3[01]))?(?:[/_\-.]|$)"
)
# Compact YYYYMMDD in filenames: 20251021_Force_Design…, p20260414_01e.pdf
_URL_COMPACT_DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"
)
# Month-name + year in filenames: Force_Design_Update-October_2025.pdf / 2025_Oct
_URL_MONTH_YEAR_RE = re.compile(
    r"(?:^|[/_\-\s])("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")[_:\-\s]+((?:19|20)\d{2})(?![0-9])",
    re.I,
)
_URL_YEAR_MONTH_NAME_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[_:\-\s]+("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")(?![a-z])",
    re.I,
)
# Bare year token in path/filename (treated as Jan 1 — conservative age gate).
_URL_YEAR_TOKEN_RE = re.compile(
    r"(?:^|[/_\-\s])((?:19|20)\d{2})(?=[/_\-\s.]|$)"
)
# Paths that usually serve PDFs even when the URL omits a .pdf suffix.
# Keep narrow — broad tokens like /articles/ match HTML news pages.
_PDF_PATH_HINT_RE = re.compile(
    r"/(?:media/document|content/dam|crs_external_products|"
    r"download(?:s)?/|attachments?/|portals?/[^/]+/docs?/)/",
    re.I,
)
# Explicit /PDF/ segments (Congress CRS, arXiv, many .gov portals).
_PDF_DIR_RE = re.compile(r"/pdf(?:/|$)", re.I)
# HTML news / magazine paths — never treat as documents without .pdf.
_HTML_ARTICLE_PATH_RE = re.compile(
    r"/(?:articles?|news|story|blog|posts?|press-releases?)/",
    re.I,
)
_RELATIVE_AGE_RE = re.compile(
    r"(?i)(?:^|[^\w])(\d+)\s*"
    r"(minute|minutes|hour|hours|day|days|week|weeks|month|months)\s+ago\b"
)
_ABS_ISO_DATE_RE = re.compile(
    r"\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b"
)
_ABS_MONTH_DATE_RE = re.compile(
    r"\b("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")\s+(\d{1,2})(?:,)?\s+((?:19|20)\d{2})\b",
    re.I,
)
_MONTH_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_HEAD_PDF_CACHE: dict[str, bool] = {}


def document_scan_enabled() -> bool:
    return bool(getattr(settings, "DOCUMENT_SCAN_ENABLED", False))


def document_scan_max_age_days() -> int:
    return max(1, int(getattr(settings, "DOCUMENT_SCAN_MAX_AGE_DAYS", 30) or 30))


def document_scan_min_importance() -> int:
    return max(0, int(getattr(settings, "DOCUMENT_SCAN_MIN_IMPORTANCE", 40) or 40))


def document_scan_engines() -> str:
    raw = (getattr(settings, "DOCUMENT_SCAN_ENGINES", "") or "").strip()
    # Google first when healthy (week/day filters); Bing/Yandex as undated fallbacks.
    return raw or "google,bing,yandex"


def document_scan_limit_per_keyword() -> int:
    return max(1, min(40, int(getattr(settings, "DOCUMENT_SCAN_LIMIT_PER_KEYWORD", 12) or 12)))


def document_scan_parallelism() -> int:
    return max(1, min(8, int(getattr(settings, "DOCUMENT_SCAN_PARALLELISM", 2) or 2)))


def document_scan_keyword_cooldown_minutes() -> int:
    return max(0, int(getattr(settings, "DOCUMENT_SCAN_KEYWORD_COOLDOWN_MINUTES", 25) or 25))


def document_scan_query_delay_sec() -> float:
    return max(0.0, float(getattr(settings, "DOCUMENT_SCAN_QUERY_DELAY_SEC", 2.0) or 0.0))


def document_scan_max_keywords_per_run() -> int:
    """Cap per sweep so one beat cannot monopolize the Celery worker."""
    base = max(5, min(60, int(getattr(settings, "DOCUMENT_SCAN_MAX_KEYWORDS_PER_RUN", 22) or 22)))
    try:
        from apps.integrations.searx.google_dork_browser import google_browser_enabled

        if google_browser_enabled():
            browser_cap = max(
                3,
                int(getattr(settings, "DOCUMENT_SCAN_GOOGLE_BROWSER_MAX_KEYWORDS", 12) or 12),
            )
            return min(base, browser_cap)
    except Exception:  # noqa: BLE001
        pass
    return base


def document_scan_target_created_per_run() -> int:
    """Early-stop after enough new docs — avoids long empty tails."""
    return max(1, min(40, int(getattr(settings, "DOCUMENT_SCAN_TARGET_CREATED", 10) or 10)))


def document_scan_fallback_max_age_days() -> int:
    """
    Upper bound for trusted publishers when engines cannot apply month filters.
    Defaults to the same 1-month window as Google advanced search (Past month).
    """
    primary = document_scan_max_age_days()
    raw = int(getattr(settings, "DOCUMENT_SCAN_FALLBACK_MAX_AGE_DAYS", primary) or primary)
    return max(primary, min(62, raw))


def _pace_searx_query() -> None:
    """Serialize Searx calls across threads with a minimum inter-query gap."""
    global _SEARX_LAST_MONOTONIC
    delay = document_scan_query_delay_sec()
    if delay <= 0:
        return
    with _SEARX_PACE_LOCK:
        now = time.monotonic()
        wait = _SEARX_LAST_MONOTONIC + delay - now
        if wait > 0:
            time.sleep(wait)
        _SEARX_LAST_MONOTONIC = time.monotonic()


def _engine_cooldown_key(engine: str) -> str:
    return f"bs:docscan:engine_cool:{engine.strip().lower()}"


def _mark_engines_unresponsive(unresponsive: list[Any]) -> None:
    """Record Searx suspensions so we stop hammering blocked engines."""
    try:
        from apps.core.task_lock import _redis_client

        client = _redis_client()
    except Exception:  # noqa: BLE001
        return
    for item in unresponsive or []:
        name = ""
        reason = ""
        if isinstance(item, (list, tuple)) and item:
            name = str(item[0] or "")
            reason = str(item[1] or "") if len(item) > 1 else ""
        elif isinstance(item, str):
            name = item
        lowered = name.casefold()
        if not lowered:
            continue
        # Map "google cse" → google cooldown.
        engine = "google" if "google" in lowered else lowered.split()[0]
        ttl = 300
        if "captcha" in reason.casefold():
            ttl = 900
        elif "too many" in reason.casefold() or "suspend" in reason.casefold():
            ttl = 600
        elif "timeout" in reason.casefold():
            ttl = 180
        try:
            client.setex(_engine_cooldown_key(engine), ttl, reason or "unresponsive")
        except Exception:  # noqa: BLE001
            continue


def _engine_on_cooldown(engine: str) -> bool:
    try:
        from apps.core.task_lock import _redis_client

        return bool(_redis_client().get(_engine_cooldown_key(engine)))
    except Exception:  # noqa: BLE001
        return False


def _time_ranges_for_engine(engine: str, preferred: str) -> list[str]:
    """
    Bing/Brave/DDG often return ZERO hits when time_range is set.
    Google can use month (Past month ≈ advanced search); finish with unrestricted (\"\").
    """
    eng = (engine or "").lower()
    if eng in {"bing", "brave", "duckduckgo", "qwant", "startpage", "yandex", "semantic scholar"}:
        return [""]
    ranges: list[str] = []
    for tr in ("month", preferred, "week", ""):
        if tr == "" or tr in {"day", "week", "month", "year"}:
            if tr not in ranges:
                ranges.append(tr)
    return ranges


def _google_after_clause(*, days: int | None = None) -> str:
    window = max(1, int(days or document_scan_max_age_days()))
    cut = timezone.now() - timedelta(days=window)
    return f"after:{cut.date().isoformat()}"


_AFTER_CLAUSE_RE = re.compile(r"\bafter:\d{4}-\d{2}-\d{2}\b", re.I)


def strip_google_after_clause(query: str) -> str:
    """Bing/Yandex do not honor Google ``after:YYYY-MM-DD`` — it zeros recall."""
    text = _AFTER_CLAUSE_RE.sub(" ", query or "")
    return " ".join(text.split())


def _document_query_variants(keyword: str, filetypes: str = "pdf") -> list[str]:
    """Phrase-exact dork variants biased toward recent publisher PDFs.

    First variant is the clean phrase dork (no ``after:``) so Bing/Chromium
    can use engine-native month filters. Publisher ``site:`` scopes follow for
    recall when open-web filetype:pdf rankings are noisy.
    """
    phrase = normalize_document_keyword(keyword)
    if not phrase:
        return []
    quoted = quote_document_phrase(phrase)
    primary = build_document_query(phrase, filetypes)
    after = _google_after_clause()
    year = str(timezone.now().year)
    variants = [
        primary,
        f"{primary} {year}".strip(),
    ]
    if "pdf" in (filetypes or "pdf").lower() and quoted:
        for site in _PUBLISHER_SITE_DORKS:
            variants.append(f"{quoted} filetype:pdf site:{site}")
        variants.append(f"{primary} {after}".strip())
        variants.append(f"{quoted} {after} filetype:pdf")
    seen: set[str] = set()
    out: list[str] = []
    for q in variants:
        q = " ".join((q or "").split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# Searx/Google works best with short topical phrases (not full document titles).
DOCUMENT_KEYWORD_MAX_WORDS = 2
DOCUMENT_KEYWORD_MIN_WORDS = 2
DOCUMENT_KEYWORD_MAX_CHARS = 64


def normalize_document_keyword(keyword: str) -> str:
    """Collapse whitespace; strip accidental quotes / filetype operators."""
    phrase = " ".join((keyword or "").split())
    # Users sometimes paste a full dork — keep only the phrase part.
    lowered = phrase.casefold()
    if " filetype:" in lowered:
        phrase = phrase[: lowered.index(" filetype:")].strip()
    # Strip wrapping quotes so we can re-apply phrase quotes consistently.
    if len(phrase) >= 2 and phrase[0] == '"' and phrase[-1] == '"':
        phrase = phrase[1:-1].strip()
    phrase = phrase.replace('"', "")
    return phrase[:DOCUMENT_KEYWORD_MAX_CHARS]


def document_keyword_too_long(keyword: str) -> bool:
    """True when the phrase is not exactly two words (required for scan dorks)."""
    words = [w for w in normalize_document_keyword(keyword).split() if w]
    return len(words) != DOCUMENT_KEYWORD_MAX_WORDS


def quote_document_phrase(keyword: str) -> str:
    """
    Google-dork phrase match: multi-word keywords become \"information warfare\".

    Single tokens stay bare (AUKUS, hypersonic). Empty → \"\".
    """
    phrase = normalize_document_keyword(keyword)
    if not phrase:
        return ""
    words = [w for w in phrase.split() if w]
    if len(words) >= 2:
        return f'"{phrase}"'
    return phrase


def build_document_query(keyword: str, filetypes: str = "pdf") -> str:
    """Build ``\"information warfare\" filetype:pdf`` style dorks."""
    phrase = quote_document_phrase(keyword)
    types = [
        t.strip().lstrip(".").lower()
        for t in (filetypes or "pdf").split(",")
        if t.strip()
    ] or ["pdf"]
    if not phrase:
        return ""
    if len(types) == 1:
        return f"{phrase} filetype:{types[0]}".strip()
    joined = " OR ".join(f"filetype:{t}" for t in types)
    return f"{phrase} ({joined})".strip()


def normalize_document_url(url: str) -> str:
    """Canonical URL for dedupe: strip fragment + tracking query; normalize CRS."""
    text = (url or "").strip()
    if not text:
        return ""
    text = rewrite_crs_pdf_url(text) or text
    try:
        parsed = urlparse(text)
    except ValueError:
        return text[:2048]
    # Drop tracking / cache-buster query params that create duplicate rows.
    drop_keys = {
        "ver",
        "ved",
        "usg",
        "sa",
        "source",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
    }
    query_pairs = []
    if parsed.query:
        from urllib.parse import parse_qsl, urlencode

        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.casefold() in drop_keys:
                continue
            query_pairs.append((key, value))
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    # Collapse CRS versioned duplicates to the product stem when possible.
    dedupe = document_dedupe_key(text)
    if dedupe.startswith("crs:"):
        # Prefer rewritten congress.gov PDF when we have series/id/ver.
        return (rewrite_crs_pdf_url(text) or text.split("#", 1)[0])[:2048]
    rebuilt = parsed._replace(
        scheme=(parsed.scheme or "https").lower(),
        netloc=host,
        path=path.rstrip("/") if path != "/" else path,
        params="",
        query=urlencode(query_pairs) if query_pairs else "",
        fragment="",
    )
    from urllib.parse import urlunparse

    return urlunparse(rebuilt)[:2048]


_CRS_PRODUCT_RE = re.compile(
    r"(?i)https?://(?:www\.)?crsreports\.congress\.gov/product/pdf/"
    r"(?P<series>[A-Za-z]+)/(?P<doc_id>[A-Za-z]{1,3}\d+)(?:/(?P<ver>\d+))?(?:/|\.pdf)?(?:\?|$)"
)
_CRS_EXTERNAL_RE = re.compile(
    r"(?i)https?://(?:www\.)?congress\.gov/crs_external_products/"
    r"(?P<series>[A-Za-z]+)/PDF/(?P<doc_id>[^/]+)/(?P<file>[^/?#]+?)(?:\.pdf)?(?:\?|$)"
)


def rewrite_crs_pdf_url(url: str) -> str:
    """
    Map blocked ``crsreports.congress.gov/product/pdf/...`` links to the public
    ``congress.gov/crs_external_products/.../*.pdf`` mirrors (application/pdf).
    """
    text = (url or "").strip()
    if not text:
        return ""
    match = _CRS_PRODUCT_RE.search(text)
    if match:
        series = match.group("series").upper()
        doc_id = match.group("doc_id").upper()
        ver = match.group("ver")
        filename = f"{doc_id}.{ver}.pdf" if ver else f"{doc_id}.pdf"
        return (
            f"https://www.congress.gov/crs_external_products/"
            f"{series}/PDF/{doc_id}/{filename}"
        )
    # Already on congress.gov external products — ensure .pdf suffix.
    ext = _CRS_EXTERNAL_RE.search(text)
    if ext:
        series = ext.group("series").upper()
        doc_id = ext.group("doc_id")
        file_stem = ext.group("file")
        if not file_stem.lower().endswith(".pdf"):
            file_stem = f"{file_stem}.pdf"
        return (
            f"https://www.congress.gov/crs_external_products/"
            f"{series}/PDF/{doc_id}/{file_stem}"
        )
    return ""


def document_dedupe_key(url: str) -> str:
    """Stable key so CRS IF10250/45 and /46 collapse to one product."""
    text = (url or "").strip()
    if not text:
        return ""
    match = _CRS_PRODUCT_RE.search(text) or _CRS_EXTERNAL_RE.search(text)
    if match:
        series = (match.group("series") or "").upper()
        doc_id = (match.group("doc_id") or "").upper()
        return f"crs:{series}:{doc_id}"
    try:
        parsed = urlparse(text)
    except ValueError:
        return text.casefold()[:2048]
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "").rstrip("/").casefold()
    return f"{host}{path}"[:2048]


def _is_trusted_doc_host(host: str) -> bool:
    h = (host or "").lower()
    if not h:
        return False
    if h.endswith(".mil") or h.endswith(".gov") or h.endswith(".edu"):
        return True
    return any(hint in h for hint in _TRUSTED_HOST_HINTS)


def validate_live_pdf(url: str) -> tuple[bool, str]:
    """
    Confirm the URL is a reachable PDF (2xx/206 + application/pdf or %PDF magic).

    Returns (ok, reason).
    """
    key = (url or "").strip()
    if not key:
        return False, "empty"
    cache_key = f"live:{key}"
    if cache_key in _HEAD_PDF_CACHE:
        cached = _HEAD_PDF_CACHE[cache_key]
        return bool(cached), "cache_hit" if cached else "cache_miss_dead"
    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; NewsCrawlerDocScan/1.0; +defense-intel)"
            ),
            "Accept": "application/pdf,*/*;q=0.8",
        }
        with httpx.Client(timeout=12.0, follow_redirects=True, headers=headers) as client:
            response = client.get(key, headers={**headers, "Range": "bytes=0-1023"})
            status = response.status_code
            if status >= 400:
                _HEAD_PDF_CACHE[cache_key] = False
                return False, f"http_{status}"
            ctype = (response.headers.get("content-type") or "").lower()
            body = response.content or b""
            magic = body.lstrip().startswith(b"%PDF")
            ctype_ok = "application/pdf" in ctype or ctype.endswith("/pdf")
            if magic or ctype_ok:
                _HEAD_PDF_CACHE[cache_key] = True
                return True, "pdf"
            _HEAD_PDF_CACHE[cache_key] = False
            return False, f"not_pdf:{ctype[:40] or 'unknown'}"
    except Exception as exc:  # noqa: BLE001
        _HEAD_PDF_CACHE[cache_key] = False
        return False, f"error:{exc.__class__.__name__}"


def _probe_is_pdf(url: str) -> bool:
    """HEAD/GET check that the URL serves a PDF body."""
    ok, _reason = validate_live_pdf(url)
    return ok


def _looks_like_document_url(url: str, *, filetype: str = "pdf", probe: bool = False) -> bool:
    """True only for real document URLs (prefer ``.pdf``), not HTML article pages."""
    text = (url or "").strip()
    if not text:
        return False
    lower = text.lower()
    ft = (filetype or "pdf").lower()
    if ft != "pdf":
        return f".{ft}" in lower.split("?")[0]
    # Hard accept: explicit PDF file / filetype markers.
    if _PDF_URL_RE.search(lower) or "filetype=pdf" in lower or "type=pdf" in lower:
        return True
    if ".pdf" in lower.split("?")[0]:
        return True
    host = _host(text)
    path = ""
    try:
        path = (urlparse(text).path or "").lower()
    except ValueError:
        path = ""
    # Reject HTML news/magazine landing pages even on defense hosts.
    if _HTML_ARTICLE_PATH_RE.search(path):
        return False
    # arXiv / CRS-style /pdf/ directories (file may omit .pdf suffix).
    if _PDF_DIR_RE.search(path):
        return True
    # Official portals publish PDFs under /media/document/... without .pdf.
    if _PDF_PATH_HINT_RE.search(path) and _is_trusted_doc_host(host):
        return True
    if probe and _is_trusted_doc_host(host) and (
        "/media/" in path or "/document" in path or "/strategy" in path or "/pdf" in path
    ):
        return _probe_is_pdf(text)
    return False


def _display_path(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return (url or "")[:1024]
    path = unquote(parsed.path or "")
    if parsed.query:
        return f"{path}?{parsed.query}"[:1024]
    return path[:1024] or (url or "")[:1024]


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _source_from_engine(engine: str) -> str:
    eng = (engine or "").lower()
    if "google" in eng:
        return ScannedDocument.Source.GOOGLE
    if "bing" in eng:
        return ScannedDocument.Source.BING
    if "brave" in eng:
        return ScannedDocument.Source.BRAVE
    if "duck" in eng:
        return ScannedDocument.Source.DUCKDUCKGO
    if eng:
        return ScannedDocument.Source.OTHER
    return ScannedDocument.Source.SEARX


def _is_defense_doc_host(host: str) -> bool:
    h = (host or "").lower()
    if not h:
        return False
    if h.endswith(".mil") or h.endswith(".gov"):
        # Prefer defense-ish .gov; still allow broad .gov with other gates.
        return True
    return any(hint in h for hint in _DEFENSE_HOST_HINTS)


def _is_academic_doc_host(host: str) -> bool:
    h = (host or "").lower()
    return bool(h) and any(hint in h for hint in _ACADEMIC_HOST_HINTS)


def _is_fresh_publisher_host(host: str) -> bool:
    h = (host or "").lower()
    if not h:
        return False
    if h.endswith(".mil") or h.endswith(".gov"):
        return True
    return any(hint in h for hint in _FRESH_PUBLISHER_HOSTS)


def _parse_url_path_date(url: str) -> datetime | None:
    """Best (most specific) publish-ish date embedded in a PDF URL/filename."""
    dates = _iter_url_path_dates(url)
    if not dates:
        return None
    # Prefer day-level dates over year-only Jan 1 placeholders.
    dates_sorted = sorted(
        dates,
        key=lambda d: (d.day != 1 or d.month != 1, d.month != 1, d),
        reverse=True,
    )
    return dates_sorted[0]


def _aware(year: int, month: int, day: int = 1) -> datetime | None:
    try:
        return timezone.make_aware(
            datetime(year, month, day), timezone.get_current_timezone()
        )
    except ValueError:
        return None


def _iter_url_path_dates(url: str) -> list[datetime]:
    """All date cues from a URL path/filename (unquoted)."""
    text = (url or "").strip()
    if not text:
        return []
    try:
        path = urlparse(text).path or text
    except ValueError:
        path = text
    path = unquote(path)
    found: list[datetime] = []

    for named in _URL_MONTH_NAME_DATE_RE.finditer(path):
        try:
            year = int(named.group(1))
            month = _MONTH_NUM.get(named.group(2).lower())
            day = int(named.group(3) or 1)
            dt = _aware(year, month, day) if month else None
            if dt:
                found.append(dt)
        except ValueError:
            continue

    for full in _URL_PATH_DATE_RE.finditer(path):
        dt = _aware(int(full.group(1)), int(full.group(2)), int(full.group(3)))
        if dt:
            found.append(dt)

    for ym in _URL_YEAR_MONTH_RE.finditer(path):
        dt = _aware(int(ym.group(1)), int(ym.group(2)), 1)
        if dt:
            found.append(dt)

    for fn in _URL_FILENAME_DATE_RE.finditer(path):
        day = int(fn.group(3) or 1)
        dt = _aware(int(fn.group(1)), int(fn.group(2)), day)
        if dt:
            found.append(dt)

    for compact in _URL_COMPACT_DATE_RE.finditer(path):
        dt = _aware(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))
        if dt:
            found.append(dt)

    for my in _URL_MONTH_YEAR_RE.finditer(path):
        month = _MONTH_NUM.get(my.group(1).lower())
        dt = _aware(int(my.group(2)), month, 1) if month else None
        if dt:
            found.append(dt)

    for ym2 in _URL_YEAR_MONTH_NAME_RE.finditer(path):
        month = _MONTH_NUM.get(ym2.group(2).lower())
        dt = _aware(int(ym2.group(1)), month, 1) if month else None
        if dt:
            found.append(dt)

    # Year-only tokens only when no stronger date was found on this path.
    # Skip the *current* calendar year alone — it is not proof of Jan 1 and
    # would falsely stale-reject every ``…/2026/….pdf`` mid-year.
    if not found:
        current_year = timezone.now().year
        for year_m in _URL_YEAR_TOKEN_RE.finditer(path):
            year = int(year_m.group(1))
            if year >= current_year:
                continue
            dt = _aware(year, 1, 1)
            if dt:
                found.append(dt)

    return found


def _iter_text_dates(text: str, *, now: datetime | None = None) -> list[datetime]:
    if not text:
        return []
    found: list[datetime] = []
    relative = _parse_relative_age(text, now=now)
    if relative is not None:
        found.append(relative)
    absolute = _parse_absolute_date(text)
    if absolute is not None:
        found.append(absolute)
    for compact in _URL_COMPACT_DATE_RE.finditer(text):
        dt = _aware(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))
        if dt:
            found.append(dt)
    for my in _URL_MONTH_YEAR_RE.finditer(text):
        month = _MONTH_NUM.get(my.group(1).lower())
        dt = _aware(int(my.group(2)), month, 1) if month else None
        if dt:
            found.append(dt)
    for ym2 in _URL_YEAR_MONTH_NAME_RE.finditer(text):
        month = _MONTH_NUM.get(ym2.group(2).lower())
        dt = _aware(int(ym2.group(1)), month, 1) if month else None
        if dt:
            found.append(dt)
    return found


def _choose_publish_date(candidates: list[datetime], *, now: datetime, max_age_days: int) -> datetime | None:
    """
    Pick a publish date for gating.

    Prefer a date inside the retention window when one exists; otherwise return
    the newest candidate so the age gate can reject stale docs.
    """
    if not candidates:
        return None
    cut = now - timedelta(days=max(1, max_age_days))
    in_window = [d for d in candidates if d >= cut]
    if in_window:
        return max(in_window)
    return max(candidates)


def is_document_topic_relevant(
    *,
    title: str,
    snippet: str,
    url: str,
    keyword: str = "",
) -> bool:
    """
    Mirror Dòng tin topic doctrine: military/cyber-defense AND
    (monitored geography OR defense publisher/org cue).
    """
    from apps.workers.services import (
        is_military_context,
        is_military_cyber_context,
        is_monitored_country_context,
    )

    blob = " ".join(
        str(part or "") for part in (title, snippet, url, keyword)
    ).casefold()
    content_blob = " ".join(
        str(part or "") for part in (title, snippet, url)
    ).casefold()
    kw = (keyword or "").strip()
    kw_fold = kw.casefold()
    host = _host(url)

    military_ok = is_military_context(blob) or is_military_cyber_context(blob)
    if not military_ok:
        # Geography keywords (Taiwan Strait, Philippine Sea) are curated topics —
        # accept when content/host still signals defense publishing.
        if kw_fold and is_monitored_country_context(kw_fold) and (
            is_military_context(content_blob)
            or is_military_cyber_context(content_blob)
            or _is_defense_doc_host(host)
            or _is_fresh_publisher_host(host)
        ):
            return True
        return False

    # Curated military/cyber keywords: require the signal in title/snippet/URL so
    # off-topic academic PDFs are not kept just because the keyword was injected.
    if kw_fold and (
        is_military_context(content_blob) or is_military_cyber_context(content_blob)
    ):
        return True
    if _is_academic_doc_host(host) and not (
        is_monitored_country_context(blob)
        or re.search(
            r"\b(nato|aukus|pentagon|pla|jsdf|onr|indopacom|cyber\s+command|"
            r"south\s+china\s+sea|taiwan\s+strait|indo[\s\-]?pacific)\b",
            blob,
        )
    ):
        return False

    if is_monitored_country_context(blob):
        return True
    if _is_defense_doc_host(host):
        return True
    # Defense analysis outlets / journals (Wire "analysis" topic) without a country name.
    if re.search(
        r"\b("
        r"journal\s+of\s+military|military\s+studies|naval\s+war\s+college|"
        r"defense\s+analysis|defence\s+analysis|strategic\s+studies|"
        r"war\s+studies|scandinavian\s+journal\s+of\s+military"
        r")\b",
        blob,
    ):
        return True
    # Doctrine / alliance / force cues without an explicit country name.
    return bool(
        re.search(
            r"\b("
            r"nato|aukus|pentagon|onr|indopacom|quad|"
            r"pla(?:af|n)?|jsdf|self[\s\-]?defense\s+forces?|"
            r"cyber\s+command|force\s+posture|security\s+cooperation|"
            r"maritime\s+security|electronic\s+warfare|information\s+warfare|"
            r"nuclear\s+(?:deterrence|triad|age)|hypersonic|ballistic\s+missile|"
            r"carrier\s+strike|anti[\s\-]?submarine|c4isr|third\s+nuclear\s+age"
            r")\b",
            blob,
        )
    )


def _importance_score(
    *,
    title: str,
    snippet: str,
    url: str,
    keyword: str,
) -> int:
    blob = f"{title}\n{snippet}\n{url}"
    score = 0
    if _looks_like_document_url(url):
        score += 20
    host = _host(url)
    if _is_defense_doc_host(host):
        score += 30
    elif any(hint in url.lower() for hint in ("military", "defence", "defense", "nato")):
        score += 15
    elif _is_academic_doc_host(host):
        score -= 15
    for pattern in _IMPORTANCE_PATTERNS:
        if pattern.search(blob):
            score += 8
    kw = (keyword or "").strip().lower()
    if kw:
        tokens = [t for t in re.split(r"\s+", kw) if len(t) > 2]
        lower_blob = blob.lower()
        hits = sum(1 for t in tokens if t in lower_blob)
        if hits:
            score += min(20, hits * 6)
        if kw in lower_blob:
            score += 10
    if is_document_topic_relevant(
        title=title, snippet=snippet, url=url, keyword=keyword
    ):
        score += 15
    return max(0, min(100, score))


def _parse_relative_age(text: str, *, now: datetime | None = None) -> datetime | None:
    """Parse Google-style '2 days ago' / '3 hours ago' / '1 month ago' from snippets."""
    if not text:
        return None
    match = _RELATIVE_AGE_RE.search(str(text).strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    base = now or timezone.now()
    if unit.startswith("minute"):
        delta = timedelta(minutes=amount)
    elif unit.startswith("hour"):
        delta = timedelta(hours=amount)
    elif unit.startswith("day"):
        delta = timedelta(days=amount)
    elif unit.startswith("week"):
        delta = timedelta(weeks=amount)
    elif unit.startswith("month"):
        delta = timedelta(days=30 * amount)
    else:
        return None
    return base - delta


def _parse_absolute_date(text: str) -> datetime | None:
    if not text:
        return None
    raw = str(text)
    iso = _ABS_ISO_DATE_RE.search(raw)
    if iso:
        try:
            year, month, day = (int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            return timezone.make_aware(
                datetime(year, month, day), timezone.get_current_timezone()
            )
        except ValueError:
            pass
    mon = _ABS_MONTH_DATE_RE.search(raw)
    if mon:
        try:
            month = _MONTH_NUM.get(mon.group(1).lower())
            day = int(mon.group(2))
            year = int(mon.group(3))
            if not month:
                return None
            return timezone.make_aware(
                datetime(year, month, day), timezone.get_current_timezone()
            )
        except ValueError:
            return None
    return None


def _parse_published(
    value: Any,
    *,
    snippet: str = "",
    title: str = "",
    url: str = "",
    now: datetime | None = None,
) -> datetime | None:
    """Best publish date from SERP fields + title/snippet/URL evidence."""
    base_now = now or timezone.now()
    published, _src = _resolve_date_evidence(
        value,
        snippet=snippet,
        title=title,
        url=url,
        now=base_now,
    )
    return published


def _calendar_dates_from_text(text: str) -> list[datetime]:
    """Absolute calendar cues only (not relative 'N days ago')."""
    if not text:
        return []
    found: list[datetime] = []
    absolute = _parse_absolute_date(text)
    if absolute is not None:
        found.append(absolute)
    for compact in _URL_COMPACT_DATE_RE.finditer(text):
        dt = _aware(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))
        if dt:
            found.append(dt)
    for my in _URL_MONTH_YEAR_RE.finditer(text):
        month = _MONTH_NUM.get(my.group(1).lower())
        dt = _aware(int(my.group(2)), month, 1) if month else None
        if dt:
            found.append(dt)
    for ym2 in _URL_YEAR_MONTH_NAME_RE.finditer(text):
        month = _MONTH_NUM.get(ym2.group(2).lower())
        dt = _aware(int(ym2.group(1)), month, 1) if month else None
        if dt:
            found.append(dt)
    return found


def _serp_dates_from_value(value: Any, *, now: datetime) -> list[datetime]:
    found: list[datetime] = []
    ts = parse_published_ts(value)
    if ts is not None:
        found.append(datetime.fromtimestamp(ts, tz=dt_timezone.utc))
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        dt = parse_datetime(raw)
        if dt is not None:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            found.append(dt)
        found.extend(_calendar_dates_from_text(raw))
        rel = _parse_relative_age(raw, now=now)
        if rel is not None:
            found.append(rel)
    return found


def _resolve_date_evidence(
    value: Any,
    *,
    snippet: str,
    title: str,
    url: str,
    now: datetime,
) -> tuple[datetime | None, str]:
    """
    Document-intrinsic dates (URL/title/snippet calendar) beat SERP crawl dates.

    Google often labels an old PDF with today's SERP date; if the filename says
    October 2025 / 2006, trust that and let the age gate reject it.
    """
    intrinsic: list[datetime] = []
    intrinsic.extend(_iter_url_path_dates(url))
    intrinsic.extend(_calendar_dates_from_text(title or ""))
    intrinsic.extend(_calendar_dates_from_text(snippet or ""))

    serp: list[datetime] = []
    serp.extend(_serp_dates_from_value(value, now=now))
    rel_title = _parse_relative_age(title or "", now=now)
    if rel_title is not None:
        serp.append(rel_title)
    rel_snip = _parse_relative_age(snippet or "", now=now)
    if rel_snip is not None:
        serp.append(rel_snip)

    cut = now - timedelta(days=max(1, document_scan_max_age_days()))

    if intrinsic:
        in_window = [d for d in intrinsic if d >= cut]
        if in_window:
            return max(in_window), "parsed"
        # All embedded document dates are outside the 1-month window.
        return max(intrinsic), "parsed"

    if serp:
        chosen = _choose_publish_date(serp, now=now, max_age_days=document_scan_max_age_days())
        return chosen, "parsed"

    return None, "missing"


def _resolve_hit_published(
    hit: dict[str, Any],
    *,
    title: str,
    snippet: str,
    url: str,
    now: datetime,
) -> tuple[datetime | None, str]:
    """
    Return (published_at, provenance).

    Strict: only explicit dates from SERP / title / snippet / URL count.
    Never soft-accept ``now`` for trusted hosts — that admitted stale CRS PDFs.
    """
    published, src = _resolve_date_evidence(
        hit.get("published"),
        snippet=snippet,
        title=title,
        url=url,
        now=now,
    )
    if published is not None:
        return published, src
    return None, "missing"


def _age_ok_for_hit(
    published: datetime | None,
    *,
    url: str,
    now: datetime,
    primary_days: int,
    fallback_days: int,
) -> tuple[bool, str]:
    """Strict ~30-day gate — requires a real parsed publish date."""
    del url, fallback_days
    if published is None:
        return False, "missing"
    if published >= now - timedelta(days=primary_days):
        return True, "primary"
    return False, "stale"


def _within_max_age(published: datetime | None, *, max_age_days: int, now: datetime) -> bool:
    """Only keep docs with a known publish time inside the retention window."""
    if published is None:
        return False
    return published >= now - timedelta(days=max_age_days)


def _effective_doc_publish_date(
    *,
    published_at: datetime | None,
    title: str,
    summary: str,
    source_url: str,
    metadata: dict[str, Any] | None,
    now: datetime,
) -> tuple[datetime | None, str]:
    """
    Recompute publish evidence for purge.

    Soft-accepted timestamps (engine_time_range / year cue) are ignored — only
    real SERP/URL/title dates count.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    evidence, src = _resolve_date_evidence(
        "",
        snippet=summary or "",
        title=title or "",
        url=source_url or "",
        now=now,
    )
    if evidence is not None:
        return evidence, src if src != "missing" else "evidence"
    soft = (meta.get("date_source") or "") in {
        "engine_time_range",
        "fresh_publisher_year_cue",
    }
    if soft:
        return None, "unreliable_soft"
    if published_at is not None:
        # Stored value may itself be a soft-accept "now"; still allow if no
        # contradictory intrinsic date was found above.
        return published_at, "stored"
    return None, "missing"


def purge_stale_scanned_documents(*, max_age_days: int | None = None) -> dict[str, int]:
    """Delete scanned docs with missing/too-old publish evidence, dead PDFs, or off-topic."""
    days = max(
        1,
        int(
            max_age_days
            if max_age_days is not None
            else document_scan_max_age_days()
        ),
    )
    now = timezone.now()
    cut = now - timedelta(days=days)

    # First pass: classic published_at column + soft-accept provenance.
    soft_ids = []
    for doc in ScannedDocument.objects.only("id", "metadata").iterator():
        meta = doc.metadata if isinstance(doc.metadata, dict) else {}
        if (meta.get("date_source") or "") in {
            "engine_time_range",
            "fresh_publisher_year_cue",
        }:
            soft_ids.append(doc.id)
    deleted_soft = 0
    if soft_ids:
        deleted_soft, _ = ScannedDocument.objects.filter(id__in=soft_ids).delete()

    stale = ScannedDocument.objects.filter(
        Q(published_at__isnull=True) | Q(published_at__lt=cut)
    )
    deleted_stale, _breakdown = stale.delete()

    # Second pass: re-evaluate remaining rows by evidence date.
    recheck_ids: list[int] = []
    block_urls: list[tuple[str, str]] = []
    dead_ids: list[int] = []
    for doc in ScannedDocument.objects.only(
        "id", "title", "summary", "source_url", "published_at", "metadata"
    ).iterator():
        evidence, src = _effective_doc_publish_date(
            published_at=doc.published_at,
            title=doc.title or "",
            summary=doc.summary or "",
            source_url=doc.source_url or "",
            metadata=doc.metadata if isinstance(doc.metadata, dict) else {},
            now=now,
        )
        if not _within_max_age(evidence, max_age_days=days, now=now):
            recheck_ids.append(doc.id)
            if doc.source_url:
                block_urls.append((doc.source_url, doc.title or ""))
            continue
        live_ok, live_reason = validate_live_pdf(doc.source_url or "")
        if not live_ok:
            dead_ids.append(doc.id)
            if doc.source_url:
                block_urls.append((doc.source_url, doc.title or ""))
            logger.info(
                "purge dead pdf id=%s reason=%s url=%s",
                doc.id,
                live_reason,
                (doc.source_url or "")[:120],
            )

    deleted_recheck = 0
    if recheck_ids:
        deleted_recheck, _ = ScannedDocument.objects.filter(id__in=recheck_ids).delete()
    deleted_dead = 0
    if dead_ids:
        deleted_dead, _ = ScannedDocument.objects.filter(id__in=dead_ids).delete()
    for url, title in block_urls:
        try:
            block_scanned_document_url(url, title=title, reason="stale_or_dead_pdf")
        except Exception:  # noqa: BLE001
            pass

    off_topic_ids: list[int] = []
    for doc in ScannedDocument.objects.only(
        "id", "title", "summary", "source_url", "matched_keyword"
    ).iterator():
        if not is_document_topic_relevant(
            title=doc.title or "",
            snippet=doc.summary or "",
            url=doc.source_url or "",
            keyword=doc.matched_keyword or "",
        ):
            off_topic_ids.append(doc.id)
    deleted_off = 0
    if off_topic_ids:
        deleted_off, _ = ScannedDocument.objects.filter(id__in=off_topic_ids).delete()

    return {
        "deleted": int(deleted_soft)
        + int(deleted_stale)
        + int(deleted_recheck)
        + int(deleted_dead)
        + int(deleted_off),
        "deleted_stale": int(deleted_stale) + int(deleted_recheck) + int(deleted_soft),
        "deleted_dead": int(deleted_dead),
        "deleted_off_topic": int(deleted_off),
        "max_age_days": days,
        "cut": cut.isoformat(),
    }


def _load_known_document_urls() -> set[str]:
    known: set[str] = set()
    for raw in ScannedDocument.objects.values_list("source_url", flat=True):
        url = normalize_document_url(raw or "")
        if url:
            known.add(url)
            known.add(document_dedupe_key(url))
    try:
        from apps.intel.models import BlockedScannedDocumentUrl

        for raw in BlockedScannedDocumentUrl.objects.values_list("source_url", flat=True):
            url = normalize_document_url(raw or "")
            if url:
                known.add(url)
                known.add(document_dedupe_key(url))
    except Exception as exc:  # noqa: BLE001 — never block a sweep on blocklist load
        logger.warning("document_scan blocked-url load failed: %s", exc)
    return known


def block_scanned_document_url(
    url: str,
    *,
    title: str = "",
    reason: str = "user_deleted",
) -> str:
    """Persist a dismissed URL so later sweeps never re-create it."""
    from apps.intel.models import BlockedScannedDocumentUrl

    normalized = normalize_document_url(url)
    if not normalized:
        return ""
    BlockedScannedDocumentUrl.objects.update_or_create(
        source_url=normalized,
        defaults={
            "title": (title or "")[:512],
            "reason": (reason or "user_deleted")[:64],
        },
    )
    return normalized


def discover_document_hits(
    keyword: str,
    *,
    filetypes: str = "pdf",
    limit: int = 15,
    engines: str | None = None,
    time_range: str | None = None,
    known_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Run Google-dork / Searx discovery and return PDF-ish links (never download bodies)."""
    queries = _document_query_variants(keyword, filetypes)
    if not queries:
        return []

    preferred = (
        time_range
        or getattr(settings, "DOCUMENT_SCAN_TIME_RANGE", "month")
        or "month"
    ).strip().lower()
    if preferred not in {"day", "week", "month", "year"}:
        preferred = "month"

    known = known_urls if known_urls is not None else set()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    target = max(1, limit)

    def _accept(url: str) -> bool:
        if _looks_like_document_url(url):
            return True
        return _looks_like_document_url(url, probe=True)

    def _consume(hits: list[dict[str, Any]], *, time_filtered: bool) -> None:
        for hit in hits or []:
            url = normalize_document_url(str(hit.get("url") or ""))
            if not url or url in seen or url in known:
                continue
            if not _accept(url):
                continue
            seen.add(url)
            out.append(
                {
                    **hit,
                    "url": url,
                    "time_filtered": time_filtered or bool(hit.get("time_filtered")),
                }
            )
            if len(out) >= target * 2:
                return

    # Pass 0: Chromium Google/Bing dork — clean phrase (no Google after:).
    # Google uses tbs=qdr:m; Bing uses optional ez month filter. ``after:`` kills Bing.
    try:
        from apps.integrations.searx.google_dork_browser import (
            google_browser_enabled,
            google_browser_on_cooldown,
            search_dork_browser,
        )

        browser_query = strip_google_after_clause(queries[0])
        if google_browser_enabled() and not google_browser_on_cooldown():
            browser_result = search_dork_browser(
                browser_query,
                limit=max(target, 10),
                time_range=preferred,
            )
            _consume(browser_result.get("hits") or [], time_filtered=True)
            if out:
                return out[: target * 2]
        elif google_browser_enabled():
            from apps.integrations.searx.google_dork_browser import (
                search_bing_dork_browser,
            )

            browser_result = search_bing_dork_browser(
                browser_query,
                limit=max(target, 10),
                time_range=preferred,
            )
            _consume(browser_result.get("hits") or [], time_filtered=True)
            if out:
                return out[: target * 2]
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan google_browser path failed: %s", exc)

    if not searx_configured():
        return out[: target * 2]

    engine_param = engines or document_scan_engines()
    engine_list = [e.strip() for e in engine_param.split(",") if e.strip()] or ["bing"]
    # Prefer Chromium for Google; skip Searx google to avoid a second captcha source.
    try:
        from apps.integrations.searx.google_dork_browser import google_browser_enabled

        if google_browser_enabled():
            engine_list = [e for e in engine_list if e.lower() != "google"]
    except Exception:  # noqa: BLE001
        pass

    healthy = [e for e in engine_list if not _engine_on_cooldown(e)]
    if not healthy:
        healthy = list(engine_list)
    preferred_order = ("bing", "duckduckgo", "brave", "yandex", "mojeek", "google")
    ranked = [e for e in preferred_order if e in healthy]
    ranked.extend([e for e in healthy if e not in ranked])
    healthy = ranked or healthy

    def _collect(query: str, tr: str, engine: str) -> None:
        if _engine_on_cooldown(engine):
            return
        eng = (engine or "").lower()
        q = query
        # Google after: is meaningless (and harmful) on Bing/Yandex/etc.
        if eng != "google":
            q = strip_google_after_clause(query)
        _pace_searx_query()
        detailed = search_searx_detailed(
            q,
            engines=engine,
            limit=max(target, 16),
            exact=False,
            time_range=tr,
            strict_engines=True,
        )
        _mark_engines_unresponsive(detailed.get("unresponsive_engines") or [])
        # Only mark time_filtered when the engine actually received a range.
        _consume(detailed.get("hits") or [], time_filtered=bool(tr))

    for engine in healthy[:3]:
        for tr in _time_ranges_for_engine(engine, preferred)[:2]:
            _collect(queries[0], tr, engine)
            if len(out) >= target:
                return out[: target * 2]
        if out:
            break

    # Try publisher-scoped / alternate variants until we have PDF hits.
    if len(out) < max(2, target // 2):
        for query in queries[1:6]:
            for engine in healthy[:2]:
                for tr in _time_ranges_for_engine(engine, preferred)[:1]:
                    _collect(query, tr, engine)
                    if len(out) >= target:
                        return out[: target * 2]
            if len(out) >= max(2, target // 2):
                break

    if (
        not out
        and bool(getattr(settings, "DOCUMENT_SCAN_SECONDARY_RANGE", True))
        and "google" in healthy
        and not _engine_on_cooldown("google")
    ):
        _collect(queries[0], "month", "google")

    # Wigolo fallback when browser/Searx still thin (before giving up).
    target_floor = max(1, min(target, int(getattr(settings, "WIGOLO_DOCUMENT_MIN_HITS", 3) or 3)))
    try:
        from apps.integrations.web_reader.wigolo import (
            discover_wigolo_document_hits,
            should_call_wigolo,
            wigolo_configured,
        )

        if should_call_wigolo(
            purpose="document",
            kept_hits=len(out),
            min_hits=target_floor,
            configured=wigolo_configured(),
        ):
            ft = str(filetypes or "pdf").split(",")[0].strip().lstrip(".") or "pdf"
            wigolo_hits = discover_wigolo_document_hits(
                keyword,
                limit=max(target, 8),
                filetype=ft,
            )
            _consume(wigolo_hits, time_filtered=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan wigolo fallback failed: %s", exc)

    return out[: target * 2]



def ingest_document_hits(
    hits: list[dict[str, Any]],
    *,
    keyword_obj: DocumentScanKeyword | None = None,
    keyword_text: str = "",
    notify: bool = True,
    known_urls: set[str] | None = None,
) -> dict[str, int]:
    """Persist important document hits; create path-focused notifications for new ones."""
    now = timezone.now()
    max_age = document_scan_max_age_days()
    fallback_age = document_scan_fallback_max_age_days()
    min_score = document_scan_min_importance()
    kw_text = (
        keyword_text
        or (keyword_obj.keyword if keyword_obj else "")
        or ""
    ).strip()
    known = known_urls if known_urls is not None else _load_known_document_urls()
    created = 0
    skipped = 0
    weak = 0
    notified = 0
    created_urls: list[str] = []
    translate_ids: list[int] = []
    created_ids: list[int] = []

    for hit in hits:
        raw_url = str(hit.get("url") or "")
        url = normalize_document_url(raw_url)
        if not url:
            skipped += 1
            continue
        dedupe = document_dedupe_key(url)
        if url in known or dedupe in known:
            skipped += 1
            continue

        title = str(hit.get("title") or url)[:512]
        snippet = str(hit.get("content") or "")[:4000]
        published, date_source = _resolve_hit_published(
            hit, title=title, snippet=snippet, url=url, now=now
        )
        age_ok, age_tier = _age_ok_for_hit(
            published,
            url=url,
            now=now,
            primary_days=max_age,
            fallback_days=fallback_age,
        )
        if not age_ok:
            skipped += 1
            logger.info(
                "document_scan skip_age keyword=%r url=%s date_source=%s published=%s",
                kw_text,
                url[:160],
                date_source,
                published,
            )
            continue

        score = _importance_score(
            title=title, snippet=snippet, url=url, keyword=kw_text
        )
        if not is_document_topic_relevant(
            title=title, snippet=snippet, url=url, keyword=kw_text
        ):
            weak += 1
            logger.info(
                "document_scan skip_topic keyword=%r url=%s score=%s",
                kw_text,
                url[:160],
                score,
            )
            continue
        if score < min_score:
            weak += 1
            logger.info(
                "document_scan skip_score keyword=%r url=%s score=%s floor=%s",
                kw_text,
                url[:160],
                score,
                min_score,
            )
            continue

        live_ok, live_reason = validate_live_pdf(url)
        if not live_ok:
            skipped += 1
            logger.info(
                "document_scan skip_dead keyword=%r url=%s reason=%s",
                kw_text,
                url[:160],
                live_reason,
            )
            continue

        engine = str(hit.get("engine") or "")[:64]
        path = _display_path(url)
        host = _host(url)
        meta = {
            "query": build_document_query(
                kw_text, keyword_obj.filetypes if keyword_obj else "pdf"
            ),
            "engine": engine,
            "score_raw": hit.get("score"),
            "published_raw": hit.get("published") or "",
            "date_source": date_source,
            "age_tier": age_tier,
            "time_filtered": bool(hit.get("time_filtered")),
            "live_pdf": live_reason,
            "dedupe_key": dedupe,
            "raw_url": raw_url[:512],
        }
        try:
            with transaction.atomic():
                doc = ScannedDocument.objects.create(
                    title=title,
                    summary=snippet[:2000],
                    source_url=url,
                    file_path=path,
                    host=host,
                    filetype="pdf",
                    keyword=keyword_obj,
                    matched_keyword=kw_text[:255],
                    source=_source_from_engine(engine),
                    engine=engine,
                    importance_score=score,
                    is_important=True,
                    published_at=published,
                    discovered_at=now,
                    metadata=meta,
                )
        except IntegrityError:
            skipped += 1
            known.add(url)
            known.add(dedupe)
            continue

        known.add(url)
        known.add(dedupe)
        created_urls.append(url)
        created_ids.append(doc.id)
        created += 1
        translate_ids.append(doc.id)
        if notify:
            note = AlertNotification.objects.create(
                title=f"Tài liệu mới: {title[:200]}",
                message=(
                    f"Từ khóa: {kw_text or '—'}\n"
                    f"Đường dẫn: {path or url}\n"
                    f"URL: {url}"
                ),
                severity=(
                    AlertNotification.Severity.HIGH
                    if score >= 70
                    else AlertNotification.Severity.MEDIUM
                ),
                document=doc,
            )
            if note.id:
                notified += 1

    if translate_ids:
        from apps.integrations.ai.translate import enqueue_document_title_translations

        enqueue_document_title_translations(translate_ids)

    return {
        "created": created,
        "skipped": skipped,
        "weak": weak,
        "notified": notified,
        "created_urls": created_urls,
        "created_ids": created_ids,
    }


def _keyword_on_cooldown(row: DocumentScanKeyword, *, now: datetime, force: bool) -> bool:
    """Legacy cooldown — rotation via is_active replaces this for normal sweeps."""
    if force:
        return False
    cooldown_min = document_scan_keyword_cooldown_minutes()
    if cooldown_min <= 0 or not row.last_scanned_at:
        return False
    return row.last_scanned_at >= now - timedelta(minutes=cooldown_min)


def deactivate_scanned_keyword(keyword_id: int) -> bool:
    """Turn off a keyword after it has been scanned in this round."""
    updated = DocumentScanKeyword.objects.filter(pk=keyword_id, is_active=True).update(
        is_active=False
    )
    return bool(updated)


def reactivate_all_document_scan_keywords() -> int:
    """Re-enable every keyword once a full round has finished."""
    return int(
        DocumentScanKeyword.objects.filter(is_active=False).update(is_active=True)
    )


def ensure_keywords_for_scan_round() -> int:
    """
    If no active keywords remain, start a new round by turning them all back on.

    Returns the number of keywords reactivated (0 if a round was already in progress).
    """
    if DocumentScanKeyword.objects.filter(is_active=True).exists():
        return 0
    reactivated = reactivate_all_document_scan_keywords()
    if reactivated:
        logger.info(
            "document_scan new round — reactivated %s keywords",
            reactivated,
        )
    return reactivated


def _scan_one_keyword(
    keyword_id: int,
    *,
    limit: int,
    known_urls: set[str],
    force: bool,
    manage_db_connections: bool = False,
) -> dict[str, Any]:
    """Worker for one keyword (safe for ThreadPoolExecutor)."""
    if manage_db_connections:
        close_old_connections()
    try:
        row = DocumentScanKeyword.objects.filter(pk=keyword_id, is_active=True).first()
        if not row:
            return {
                "id": keyword_id,
                "keyword": "",
                "skipped_reason": "missing",
                "created": 0,
                "created_ids": [],
                "created_urls": [],
            }
        now = timezone.now()
        query = row.build_query()

        hits = discover_document_hits(
            row.keyword,
            filetypes=row.filetypes,
            limit=limit,
            known_urls=known_urls,
        )
        stats = ingest_document_hits(
            hits,
            keyword_obj=row,
            keyword_text=row.keyword,
            known_urls=known_urls,
        )
        row.last_scanned_at = now
        row.last_hit_count = stats["created"]
        row.is_active = False  # Done this round — skip until the full set finishes.
        row.save(
            update_fields=[
                "last_scanned_at",
                "last_hit_count",
                "is_active",
                "updated_at",
            ]
        )
        logger.info(
            "document_scan keyword=%s query=%r hits=%s created=%s deactivated=1",
            row.keyword,
            query,
            len(hits),
            stats["created"],
        )
        return {
            "id": row.id,
            "query": query,
            "keyword": row.keyword,
            "hits": len(hits),
            "created": stats["created"],
            "skipped": stats["skipped"],
            "weak": stats["weak"],
            "notified": stats["notified"],
            "deactivated": True,
            "created_urls": stats.get("created_urls") or [],
            "created_ids": stats.get("created_ids") or [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("document_scan keyword_id=%s failed: %s", keyword_id, exc)
        # Still retire the keyword so one failure cannot block the round forever.
        try:
            deactivate_scanned_keyword(keyword_id)
        except Exception:  # noqa: BLE001
            pass
        return {
            "id": keyword_id,
            "keyword": "",
            "error": str(exc),
            "created": 0,
            "skipped": 0,
            "weak": 0,
            "notified": 0,
            "hits": 0,
            "deactivated": True,
            "created_urls": [],
            "created_ids": [],
        }
    finally:
        if manage_db_connections:
            close_old_connections()


def scan_documents_via_searx(
    *,
    limit_per_keyword: int | None = None,
    keyword_ids: list[int] | None = None,
    force: bool = False,
    publish_progress: bool = True,
) -> dict[str, Any]:
    """Sweep active keywords sequentially → ScannedDocument + live progress."""
    if not document_scan_enabled():
        return {"skipped": True, "reason": "disabled"}
    if not searx_configured():
        return {"skipped": True, "reason": "searx_not_configured"}

    limit = limit_per_keyword or document_scan_limit_per_keyword()
    # New round: if every keyword was turned off after prior scans, turn them all back on.
    if not keyword_ids:
        ensure_keywords_for_scan_round()

    qs = DocumentScanKeyword.objects.filter(is_active=True).order_by(
        F("last_scanned_at").asc(nulls_first=True),
        "-priority",
        "name",
        "id",
    )
    if keyword_ids:
        qs = qs.filter(id__in=keyword_ids)

    keywords = list(qs.values_list("id", flat=True))
    if not keyword_ids:
        keywords = keywords[: document_scan_max_keywords_per_run()]
    target_created = document_scan_target_created_per_run()
    if publish_progress:
        from apps.integrations.searx.document_scan_status import mark_scan_running

        # Flip UI out of "queued" before any Searx / DB prep work.
        mark_scan_running(total_keywords=len(keywords))

    known_urls = _load_known_document_urls()
    # Sequential during live UI sweeps: ordered progress + early-stop.
    workers = 1 if publish_progress else (
        min(document_scan_parallelism(), max(1, len(keywords))) or 1
    )
    from django.db import connection

    if connection.in_atomic_block:
        workers = 1

    totals: dict[str, Any] = {
        "keywords": len(keywords),
        "created": 0,
        "skipped": 0,
        "weak": 0,
        "notified": 0,
        "hits": 0,
        "cooldown_skipped": 0,
        "deactivated": 0,
        "reactivated": 0,
        "parallelism": workers,
        "force": force,
        "early_stop": False,
        "created_ids": [],
        "queries": [],
    }
    if not keywords:
        if publish_progress:
            from apps.integrations.searx.document_scan_status import mark_scan_finished

            mark_scan_finished(created=0)
        return totals

    results: list[dict[str, Any]] = []
    if workers == 1:
        created_so_far = 0
        for index, kid in enumerate(keywords, start=1):
            stats = _scan_one_keyword(
                kid,
                limit=limit,
                known_urls=set(known_urls),
                force=force,
                manage_db_connections=False,
            )
            results.append(stats)
            for url in stats.get("created_urls") or []:
                known_urls.add(url)
            created_so_far += int(stats.get("created") or 0)
            if publish_progress:
                from apps.integrations.searx.document_scan_status import mark_scan_progress

                mark_scan_progress(
                    done=index,
                    total=len(keywords),
                    keyword=str(stats.get("keyword") or ""),
                    query=str(stats.get("query") or ""),
                    created_delta=int(stats.get("created") or 0),
                    hits_delta=int(stats.get("hits") or 0),
                    cooldown=stats.get("skipped_reason") == "cooldown",
                    created_ids=list(stats.get("created_ids") or []),
                )
            if created_so_far >= target_created:
                totals["early_stop"] = True
                logger.info(
                    "document_scan early-stop created=%s target=%s done=%s/%s",
                    created_so_far,
                    target_created,
                    index,
                    len(keywords),
                )
                break
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _scan_one_keyword,
                    kid,
                    limit=limit,
                    known_urls=set(known_urls),
                    force=force,
                    manage_db_connections=True,
                ): kid
                for kid in keywords
            }
            done = 0
            for fut in as_completed(futures):
                stats = fut.result()
                results.append(stats)
                done += 1
                if publish_progress:
                    from apps.integrations.searx.document_scan_status import (
                        mark_scan_progress,
                    )

                    mark_scan_progress(
                        done=done,
                        total=len(keywords),
                        keyword=str(stats.get("keyword") or ""),
                        query=str(stats.get("query") or ""),
                        created_delta=int(stats.get("created") or 0),
                        hits_delta=int(stats.get("hits") or 0),
                        cooldown=stats.get("skipped_reason") == "cooldown",
                        created_ids=list(stats.get("created_ids") or []),
                    )

    for stats in results:
        totals["created"] += int(stats.get("created") or 0)
        totals["skipped"] += int(stats.get("skipped") or 0)
        totals["weak"] += int(stats.get("weak") or 0)
        totals["notified"] += int(stats.get("notified") or 0)
        totals["hits"] += int(stats.get("hits") or 0)
        if stats.get("deactivated"):
            totals["deactivated"] += 1
        if stats.get("skipped_reason") == "cooldown":
            totals["cooldown_skipped"] += 1
        for url in stats.get("created_urls") or []:
            known_urls.add(url)
        for doc_id in stats.get("created_ids") or []:
            totals["created_ids"].append(doc_id)
        totals["queries"].append(
            {
                k: v
                for k, v in stats.items()
                if k not in {"created_urls", "created_ids"}
            }
        )

    # Full round complete — no active keywords left → turn everything back on.
    if not DocumentScanKeyword.objects.filter(is_active=True).exists():
        totals["reactivated"] = reactivate_all_document_scan_keywords()
        logger.info(
            "document_scan round complete — reactivated %s keywords",
            totals["reactivated"],
        )

    if publish_progress:
        from apps.integrations.searx.document_scan_status import mark_scan_finished

        mark_scan_finished(created=int(totals["created"]))
    return totals
