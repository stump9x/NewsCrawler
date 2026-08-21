"""Clearnet claim-news safety for The Wire (no forum login, no dumps).

Policy (defensive CTI):
- Collect from secondary claim/dark-web news RSS only.
- Never log into underground forums; never store cookies/sessions.
- Never persist dump bodies, attachment URLs, or credential samples.
- Reject Wire items whose primary link is a known underground forum host.
- Treat reports as alleged claims, not confirmed breaches.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Direct board RSS names — deactivated on seed; never collected.
DIRECT_FORUM_FEED_NAMES: frozenset[str] = frozenset(
    {
        "darkforums",
        "breachforums-st",
        "breached-fi",
        "xss-is",
        "exploit-in",
        "cryptbb",
        "altenens",
        "demonforums",
        "blackforums",
        "onniforums",
        "dread-onion",
    }
)

# Clearnet secondary claim / dark-web news feeds (intel_catalog + rss_sources).
CLAIM_NEWS_FEED_NAMES: frozenset[str] = frozenset(
    {
        "darkwebinformer",
        "databreaches-net",
        "undercodenews",
        "therecord-media",
        "bleepingcomputer",
        "www-hackread-com",
        "www-cyberscoop-com",
        "www-securityweek-com",
        "socradar-feed",
        "socradar-blog",
        "dehashed-blog",
        "hendry-adrian",
        "databreachtoday",
    }
)

# Host fragments that mark underground boards — never use as Wire source_url.
FORUM_HOST_MARKERS: tuple[str, ...] = (
    "darkforums",
    "breachforums",
    "breached.fi",
    "xss.is",
    "exploit.in",
    "cryptbb",
    "altenen",
    "demonforums",
    "blackforums",
    "onniforums",
    "dreadytofatroptsdj6io7l3xptbet6onoyno2yv7jicoxknyazubrad.onion",
)

# Clearnet secondary news mentioning these forums → tag as forum-claim.
SECONDARY_FORUM_ALIASES: tuple[str, ...] = (
    "breachforums",
    "breach forums",
    "darkforums",
    "dark forums",
    "leakbase",
    "leak base",
    "xss.is",
    "xss is",
    "cracked.io",
    "cracked.to",
    "xforums",
    "x forums",
    "gerkforums",
    "gerk forums",
    "china market",
    "dark army",
    "demonforums",
    "exploit.in",
    "cryptbb",
    "altenen",
    "blackforums",
    "onniforums",
    "dread forum",
    "underground forum",
    "dark web forum",
    "hacking forum",
)

_DUMP_EXT_RE = re.compile(
    r"\.(?:sql|csv|tsv|rar|zip|7z|gz|tgz|tar|bak|dump|txt|mdb|sqlite)(?:\b|$)",
    re.I,
)
_HOSTING_RE = re.compile(
    r"(?:mega\.nz|mediafire\.com|gofile\.io|anonfiles|pixeldrain|"
    r"bayfiles|racaty|upload\.ee|drive\.google\.com/file)",
    re.I,
)
_CRED_LINE_RE = re.compile(
    r"(?m)^[^\s:@/]{1,64}@[^\s:@/]{1,255}\.[a-z]{2,24}\s*[:=|;]\s*\S{3,}",
    re.I,
)
_COMBO_RE = re.compile(
    r"(?:combo\s*list|full\s*dump|database\s*dump|stealer\s*logs?\s*download|"
    r"ulps?\s*for\s*sale|private\s*logs?\s*sale|fresh\s*logs?\s*\d)",
    re.I,
)
_DOWNLOAD_CTA_RE = re.compile(
    r"(?:\[download\]|\bdownload\s+(?:link|here|dump|file|db)\b|"
    r"\bmagnet:\?|\btorrent\b)",
    re.I,
)

_SAFE_CLAIM_SUMMARY = (
    "Alleged claim reported in open-source / dark-web news (metadata only). "
    "No sample, dump, or forum session stored."
)

# Back-compat alias for older imports/tests.
FORUM_FEED_NAMES = DIRECT_FORUM_FEED_NAMES


def feed_name_is_direct_forum(feed_name: str) -> bool:
    return str(feed_name or "").strip().lower() in DIRECT_FORUM_FEED_NAMES


def feed_name_is_forum(feed_name: str) -> bool:
    """Deprecated name: True only for deactivated direct-forum feeds."""
    return feed_name_is_direct_forum(feed_name)


def feed_name_is_claim_news(feed_name: str) -> bool:
    return str(feed_name or "").strip().lower() in CLAIM_NEWS_FEED_NAMES


def url_looks_like_forum(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    if not host:
        return False
    return any(marker in host for marker in FORUM_HOST_MARKERS)


def is_claim_news_item(item: dict[str, Any]) -> bool:
    if str(item.get("discovery") or "") in {"claim-news", "forum-claim"}:
        return True
    if feed_name_is_claim_news(str(item.get("feed") or "")):
        return True
    notes = str(item.get("feed_notes") or item.get("notes") or "").casefold()
    return "claim/dark-web news" in notes


def is_forum_source_item(item: dict[str, Any]) -> bool:
    """True for forum-tagged Wire path (secondary forum mention)."""
    if str(item.get("discovery") or "") in {"forum-rss", "forum-claim"}:
        return True
    if item.get("forum_claim"):
        return True
    return False


def mentions_secondary_forum_alias(*parts: str) -> bool:
    text = " ".join(str(p or "") for p in parts).casefold()
    if not text.strip():
        return False
    return any(alias in text for alias in SECONDARY_FORUM_ALIASES)


def looks_like_sample_or_dump(
    *,
    title: str = "",
    summary: str = "",
    link: str = "",
) -> bool:
    """True when content looks like a dump/sample rather than a news headline."""
    blob = f"{title}\n{summary}\n{link}"
    link_l = str(link or "")
    parsed = urlparse(link_l)
    path = (parsed.path or "").lower()

    if _DUMP_EXT_RE.search(path) or _DUMP_EXT_RE.search(link_l):
        return True
    if _HOSTING_RE.search(link_l):
        return True
    if _CRED_LINE_RE.search(summary or ""):
        return True
    if summary and summary.count("@") >= 5 and summary.count("\n") >= 3:
        return True
    if (_COMBO_RE.search(blob) or _DOWNLOAD_CTA_RE.search(blob)) and (
        _HOSTING_RE.search(blob) or _DUMP_EXT_RE.search(blob)
    ):
        return True
    return False


def scrub_summary_to_metadata(summary: str, *, is_claim: bool = False) -> str:
    if is_claim:
        text = " ".join(str(summary or "").split())
        if not text or looks_like_sample_or_dump(summary=text):
            return _SAFE_CLAIM_SUMMARY
        return text[:480]
    text = " ".join(str(summary or "").split())
    if not text:
        return ""
    if looks_like_sample_or_dump(summary=text):
        return _SAFE_CLAIM_SUMMARY
    return text[:480]


def prepare_wire_item_for_safety(item: dict[str, Any]) -> dict[str, Any] | None:
    """
    Return a sanitized copy for Wire ingest, or None to reject.

    Rejects dump/sample payloads and primary links to underground forum hosts.
    """
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    link = str(item.get("link") or item.get("url") or "").strip()
    summary = str(item.get("summary") or item.get("description") or "")

    if url_looks_like_forum(link):
        return None
    if feed_name_is_direct_forum(str(item.get("feed") or "")):
        return None

    if looks_like_sample_or_dump(title=title, summary=summary, link=link):
        return None

    secondary = mentions_secondary_forum_alias(title, summary, link)
    discovery = str(item.get("discovery") or "")
    explicit_forum = bool(item.get("forum_claim")) or discovery in {
        "forum-claim",
        "forum-rss",
    }
    claim_feed = (
        is_claim_news_item(item) and not explicit_forum and not secondary
    )

    out = dict(item)
    out["title"] = title[:512]
    out["link"] = link[:2048]
    out["metadata_only"] = bool(secondary or explicit_forum or claim_feed)

    if secondary or explicit_forum:
        out["discovery"] = "forum-claim"
        out["forum_claim"] = True
        out["summary"] = scrub_summary_to_metadata(summary, is_claim=True)
        out["description"] = out["summary"]
        if str(out.get("category") or "").lower() in {"", "other"}:
            out["category"] = "news"
    elif claim_feed:
        out["discovery"] = str(out.get("discovery") or "claim-news")
        out["forum_claim"] = False
        out["alleged_claim"] = True
        out["summary"] = scrub_summary_to_metadata(summary, is_claim=True)
        out["description"] = out["summary"]
        if str(out.get("category") or "").lower() in {"", "other"}:
            out["category"] = "breach"
    else:
        out["summary"] = scrub_summary_to_metadata(summary)
        out["description"] = out["summary"]

    return out
