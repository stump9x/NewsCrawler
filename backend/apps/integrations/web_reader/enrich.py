"""Enrich DataLeak rows with readable page text + secret detection."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.integrations.github.detector import detect_secrets
from apps.integrations.web_reader import ReadResult, read_url, web_reader_enabled
from apps.integrations.web_reader.channels.paste import is_paste_or_raw_url, read_paste_raw
from apps.integrations.web_reader.channels.reddit import is_reddit_url, read_reddit
from apps.integrations.web_reader.channels.x_twitter import is_x_url, read_x_status
from apps.integrations.web_reader.phrase import contains_phrase


logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def enrich_enabled() -> bool:
    return bool(getattr(settings, "SEARX_LEAK_ENRICH", True)) and web_reader_enabled()


def _map_alert_severity(alerts: list) -> str | None:
    if not alerts:
        return None
    best = max((_SEVERITY_RANK.get(a.severity, 0) for a in alerts), default=0)
    if best >= 4:
        return "critical"
    if best >= 3:
        return "high"
    if best >= 2:
        return "medium"
    return "low"


def _keyword_snippets(text: str, keyword: str, *, limit: int = 12) -> list[dict[str, Any]]:
    needle = (keyword or "").casefold()
    if not text or not needle:
        return []
    out: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if needle in line.casefold():
            out.append({"line": line_no, "text": line.strip()[:500]})
            if len(out) >= limit:
                break
    return out


def _read_for_url(url: str) -> ReadResult:
    """Route enrich: paste → reddit (post+comments) → X (status+replies) → Jina/httpx."""
    if is_paste_or_raw_url(url):
        result = read_paste_raw(url)
        if result.ok:
            return result
        logger.info("paste reader miss %s: %s — falling back", url[:120], result.error)
    if is_reddit_url(url):
        result = read_reddit(url)
        if result.ok:
            return result
        logger.info("reddit reader miss %s: %s — falling back", url[:120], result.error)
    if is_x_url(url):
        result = read_x_status(url)
        if result.ok:
            return result
        logger.info("x reader miss %s: %s — falling back", url[:120], result.error)
    return read_url(url)


def enrich_leak_from_url(leak, *, keyword: str = "") -> dict[str, Any]:
    """
    Fetch leak.source_url body, run secret detector, update metadata in place.

    Safe no-op when enrich disabled or URL empty/blocked.
    """
    from apps.intel.models import DataLeak

    if not enrich_enabled():
        return {"skipped": True, "reason": "enrich_disabled"}
    url = (leak.source_url or "").strip()
    if not url:
        return {"skipped": True, "reason": "no_url"}

    result: ReadResult = _read_for_url(url)
    meta = dict(leak.metadata or {})
    meta["reader"] = {
        "ok": result.ok,
        "backend": result.backend,
        "error": result.error,
        "fetched_at": timezone.now().isoformat(),
    }
    if not result.ok:
        leak.metadata = meta
        leak.save(update_fields=["metadata", "updated_at"])
        return {
            "skipped": False,
            "ok": False,
            "backend": result.backend,
            "error": result.error,
        }

    kw = keyword or str(meta.get("keyword") or "")
    social = result.backend in {"reddit", "x_twitter"}
    phrase_ok = True
    if social and kw:
        phrase_ok = contains_phrase(result.text, kw)
    meta["phrase_match"] = phrase_ok

    # Social false-positive guard: no phrase in post/comments → do not propose as hit.
    if social and kw and not phrase_ok:
        meta["content_fetched"] = True
        meta["alert_types"] = []
        meta["evidence"] = ""
        meta["match_snippets"] = []
        meta["reader_chars"] = len(result.text)
        meta["skipped_reason"] = "phrase_not_in_post_or_comments"
        leak.metadata = meta
        leak.save(update_fields=["metadata", "updated_at"])
        return {
            "ok": True,
            "backend": result.backend,
            "relevant": False,
            "alerts": 0,
            "snippets": 0,
            "severity": leak.severity,
        }

    alerts = detect_secrets(result.text)
    snippets = _keyword_snippets(result.text, kw)
    evidence = "\n".join(dict.fromkeys(a.evidence for a in alerts))[:5000]
    alert_types = list(dict.fromkeys(a.kind for a in alerts))

    meta["content_fetched"] = True
    meta["alert_types"] = alert_types
    meta["evidence"] = evidence
    meta["match_snippets"] = snippets
    meta["reader_chars"] = len(result.text)

    update_fields = ["metadata", "updated_at"]
    if evidence:
        block = f"\n\n--- page evidence ---\n{evidence}"
        if block.strip() not in (leak.description or ""):
            leak.description = ((leak.description or "") + block)[:8000]
            update_fields.append("description")

    mapped = _map_alert_severity(alerts)
    if mapped and _SEVERITY_RANK.get(mapped, 0) > _SEVERITY_RANK.get(leak.severity, 0):
        leak.severity = mapped
        update_fields.append("severity")
    if alert_types and leak.leak_type == DataLeak.LeakType.OTHER:
        if any(
            k in {"password", "database-url", "connection-string"} for k in alert_types
        ):
            leak.leak_type = DataLeak.LeakType.CREDENTIALS
            update_fields.append("leak_type")
        elif any(
            k
            in {
                "api-key",
                "github-token",
                "aws-access-key",
                "aws-secret-key",
            }
            for k in alert_types
        ):
            leak.leak_type = DataLeak.LeakType.API_KEY
            update_fields.append("leak_type")
    if result.backend == "paste" and leak.leak_type == DataLeak.LeakType.OTHER:
        leak.leak_type = DataLeak.LeakType.PASTE
        update_fields.append("leak_type")

    leak.metadata = meta
    leak.save(update_fields=list(dict.fromkeys(update_fields)))
    return {
        "ok": True,
        "backend": result.backend,
        "alerts": len(alerts),
        "snippets": len(snippets),
        "severity": leak.severity,
    }
