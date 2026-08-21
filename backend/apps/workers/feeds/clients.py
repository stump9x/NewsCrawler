"""HTTP clients for public (and keyed) threat intel feeds."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone as dt_timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
# v12: extract article cover images directly from RSS/Atom metadata.
RSS_PROCESSING_VERSION = 13

RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; NewsCrawlerRSS/1.0; "
        "+defense-intel; RSS aggregator)"
    ),
    "Accept": (
        "application/rss+xml, application/atom+xml, application/xml, "
        "application/json;q=0.9, text/xml;q=0.9, */*;q=0.8"
    ),
}


def absolutize_feed_link(link: str, feed_url: str = "") -> str:
    """Resolve relative RSS item links (e.g. MOD.go.jp /j/...) against the feed URL."""
    href = (link or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return href
    base = (feed_url or "").strip()
    if not base:
        return href
    absolute = urljoin(base, href)
    parsed_abs = urlparse(absolute)
    if parsed_abs.scheme in {"http", "https"} and parsed_abs.netloc:
        return absolute
    return href


class _FirstImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.src = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.src or tag.casefold() != "img":
            return
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        self.src = (
            values.get("src")
            or values.get("data-src")
            or values.get("data-lazy-src")
            or ""
        ).strip()


def _safe_image_url(candidate: str, *, article_url: str, feed_url: str) -> str:
    raw = (candidate or "").strip()
    if not raw:
        return ""
    absolute = urljoin(article_url or feed_url, raw)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    return parsed._replace(fragment="").geturl()[:2048]


def _extract_rss_image_url(
    node: Any,
    *,
    summary: str,
    article_url: str,
    feed_url: str,
) -> str:
    """Extract a cover URL from RSS metadata without fetching the article page."""
    candidates: list[str] = []
    thumbnails: list[str] = []
    html_fragments = [summary]

    for child in node.iter():
        tag = str(getattr(child, "tag", "") or "")
        local = tag.split("}", 1)[-1].casefold()
        attrs = {
            str(key).casefold(): str(value or "")
            for key, value in getattr(child, "attrib", {}).items()
        }
        candidate = (
            attrs.get("url")
            or attrs.get("href")
            or attrs.get("src")
            or ""
        )
        media_type = attrs.get("type", "").casefold()
        medium = attrs.get("medium", "").casefold()

        if local == "thumbnail" and candidate:
            thumbnails.append(candidate)
        elif local == "enclosure" and candidate:
            if media_type.startswith("image/") or not media_type:
                candidates.append(candidate)
        elif local == "content" and candidate:
            if (
                "search.yahoo.com/mrss" in tag.casefold()
                or media_type.startswith("image/")
                or medium == "image"
            ):
                candidates.append(candidate)
        elif local == "link" and candidate:
            if attrs.get("rel", "").casefold() == "enclosure" and media_type.startswith(
                "image/"
            ):
                candidates.append(candidate)

        text = str(getattr(child, "text", "") or "")
        if local in {"description", "summary", "content", "encoded"} and "<img" in text.casefold():
            html_fragments.append(text)

    candidates.extend(thumbnails)
    for fragment in html_fragments:
        if not fragment:
            continue
        parser = _FirstImageParser()
        try:
            parser.feed(fragment)
        except Exception:  # noqa: BLE001
            continue
        if parser.src:
            candidates.append(parser.src)

    for candidate in candidates:
        safe = _safe_image_url(
            candidate,
            article_url=article_url,
            feed_url=feed_url,
        )
        if safe:
            return safe
    return ""


class _StopParsing(Exception):
    """Raised to abort HTML parsing once <head> metadata is exhausted."""


class _OpenGraphImageParser(HTMLParser):
    """Extract a social-preview image from an article's <head> only.

    Reads public preview metadata (og:image, twitter:image, link rel=image_src)
    — it never scrapes article body text.
    """

    _META_KEYS = (
        "og:image:secure_url",
        "og:image:url",
        "og:image",
        "twitter:image:src",
        "twitter:image",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._found: dict[str, str] = {}
        self._link_image_src = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        values = {str(k).casefold(): str(v or "").strip() for k, v in attrs}
        if name == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            content = values.get("content") or ""
            if key in self._META_KEYS and content and key not in self._found:
                self._found[key] = content
        elif name == "link":
            rel = (values.get("rel") or "").casefold()
            if rel == "image_src" and not self._link_image_src:
                self._link_image_src = values.get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        # Metadata lives in <head>; stop as soon as the body starts.
        if tag.casefold() in {"head", "body"}:
            raise _StopParsing

    def best(self) -> str:
        for key in self._META_KEYS:
            if self._found.get(key):
                return self._found[key]
        return self._link_image_src


def fetch_og_image(article_url: str, *, max_bytes: int = 262_144) -> str:
    """Fetch an article page's social-preview image (og:image/twitter:image).

    Reads only the <head> region (capped at ``max_bytes``) of a public article
    URL to recover a cover image for feeds that omit images from their RSS.
    Returns "" on any failure. Never raises.
    """
    from apps.core.security import UnsafeURLError, validate_public_http_url

    url = (article_url or "").strip()
    if not url:
        return ""
    try:
        validate_public_http_url(url, allow_http=True)
    except UnsafeURLError:
        return ""

    headers = {
        "User-Agent": RSS_HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    }
    try:
        with httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            max_redirects=4,
            headers=headers,
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    return ""
                content_type = response.headers.get("content-type", "").casefold()
                if content_type and "html" not in content_type:
                    return ""
                # Re-validate the post-redirect host to guard against SSRF.
                try:
                    validate_public_http_url(str(response.url), allow_http=True)
                except UnsafeURLError:
                    return ""
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
        html = b"".join(chunks).decode("utf-8", "ignore")
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("og:image fetch failed for %s: %s", url, exc)
        return ""

    parser = _OpenGraphImageParser()
    try:
        parser.feed(html)
    except _StopParsing:
        pass
    except Exception:  # noqa: BLE001
        return ""

    safe = _safe_image_url(parser.best(), article_url=str(url), feed_url="")
    # Reject site-root values (e.g. "https://host/") — never a real cover image.
    if safe and urlparse(safe).path.strip("/") == "":
        return ""
    return safe


def _client(
    *,
    follow_redirects: bool = False,
    via_tor: bool = False,
    cookies: dict[str, str] | None = None,
) -> httpx.Client:
    # Default: do not follow redirects blindly (SSRF via open redirect → private IP).
    proxy = None
    if via_tor:
        if not bool(getattr(settings, "TOR_ENABLED", False)):
            raise httpx.ProxyError("Tor is disabled (TOR_ENABLED=false)")
        proxy = (getattr(settings, "TOR_SOCKS_PROXY", "") or "").strip()
        if not proxy:
            raise httpx.ProxyError("TOR_SOCKS_PROXY is empty")
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=follow_redirects,
        headers=RSS_HEADERS,
        proxy=proxy,
        cookies=cookies or None,
    )


def fetch_cve_recent(limit: int = 30) -> list[dict[str, Any]]:
    """
    Fetch recent CVEs from CIRCL (no API key required).
    https://cve.circl.lu/api/last
    """
    url = "https://cve.circl.lu/api/last"
    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        return []
    return data[:limit]


def fetch_ransomware_recent(limit: int = 30) -> list[dict[str, Any]]:
    """
    Fetch recent ransomware victims from ransomware.live with ransomlook.io fallback.
    """
    items = _fetch_ransomware_live(limit=limit)
    if items:
        return items
    return _fetch_ransomlook(limit=limit)


def _fetch_ransomware_live(limit: int) -> list[dict[str, Any]]:
    url = "https://api.ransomware.live/v2/recentvictims"
    try:
        with _client() as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("ransomware.live feed failed: %s", exc)
        return []

    if isinstance(data, list):
        return data[:limit]
    if isinstance(data, dict):
        for key in ("victims", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key][:limit]
    return []


def _fetch_ransomlook(limit: int) -> list[dict[str, Any]]:
    """Watcher-compatible secondary ransomware source."""
    url = "https://www.ransomlook.io/api/recent"
    try:
        with _client() as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("ransomlook.io feed failed: %s", exc)
        return []

    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data[:limit]:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "victim": item.get("victim") or item.get("post_title") or item.get("name"),
                    "group": item.get("group") or item.get("group_name"),
                    "discovered": item.get("discovered") or item.get("date"),
                    "website": item.get("website") or item.get("link"),
                    "post_url": item.get("post_url") or item.get("link"),
                    "source": "ransomlook.io",
                }
            )
    return rows


# Default fallback only when FeedSource table is empty.
# Keep this list defense-news RSS only — never seed cyber-breach/leak feeds.
DEFAULT_CERT_FEEDS = [
    {
        "name": "twz",
        "url": "https://www.twz.com/feed",
        "category": "news",
    },
    {
        "name": "defense-news",
        "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
        "category": "news",
    },
    {
        "name": "breaking-defense",
        "url": "https://breakingdefense.com/feed/",
        "category": "news",
    },
]


_HARD_BLOCK_STATUS_MARKERS = ("401", "403", "451")


def _is_hard_block_feed_error(error: str) -> bool:
    """Publisher / WAF hard blocks (not transient transport blips)."""
    text = (error or "").lower()
    return any(marker in text for marker in _HARD_BLOCK_STATUS_MARKERS)


def _is_dual_path_hard_block(error: str) -> bool:
    """Clearnet and Tor both returned a hard block — retrying will not help."""
    text = (error or "").lower()
    if "direct:" not in text or "tor:" not in text:
        return False
    parts = [part.strip() for part in text.split(";")]
    direct = next((part for part in parts if part.startswith("direct:")), "")
    tor = next((part for part in parts if part.startswith("tor:")), "")
    return any(m in direct for m in _HARD_BLOCK_STATUS_MARKERS) and any(
        m in tor for m in _HARD_BLOCK_STATUS_MARKERS
    )


def _is_terminal_feed_error(error: str) -> bool:
    """Errors that will not recover without a URL change — delete immediately."""
    text = (error or "").lower()
    # DNS failures and resolver outages can recover; retry them normally.
    if any(
        marker in text
        for marker in (
            "dns resolution failed",
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo failed",
        )
    ):
        return False
    if _is_dual_path_hard_block(text):
        return True
    markers = (
        "404",
        "410",
        "ssrf_blocked",
    )
    return any(m in text for m in markers)


def _looks_like_rss_or_atom(body: str) -> bool:
    head = (body or "")[:800].lstrip().lower()
    return head.startswith("<?xml") or "<rss" in head or "<feed" in head


def _looks_like_official_article_json(body: str) -> bool:
    """Detect first-party article list APIs (e.g. SecRSS) used when RSS is absent."""
    text = (body or "").lstrip()
    if not text.startswith("{"):
        return False
    try:
        import json

        payload = json.loads(text)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        return False
    first = rows[0]
    return isinstance(first, dict) and bool(first.get("title")) and (
        first.get("id") is not None or first.get("url") or first.get("link")
    )


def _strip_html_excerpt(raw: str, *, limit: int = 1200) -> str:
    """Flatten HTML/article body into a short plain-text summary."""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", str(raw or ""))
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(1, limit)]


def _parse_official_article_json(
    body: str,
    *,
    feed_url: str,
    feed_name: str,
    category: str,
    feed: dict[str, Any],
    via_tor: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Normalize SecRSS-style article JSON into Wire feed rows."""
    import json

    from apps.workers.feeds.forum_safety import prepare_wire_item_for_safety

    payload = json.loads(body)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    host = urlparse(feed_url).hostname or "www.secrss.com"
    origin = f"{urlparse(feed_url).scheme or 'https'}://{host}"
    collected: list[dict[str, Any]] = []
    for entry in rows[: max(1, limit)]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        article_id = entry.get("id")
        link = str(entry.get("url") or entry.get("link") or entry.get("source_url") or "").strip()
        if not link and article_id is not None:
            link = f"{origin}/articles/{article_id}"
        link = absolutize_feed_link(link, feed_url)
        if not link:
            continue
        summary = str(entry.get("summary") or entry.get("core_text") or "").strip()
        if not summary:
            summary = _strip_html_excerpt(
                str(entry.get("content") or entry.get("body") or ""),
                limit=1200,
            )
        elif len(summary) < 80:
            # API summaries are often one line; append a content excerpt for
            # relevance / geography detection on Chinese defense analysis.
            extra = _strip_html_excerpt(
                str(entry.get("content") or entry.get("body") or ""),
                limit=800,
            )
            if extra and extra not in summary:
                summary = f"{summary} {extra}".strip()
        published = str(
            entry.get("published_at")
            or entry.get("humansPublishedAt")
            or entry.get("updated_at")
            or ""
        ).strip()
        image_url = _safe_image_url(
            str(entry.get("image_url") or entry.get("thumb_image_url") or ""),
            article_url=link,
            feed_url=feed_url,
        )
        row = {
            "title": title[:512],
            "link": link,
            "summary": summary[:4000],
            "content": _strip_html_excerpt(
                str(entry.get("content") or ""),
                limit=2000,
            ),
            "image_url": image_url,
            "published": published,
            "feed": feed_name,
            "feed_url": feed_url,
            "category": category,
            "country": feed.get("country") or "",
            "country_code": feed.get("country_code") or "",
            "feed_confidence": feed.get("confidence"),
            "feed_notes": feed.get("notes") or "",
            "requires_tor": via_tor,
        }
        safe = prepare_wire_item_for_safety(row)
        if safe is None:
            continue
        collected.append(safe)
    return collected


def _should_retry_via_tor(exc: BaseException) -> bool:
    """Use Tor for IP/geo blocks, rate limits, and common transport failures."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ProxyError)):
        return True
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {403, 429, 451, 502, 503}:
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "403",
            "429",
            "451",
            "502",
            "503",
            "timed out",
            "timeout",
            "connect",
            "proxy",
        )
    )


def _is_onion_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host.endswith(".onion")


def fetch_feed_body_with_tor_fallback(
    url: str,
    *,
    prefer_tor: bool = False,
    etag: str = "",
    last_modified: str = "",
    cookies: dict[str, str] | None = None,
    allow_html: bool = False,
) -> tuple[str | None, dict[str, Any], bool]:
    """
    Fetch RSS/Atom with Tor optimization for blocked clearnet sources.

    - .onion → Tor only (when enabled)
    - clearnet → preferred path then fallback (clearnet ↔ Tor)
    - HTML/login walls fail that path so the alternate path can run

    Returns (body_or_none, meta, used_tor).
    """
    tor_on = bool(getattr(settings, "TOR_ENABLED", False))
    onion = _is_onion_url(url)

    if onion:
        if not tor_on:
            raise httpx.ProxyError("Onion feed requires TOR_ENABLED=true")
        body, meta = _fetch_rss_body(
            url,
            etag=etag,
            last_modified=last_modified,
            via_tor=True,
            cookies=cookies,
        )
        return body, meta, True

    if prefer_tor and tor_on:
        order = [True, False]
    else:
        order = [False]
        if tor_on:
            order.append(True)

    errors: list[str] = []
    for idx, via_tor in enumerate(order):
        has_next = idx < len(order) - 1
        try:
            body, meta = _fetch_rss_body(
                url,
                etag=etag,
                last_modified=last_modified,
                via_tor=via_tor,
                cookies=cookies,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{'tor' if via_tor else 'direct'}:{exc}")
            if not has_next:
                raise httpx.HTTPError("; ".join(errors[:4])) from exc
            if via_tor is False and not _should_retry_via_tor(exc):
                raise
            continue

        if meta.get("not_modified") or body is None:
            return body, meta, via_tor
        if _looks_like_rss_or_atom(body) or _looks_like_official_article_json(body) or allow_html:
            return body, meta, via_tor
        errors.append(f"{'tor' if via_tor else 'direct'}:non_rss_body")
        if not has_next:
            raise httpx.HTTPError("; ".join(errors[:4]))
        continue

    raise httpx.HTTPError("; ".join(errors[:4]) or "feed_fetch_failed")


def _fetch_rss_body(
    url: str,
    *,
    max_redirects: int | None = None,
    etag: str = "",
    last_modified: str = "",
    via_tor: bool = False,
    cookies: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    GET feed body with optional conditional headers.

    Returns (body_or_none, meta) where meta includes not_modified, etag,
    last_modified, body_sha256.
    """
    import hashlib
    from urllib.parse import urljoin

    from apps.core.security import validate_fetch_http_url

    if max_redirects is None:
        max_redirects = int(getattr(settings, "FEED_MAX_REDIRECTS", 5) or 5)

    current = validate_fetch_http_url(url, via_tor=via_tor, allow_http=True)
    cond: dict[str, str] = {}
    if etag:
        cond["If-None-Match"] = etag
    if last_modified:
        cond["If-Modified-Since"] = last_modified

    with _client(follow_redirects=False, via_tor=via_tor, cookies=cookies) as client:
        for _ in range(max_redirects + 1):
            response = client.get(current, headers=cond or None)
            if response.status_code == 304:
                return None, {
                    "not_modified": True,
                    "etag": response.headers.get("etag") or etag,
                    "last_modified": response.headers.get("last-modified")
                    or last_modified,
                    "body_sha256": "",
                }
            if response.is_redirect:
                loc = response.headers.get("location")
                if not loc:
                    response.raise_for_status()
                nxt = urljoin(str(response.url), loc)
                current = validate_fetch_http_url(
                    nxt, via_tor=via_tor, allow_http=True
                )
                continue
            response.raise_for_status()
            text = response.text
            digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
            return text, {
                "not_modified": False,
                "etag": response.headers.get("etag") or "",
                "last_modified": response.headers.get("last-modified") or "",
                "body_sha256": digest,
            }
    raise httpx.HTTPError(f"Exceeded {max_redirects} redirects for {url}")



class _OfficialHTMLLinkParser(HTMLParser):
    """Extract visible first-party article links from a publisher homepage."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1
        if tag.lower() == "a" and not self._href:
            self._href = attrs_dict.get("href", "").strip()
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href and self._hidden_depth == 0 and data.strip():
            self._parts.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._parts)))
            self._href = ""
            self._parts = []
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1


def _normalized_host(value: str) -> str:
    host = (urlparse(value).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_same_publisher_url(candidate: str, homepage: str) -> bool:
    candidate_host = _normalized_host(candidate)
    homepage_host = _normalized_host(homepage)
    if not candidate_host or not homepage_host:
        return False
    return (
        candidate_host == homepage_host
        or candidate_host.endswith("." + homepage_host)
        or homepage_host.endswith("." + candidate_host)
    )


def _extract_official_html_items(
    body: str, *, homepage: str, limit: int = 20
) -> list[dict[str, str]]:
    """Convert official-site article links into normalized feed-like rows."""
    parser = _OfficialHTMLLinkParser()
    parser.feed(body or "")
    blocked_text = {
        "home", "news", "about", "contact", "login", "sign in", "subscribe",
        "privacy", "terms", "more", "menu", "search",
    }
    blocked_suffixes = (
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js",
        ".ico", ".xml", ".rss", ".atom",
    )
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, raw_title in parser.links:
        title = re.sub(r"\s+", " ", raw_title or "").strip(" \t\r\n-|")
        if len(title) < 16 or title.casefold() in blocked_text:
            continue
        absolute = urljoin(homepage, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not _is_same_publisher_url(absolute, homepage):
            continue
        if len(parsed.path.strip("/")) < 4 or parsed.path.lower().endswith(blocked_suffixes):
            continue
        # Janes renders the public defence-news index as HTML rather than RSS.
        # Keep only article/detail links so case studies and navigation do not
        # consume the per-source fetch budget.
        if (
            _normalized_host(homepage) == "janes.com"
            and "/defence-news-details/" not in parsed.path.casefold()
        ):
            continue
        clean_url = parsed._replace(fragment="").geturl()
        if clean_url in seen:
            continue
        seen.add(clean_url)
        published = ""
        date_match = re.match(r"^(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+", title)
        if date_match:
            try:
                published = datetime.strptime(
                    date_match.group(1), "%d %B %Y"
                ).replace(tzinfo=dt_timezone.utc).isoformat()
            except ValueError:
                published = ""
            title = title[date_match.end() :].strip()
        title = re.sub(
            r"\s*(?:Read\s+(?:Article|Analysis|Case\s+Study))\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        output.append(
            {
                "title": title[:512],
                "link": clean_url,
                "summary": "",
                "published": published,
            }
        )
        if len(output) >= max(1, limit):
            break
    return output

def load_active_rss_feeds() -> list[dict[str, Any]]:
    """Prefer DB FeedSource rows; fall back to DEFAULT_CERT_FEEDS."""
    try:
        from apps.intel.models import FeedSource

        from django.db.models import Case, IntegerField, When

        # Breach/ransomware/CERT first so short beat windows don't starve high-signal feeds.
        category_rank = Case(
            When(category="breach", then=0),
            When(category="ransomware", then=1),
            When(category="cert", then=2),
            default=3,
            output_field=IntegerField(),
        )
        rows = list(
            FeedSource.objects.filter(is_active=True)
            .annotate(_category_rank=category_rank)
            .order_by("_category_rank", "confidence", "name")
            .values(
                "id",
                "name",
                "url",
                "category",
                "confidence",
                "country",
                "country_code",
                "http_etag",
                "http_last_modified",
                "last_body_sha256",
                "processing_version",
                "is_wordpress",
                "wordpress_site_url",
                "requires_tor",
                "notes",
            )
        )
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 — migrations / boot edge
        logger.debug("load_active_rss_feeds skipped DB: %s", exc)
    return list(DEFAULT_CERT_FEEDS)


def _mark_feed_status(
    feed: dict[str, Any],
    *,
    status: str,
    item_count: int = 0,
    error: str = "",
    etag: str = "",
    last_modified: str = "",
    body_sha256: str = "",
    processing_version: int | None = None,
    is_wordpress: bool | None = None,
    wordpress_site_url: str | None = None,
) -> None:
    feed_id = feed.get("id")
    if not feed_id:
        return
    try:
        from apps.intel.models import FeedSource

        row = FeedSource.objects.filter(pk=feed_id).first()
        if row is None:
            return

        if status in ("ok", "not_modified"):
            updates: dict[str, Any] = {
                "last_fetched_at": timezone.now(),
                # 304 and identical-body responses are successful checks.
                "last_status": "ok",
                "last_error": "",
                "consecutive_failures": 0,
                "is_active": True,
            }
            if status == "ok":
                updates["last_item_count"] = item_count
            if etag:
                updates["http_etag"] = etag[:255]
            if last_modified:
                updates["http_last_modified"] = last_modified[:128]
            if body_sha256:
                updates["last_body_sha256"] = body_sha256[:64]
            if processing_version is not None:
                updates["processing_version"] = processing_version
            if is_wordpress is not None:
                updates["is_wordpress"] = is_wordpress
            if wordpress_site_url is not None:
                updates["wordpress_site_url"] = wordpress_site_url[:2048]
            FeedSource.objects.filter(pk=feed_id).update(**updates)
            return

        # Soft skip: keep curated feeds when Tor is offline or policy-disabled.
        if status in ("tor_off", "skipped", "disabled"):
            FeedSource.objects.filter(pk=feed_id).update(
                last_fetched_at=timezone.now(),
                last_status=status[:16],
                last_error=(error or "")[:2000],
            )
            return

        failures = int(row.consecutive_failures or 0) + 1
        delete_after = max(
            1, int(getattr(settings, "FEED_DELETE_AFTER_FAILURES", 3) or 3)
        )
        terminal = _is_terminal_feed_error(error)
        hard_block = _is_hard_block_feed_error(error)
        # Soft-block SSRF noise on Tor-routed HTTPS feeds (clearnet DNS quirk).
        # Tor-successful feeds reset failures via status=ok and are kept.
        # After delete_after consecutive hard failures (clearnet+Tor), delete anyway.
        if bool(feed.get("requires_tor")) and "ssrf_blocked" in (error or "").lower():
            terminal = False
        # 401/403/451 rarely recover after both paths (or Tor-off) failed; drop
        # sooner than generic timeouts so Wire stops accumulating dead cards.
        effective_limit = (
            1 if hard_block else delete_after
        )
        if terminal or failures >= effective_limit:
            logger.info(
                "Deleting feed source pk=%s after %s failure(s) terminal=%s hard_block=%s: %s",
                feed_id,
                failures,
                terminal,
                hard_block,
                (error or "")[:120],
            )
            FeedSource.objects.filter(pk=feed_id).delete()
            return

        FeedSource.objects.filter(pk=feed_id).update(
            last_fetched_at=timezone.now(),
            last_status="error",
            last_error=(error or "")[:2000],
            last_item_count=item_count,
            consecutive_failures=failures,
            # Keep transient failures in the sweep until the configured limit.
            is_active=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("feed status update failed: %s", exc)


def fetch_cert_rss_feeds(
    feeds: list[dict[str, Any]] | None = None, limit_per_feed: int = 20
) -> list[dict[str, Any]]:
    """Parse configured RSS/Atom feeds into normalized threat items."""
    import xml.etree.ElementTree as ET

    collected: list[dict[str, Any]] = []
    feed_list = feeds if feeds is not None else load_active_rss_feeds()
    for feed in feed_list:
        name = feed.get("name") or "rss"
        url = feed.get("url") or ""
        category = feed.get("category") or "news"
        processing_version = max(
            RSS_PROCESSING_VERSION,
            int(
                getattr(settings, "RSS_PROCESSING_VERSION", RSS_PROCESSING_VERSION)
                or RSS_PROCESSING_VERSION
            ),
        )
        cache_is_current = (
            int(feed.get("processing_version") or 0) == processing_version
        )
        if not url:
            continue
        try:
            from apps.workers.feeds.forum_safety import feed_name_is_direct_forum

            if feed_name_is_direct_forum(name):
                logger.info("RSS feed %s skipped (direct forum scrape disabled)", name)
                _mark_feed_status(
                    feed,
                    status="disabled",
                    error="direct_forum_feed_disabled — use clearnet claim/status sources",
                )
                continue
        except Exception:  # noqa: BLE001
            pass
        prefer_tor = bool(feed.get("requires_tor")) and bool(
            getattr(settings, "TOR_ENABLED", False)
        )
        # Clearnet HTTPS always allowed to attempt (even if requires_tor was set historically).
        # Onion-only skip when Tor is off is handled inside fetch_feed_body_with_tor_fallback.
        if _is_onion_url(url) and not bool(getattr(settings, "TOR_ENABLED", False)):
            logger.info("RSS feed %s skipped (onion requires Tor)", name)
            _mark_feed_status(
                feed,
                status="tor_off",
                error="Onion feed requires TOR_ENABLED=true",
            )
            continue
        try:
            from apps.core.security import UnsafeURLError, validate_fetch_http_url

            validate_fetch_http_url(
                url,
                via_tor=prefer_tor or _is_onion_url(url),
                allow_http=True,
            )
        except UnsafeURLError as exc:
            # If Tor-prefer validation fails but clearnet might work, allow clearnet attempt.
            if prefer_tor and not _is_onion_url(url):
                try:
                    validate_fetch_http_url(url, via_tor=False, allow_http=True)
                except UnsafeURLError as exc2:
                    logger.warning("RSS feed %s blocked (SSRF policy): %s", name, exc2)
                    _mark_feed_status(feed, status="error", error=f"ssrf_blocked:{exc2}")
                    continue
            else:
                logger.warning("RSS feed %s blocked (SSRF policy): %s", name, exc)
                _mark_feed_status(feed, status="error", error=f"ssrf_blocked:{exc}")
                continue
        request_etag = str(feed.get("http_etag") or "") if cache_is_current else ""
        request_last_modified = (
            str(feed.get("http_last_modified") or "") if cache_is_current else ""
        )
        notes = str(feed.get("notes") or "")
        allow_html = "official-html" in notes.casefold()
        try:
            raw, meta, used_tor = fetch_feed_body_with_tor_fallback(
                url,
                prefer_tor=prefer_tor,
                etag=request_etag,
                last_modified=request_last_modified,
                allow_html=allow_html,
            )
        except httpx.HTTPError as exc:
            logger.warning("RSS feed %s failed: %s", name, exc)
            _mark_feed_status(feed, status="error", error=str(exc)[:500])
            continue
        except Exception as exc:  # noqa: BLE001
            from apps.core.security import UnsafeURLError

            if isinstance(exc, UnsafeURLError):
                logger.warning("RSS feed %s redirect blocked: %s", name, exc)
                _mark_feed_status(feed, status="error", error=f"ssrf_blocked:{exc}")
                continue
            raise

        via_tor = used_tor
        if used_tor and not bool(feed.get("requires_tor")):
            from apps.intel.models import FeedSource

            feed["requires_tor"] = True
            FeedSource.objects.filter(pk=feed.get("id")).update(requires_tor=True)
            logger.info("RSS feed %s recovered via Tor; route persisted", name)
        elif (not used_tor) and bool(feed.get("requires_tor")) and not _is_onion_url(url):
            # Clearnet works — clear sticky Tor preference for HTTPS sources.
            from apps.intel.models import FeedSource

            feed["requires_tor"] = False
            FeedSource.objects.filter(pk=feed.get("id")).update(requires_tor=False)

        if meta.get("not_modified") or raw is None:
            _mark_feed_status(
                feed,
                status="not_modified",
                etag=str(meta.get("etag") or ""),
                last_modified=str(meta.get("last_modified") or ""),
                processing_version=processing_version,
            )
            continue

        body_hash = str(meta.get("body_sha256") or "")
        prev_hash = (
            str(feed.get("last_body_sha256") or "") if cache_is_current else ""
        )
        if body_hash and prev_hash and body_hash == prev_hash:
            _mark_feed_status(
                feed,
                status="not_modified",
                etag=str(meta.get("etag") or ""),
                last_modified=str(meta.get("last_modified") or ""),
                body_sha256=body_hash,
                processing_version=processing_version,
            )
            continue

        if _looks_like_official_article_json(raw):
            try:
                # SecRSS-style APIs only return ~20 rows; pull the full page.
                json_limit = max(int(limit_per_feed or 20), 40)
                notes = str(feed.get("notes") or "")
                if "secrss.com" in url or "transport=official-json" in notes:
                    json_limit = max(json_limit, 40)
                parsed_items = _parse_official_article_json(
                    raw,
                    feed_url=url,
                    feed_name=name,
                    category=category,
                    feed=feed,
                    via_tor=via_tor,
                    limit=json_limit,
                )
            except (TypeError, ValueError) as exc:
                logger.warning("JSON feed %s parse error: %s", name, exc)
                _mark_feed_status(feed, status="error", error=f"json_parse:{exc}")
                continue
            collected.extend(parsed_items)
            _mark_feed_status(
                feed,
                status="ok",
                item_count=len(parsed_items),
                etag=str(meta.get("etag") or ""),
                last_modified=str(meta.get("last_modified") or ""),
                body_sha256=body_hash,
                processing_version=processing_version,
            )
            continue

        if allow_html:
            from apps.workers.feeds.forum_safety import prepare_wire_item_for_safety

            parsed_items = _extract_official_html_items(
                raw,
                homepage=url,
                limit=limit_per_feed,
            )
            safe_items: list[dict[str, Any]] = []
            for parsed in parsed_items:
                row = {
                    **parsed,
                    "feed": name,
                    "feed_url": url,
                    "category": category,
                    "country": feed.get("country") or "",
                    "country_code": feed.get("country_code") or "",
                    "feed_confidence": feed.get("confidence"),
                    "feed_notes": notes,
                    "requires_tor": via_tor,
                }
                safe = prepare_wire_item_for_safety(row)
                if safe is not None:
                    safe_items.append(safe)
            collected.extend(safe_items)
            _mark_feed_status(
                feed,
                status="ok",
                item_count=len(safe_items),
                etag=str(meta.get("etag") or ""),
                last_modified=str(meta.get("last_modified") or ""),
                body_sha256=body_hash,
                processing_version=processing_version,
            )
            continue

        if not _looks_like_rss_or_atom(raw):
            _mark_feed_status(
                feed,
                status="error",
                error="non_rss_body",
            )
            continue

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            logger.warning("RSS feed %s XML parse error: %s", name, exc)
            _mark_feed_status(feed, status="error", error=str(exc))
            continue

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        is_wordpress = any(
            child.tag.endswith("generator")
            and "wordpress" in (child.text or "").casefold()
            for child in root.iter()
        )
        wordpress_site_url = ""
        if is_wordpress:
            site_link = (root.findtext(".//channel/link") or "").strip()
            parsed_site = urlparse(site_link)
            if parsed_site.scheme in {"http", "https"} and parsed_site.hostname:
                wordpress_site_url = site_link
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//atom:entry", ns)

        feed_count = 0
        for node in items[:limit_per_feed]:
            def _text(paths: list[str]) -> str:
                for p in paths:
                    el = node.find(p)
                    if el is None:
                        el = node.find(p, ns)
                    if el is not None and (el.text or "").strip():
                        return (el.text or "").strip()
                    if el is not None and el.get("href"):
                        return el.get("href", "")
                return ""

            def _text_ci(names: list[str]) -> str:
                """Case-insensitive local-name lookup (China Daily uses <pubdate>)."""
                wanted = {name.casefold() for name in names}
                for child in list(node):
                    tag = str(child.tag or "")
                    local = tag.split("}", 1)[-1].casefold()
                    if local in wanted and (child.text or "").strip():
                        return (child.text or "").strip()
                    if local in wanted and child.get("href"):
                        return child.get("href", "")
                return ""

            title = _text(["title", "atom:title"]) or _text_ci(["title"])
            link = _text(["link", "atom:link"]) or _text_ci(["link"])
            if not link:
                link_el = node.find("link") or node.find("atom:link", ns)
                if link_el is not None:
                    link = link_el.get("href") or (link_el.text or "")
            link = absolutize_feed_link(link, url)
            summary = _text(
                ["description", "summary", "atom:summary", "content", "atom:content"]
            ) or _text_ci(["description", "summary", "content"])
            published = _text(
                ["pubDate", "published", "atom:published", "updated", "atom:updated"]
            ) or _text_ci(
                ["pubdate", "pubDate", "published", "updated", "date", "dc:date"]
            )
            image_url = _extract_rss_image_url(
                node,
                summary=summary,
                article_url=link,
                feed_url=url,
            )
            if title:
                from apps.workers.feeds.forum_safety import prepare_wire_item_for_safety

                row = {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "image_url": image_url,
                    "published": published,
                    "feed": name,
                    "feed_url": url,
                    "category": category,
                    "country": feed.get("country") or "",
                    "country_code": feed.get("country_code") or "",
                    "feed_confidence": feed.get("confidence"),
                    "feed_notes": feed.get("notes") or "",
                    "requires_tor": via_tor,
                }
                safe = prepare_wire_item_for_safety(row)
                if safe is None:
                    continue
                collected.append(safe)
                feed_count += 1
        _mark_feed_status(
            feed,
            status="ok",
            item_count=feed_count,
            etag=str(meta.get("etag") or ""),
            last_modified=str(meta.get("last_modified") or ""),
            body_sha256=body_hash,
            processing_version=processing_version,
            is_wordpress=is_wordpress,
            wordpress_site_url=wordpress_site_url,
        )
    return collected


def fetch_hudson_rock_search(domain: str) -> dict[str, Any]:
    """Optional keyed lookup — returns empty dict when key missing."""
    api_key = getattr(settings, "HUDSON_ROCK_API_KEY", "") or ""
    if not api_key or not domain:
        return {}
    # Placeholder URL shape; real integration refined in Phase 6.
    url = f"https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-domain"
    try:
        with _client() as client:
            response = client.get(
                url,
                params={"domain": domain},
                headers={"api-key": api_key},
            )
            if response.status_code >= 400:
                logger.warning("Hudson Rock HTTP %s", response.status_code)
                return {}
            return response.json()
    except httpx.HTTPError as exc:
        logger.warning("Hudson Rock feed failed: %s", exc)
        return {}
