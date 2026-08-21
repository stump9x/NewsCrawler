"""Optional title enrichment for forum claims (Groq / free HF / heuristic).

Never sends dump bodies — titles and short hints only.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}\b",
    re.I,
)


def enrich_forum_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return items
    if not bool(getattr(settings, "FORUM_AI_ENRICH", True)):
        return [_heuristic_enrich(dict(it)) for it in items]

    out: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        try:
            enrichment = classify_forum_title(str(row.get("title") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug("forum enrich fallback: %s", exc)
            enrichment = _heuristic_classify(str(row.get("title") or ""))
        if enrichment:
            row["forum_enrichment"] = enrichment
            sector = str(enrichment.get("sector") or "").strip()
            if sector and sector.lower() not in {"", "unknown", "other"}:
                # Keep summary metadata-only; stash sector in summary line if empty-ish.
                pass
        out.append(row)
    return out


def classify_forum_title(title: str) -> dict[str, Any]:
    title = (title or "").strip()[:300]
    if not title:
        return {}

    from apps.integrations.ai.groq_pool import groq_keys_configured

    if groq_keys_configured():
        try:
            from apps.integrations.ai.clients import groq_complete

            prompt = (
                "Classify this clearnet CTI headline about an alleged dark-web/forum "
                "claim for defensive monitoring. "
                "Return ONLY compact JSON with keys: "
                "is_claim (bool), victim_hint (string), sector (string), actor_hint (string). "
                "No dump data. Title: "
                f"{title}"
            )
            result = groq_complete(prompt, max_tokens=200)
            parsed = _parse_json_object(result.get("text") or "")
            if parsed:
                parsed["provider"] = result.get("provider") or "groq"
                return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Groq forum classify failed: %s", exc)

    return _heuristic_classify(title)


def _heuristic_classify(title: str) -> dict[str, Any]:
    folded = title.casefold()
    domains = _DOMAIN_RE.findall(title)
    victim = domains[0] if domains else ""
    is_claim = any(
        k in folded
        for k in (
            "leak",
            "breach",
            "database",
            "dump",
            "ulps",
            "combo",
            "access",
            "ransomware",
            "sold",
            "sale",
        )
    )
    sector = "unknown"
    if any(k in folded for k in ("bank", "finance", "payment", "card")):
        sector = "finance"
    elif any(k in folded for k in ("gov", "ministry", "municipal")):
        sector = "government"
    elif any(k in folded for k in ("hospital", "health", "clinic")):
        sector = "healthcare"
    elif any(k in folded for k in ("school", "university", "edu")):
        sector = "education"
    return {
        "is_claim": is_claim or bool(victim),
        "victim_hint": victim[:128],
        "sector": sector,
        "actor_hint": "",
        "provider": "heuristic",
    }


def _heuristic_enrich(item: dict[str, Any]) -> dict[str, Any]:
    item["forum_enrichment"] = _heuristic_classify(str(item.get("title") or ""))
    return item


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]+\}", raw, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
