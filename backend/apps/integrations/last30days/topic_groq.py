"""Groq-assisted semantic topic expansion for last30days."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def groq_topic_expand_enabled() -> bool:
    if not getattr(settings, "LAST30DAYS_GROQ_EXPAND", True):
        return False
    from apps.integrations.ai.groq_pool import groq_keys_configured

    return groq_keys_configured(pool="translate")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Strip markdown fences if the model wraps JSON.
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def groq_expand_topic(topic: str) -> dict[str, Any] | None:
    """
    Ask Groq for canonical English + multilingual aliases for a research topic.

    Returns None on any failure (caller falls back to lexicon-only expansion).
    Shape:
      {
        "canonical_english": str,
        "aliases": [str, ...],
        "must_tokens": [str, ...],  # English tokens that should appear in hits
      }
    """
    if not groq_topic_expand_enabled():
        return None
    topic = " ".join((topic or "").split()).strip()
    if len(topic) < 2:
        return None

    from apps.integrations.ai.groq_pool import groq_chat_completion

    model = (
        getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
        or "openai/gpt-oss-120b"
    )
    timeout = float(getattr(settings, "LAST30DAYS_GROQ_TIMEOUT_SEC", 20) or 20)
    prompt = (
        "You help an Indo-Pacific defence OSINT crawler understand a search topic.\n"
        "Given the user topic (any language), return ONLY a JSON object with:\n"
        '- "canonical_english": one best English search phrase (precise, not a sentence)\n'
        '- "aliases": 4-10 equivalent names/phrases in English, Vietnamese, Chinese '
        "(simplified), and common alternate geographic/military names\n"
        '- "must_tokens": 2-5 English content tokens that true hits should contain '
        '(e.g. ["south","china","sea"] for Biển Đông; avoid stopwords)\n'
        "Rules:\n"
        "- Do NOT invent unrelated topics.\n"
        "- Prefer official / widely used names (South China Sea, Second Thomas Shoal, "
        "Ayungin Shoal, Taiwan Strait, PLA Navy, etc.).\n"
        "- Never include tone-stripped Vietnamese that collides with other words "
        "(e.g. do not emit 'bien dong').\n"
        "- No markdown, no commentary — JSON only.\n\n"
        f"Topic: {topic}"
    )
    try:
        result = groq_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise multilingual defence/geography lexicographer. "
                        "Output valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.1,
            model=model,
            timeout=timeout,
            block_for_budget=True,
            pool="translate",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Groq topic expand failed: %s", exc)
        return None

    data = _parse_json_object(str(result.get("text") or ""))
    if not data:
        logger.warning("Groq topic expand: unparseable response")
        return None

    canonical = " ".join(str(data.get("canonical_english") or "").split()).strip()
    aliases_raw = data.get("aliases") if isinstance(data.get("aliases"), list) else []
    tokens_raw = (
        data.get("must_tokens") if isinstance(data.get("must_tokens"), list) else []
    )
    aliases: list[str] = []
    seen: set[str] = set()
    for item in aliases_raw:
        text = " ".join(str(item or "").split()).strip()
        if len(text) < 2:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(text[:120])
    must_tokens = [
        t.casefold()
        for t in (str(x).strip() for x in tokens_raw)
        if len(t) > 2 and t.isascii()
    ][:8]

    if not canonical and not aliases:
        return None
    return {
        "canonical_english": canonical[:160],
        "aliases": aliases[:12],
        "must_tokens": must_tokens,
    }
