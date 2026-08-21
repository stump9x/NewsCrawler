"""Paste / raw URL specialized fetch (allowlisted hosts)."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.integrations.web_reader.reader import ReadResult, _is_public_http_url

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0
_MAX = 200_000

# Hosts where raw text is preferred over HTML/Jina.
DEFAULT_PASTE_HOSTS = frozenset(
    {
        "pastebin.com",
        "www.pastebin.com",
        "gist.githubusercontent.com",
        "raw.githubusercontent.com",
        "paste.ee",
        "www.paste.ee",
        "dpaste.org",
        "dpaste.com",
        "paste.mozilla.org",
        "pastebin.pl",
        "controlc.com",
        "ideone.com",
        "hastebin.com",
        "www.hastebin.com",
        "rentry.co",
        "rentry.org",
    }
)


def paste_enrich_enabled() -> bool:
    return bool(getattr(settings, "PASTE_ENRICH_ENABLED", True))


def doctor_paste() -> dict[str, Any]:
    enabled = paste_enrich_enabled()
    return {
        "id": "paste_raw",
        "label": "Paste / raw URL fetch",
        "role": "enrich",
        "ok": enabled,
        "configured": enabled,
        "detail": f"{len(DEFAULT_PASTE_HOSTS)} allowlisted hosts",
    }


def _allowlist() -> set[str]:
    extra = getattr(settings, "PASTE_EXTRA_HOSTS", "") or ""
    hosts = set(DEFAULT_PASTE_HOSTS)
    for part in str(extra).split(","):
        h = part.strip().lower()
        if h:
            hosts.add(h)
    return hosts


def is_paste_or_raw_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    allow = _allowlist()
    if host in allow:
        return True
    return any(host == h or host.endswith(f".{h}") for h in allow)


def _to_raw_url(url: str) -> str:
    """Rewrite common paste HTML URLs to raw endpoints."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host in {"pastebin.com", "www.pastebin.com"}:
        # /abc123 or /raw/abc123
        m = re.match(r"^/(?:raw/)?([A-Za-z0-9]+)/?$", path)
        if m:
            return f"https://pastebin.com/raw/{m.group(1)}"

    if host in {"paste.ee", "www.paste.ee"}:
        m = re.match(r"^/p/([A-Za-z0-9]+)/?$", path)
        if m:
            return f"https://paste.ee/r/{m.group(1)}"

    if "gist.github.com" in host and "/raw" not in path:
        # Leave gist HTML to Jina; raw.githubusercontent / gist.githubusercontent handled as-is.
        return url

    return url


def read_paste_raw(url: str) -> ReadResult:
    if not paste_enrich_enabled():
        return ReadResult(False, "paste", "", "paste enrich disabled")
    if not is_paste_or_raw_url(url):
        return ReadResult(False, "paste", "", "host not in paste allowlist")
    target = _to_raw_url(url)
    if not _is_public_http_url(target):
        return ReadResult(False, "paste", "", "url blocked by SSRF policy")
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                target,
                headers={"User-Agent": "NewsCrawler-PasteReader/1.0", "Accept": "text/plain,*/*"},
            )
            response.raise_for_status()
            ctype = (response.headers.get("content-type") or "").lower()
            if any(x in ctype for x in ("image/", "video/", "audio/", "octet-stream")):
                return ReadResult(False, "paste", "", f"unsupported type {ctype[:40]}")
            body = response.content[: _MAX + 1]
            text = body[:_MAX].decode("utf-8", errors="replace").strip()
            if not text:
                return ReadResult(False, "paste", "", "empty body")
            return ReadResult(True, "paste", text)
    except httpx.HTTPError as exc:
        return ReadResult(False, "paste", "", str(exc)[:200])
