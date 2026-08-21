"""Small Redis-backed telemetry store for Notebook Chat.

The store deliberately keeps aggregates only: no prompts, answers, source text,
or user identifiers are persisted. Metrics are advisory and must never break a
chat request when Redis is unavailable.
"""

from __future__ import annotations

import json
import time
from typing import Any

from django.core.cache import cache

_PREFIX = "notebook:chat:metrics:v2:"
_PROVIDERS = ("shopaikey", "cerebras", "openrouter", "groq", "ollama")
_TTL = 7 * 24 * 60 * 60


def _redis():
    try:
        from apps.integrations.ai.article_body_cache import _get_redis

        return _get_redis()
    except Exception:  # noqa: BLE001
        return None


def _get(key: str) -> dict[str, Any]:
    client = _redis()
    if client is not None:
        try:
            value = client.get(key)
            parsed = json.loads(value) if value else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            pass
    try:
        value = cache.get(key)
        return dict(value) if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _set(key: str, value: dict[str, Any]) -> None:
    client = _redis()
    if client is not None:
        try:
            client.setex(key, _TTL, json.dumps(value, ensure_ascii=False))
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        cache.set(key, value, timeout=_TTL)
    except Exception:  # noqa: BLE001
        return


def _provider(value: str) -> str:
    name = str(value or "").strip().lower()
    if name in {"openai_compatible", "openai-compatible"}:
        return "cerebras"
    return name


def record_provider_attempt(
    provider: str,
    *,
    success: bool,
    latency_ms: float | None = None,
    reason: str = "",
) -> None:
    """Update bounded counters and an EWMA latency for provider routing."""
    name = _provider(provider)
    if name not in _PROVIDERS:
        return
    key = f"{_PREFIX}provider:{name}"
    try:
        data = _get(key)
        attempts = int(data.get("attempts") or 0) + 1
        successes = int(data.get("successes") or 0) + (1 if success else 0)
        failures = int(data.get("failures") or 0) + (0 if success else 1)
        latency = max(0.0, min(float(latency_ms or 0), 300_000.0))
        previous = float(data.get("latency_ewma_ms") or 0)
        ewma = previous
        if latency > 0:
            ewma = latency if previous <= 0 else (0.25 * latency + 0.75 * previous)
        _set(
            key,
            {
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "success_rate": round(successes / attempts, 4),
                "latency_ewma_ms": round(ewma, 1),
                "last_success": bool(success),
                "last_reason": str(reason or "")[:120],
                "updated_at": time.time(),
            },
        )
    except Exception:  # noqa: BLE001
        return


def record_chat_turn(
    *,
    mode: str,
    total_ms: float,
    attempts: int,
    context_ms: float = 0,
    source_count: int = 0,
    citation_status: str = "",
    citation_coverage: float = 0,
) -> None:
    """Record privacy-safe totals for operational comparison."""
    key = f"{_PREFIX}turns"
    try:
        data = _get(key)
        count = int(data.get("count") or 0) + 1

        def ewma(field: str, value: float) -> float:
            old = float(data.get(field) or 0)
            value = max(0.0, min(float(value or 0), 300_000.0))
            return value if old <= 0 else (0.2 * value + 0.8 * old)

        _set(
            key,
            {
                "count": count,
                "total_ewma_ms": round(ewma("total_ewma_ms", total_ms), 1),
                "context_ewma_ms": round(
                    ewma("context_ewma_ms", context_ms), 1
                ),
                "attempts_ewma": round(ewma("attempts_ewma", attempts), 2),
                "last_mode": str(mode or "unknown")[:32],
                "last_source_count": max(0, int(source_count or 0)),
                "last_citation_status": str(citation_status or "")[:24],
                "last_citation_coverage": max(
                    0.0, min(float(citation_coverage or 0), 1.0)
                ),
                "updated_at": time.time(),
            },
        )
    except Exception:  # noqa: BLE001
        return


def get_chat_metrics() -> dict[str, Any]:
    providers: dict[str, Any] = {}
    for name in _PROVIDERS:
        providers[name] = _get(f"{_PREFIX}provider:{name}")
    turns = _get(f"{_PREFIX}turns")
    return {
        "ok": True,
        "privacy": "aggregate_only",
        "providers": providers,
        "turns": turns if isinstance(turns, dict) else {},
    }


def get_provider_metrics(provider: str) -> dict[str, Any]:
    name = _provider(provider)
    if name not in _PROVIDERS:
        return {}
    return _get(f"{_PREFIX}provider:{name}")
