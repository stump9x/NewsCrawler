"""Optimistic multi-provider router for Notebook AI chat + transformations.

Probes readiness (no heavy completions) in preference order:
  ShopAIKey → Groq (notebook pool) → OpenRouter → Cerebras → Ollama

Short TTL cache avoids stampeding proxy/pools. Mid-request 402/429/5xx can
mark a provider unhealthy so the SPA silently rolls to the next tier.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

PROVIDER_ORDER = ("shopaikey", "groq", "openrouter", "cerebras", "ollama")
# Transform keeps the paid Notebook-only gateway first for predictable quality.
TRANSFORM_PROVIDER_ORDER = (
    "shopaikey",
    "groq",
    "openrouter",
    "cerebras",
    "ollama",
)
_CACHE_KEY = "notebook:model_router:health:v2"
_UNHEALTHY_PREFIX = "notebook:model_router:unhealthy:"
_LAST_OK_PREFIX = "notebook:model_router:last_ok:"


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:  # noqa: BLE001
        return None


def _shared_get(key: str) -> Any:
    try:
        from apps.integrations.ai.article_body_cache import _get_redis

        client = _get_redis()
        if client is not None:
            raw = client.get(key)
            return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        pass
    cache = _cache()
    if cache is None:
        return None
    try:
        return cache.get(key)
    except Exception:  # noqa: BLE001
        return None


def _shared_set(key: str, value: Any, *, timeout: int) -> None:
    try:
        from apps.integrations.ai.article_body_cache import _get_redis

        client = _get_redis()
        if client is not None:
            client.setex(key, max(1, int(timeout)), json.dumps(value))
            return
    except Exception:  # noqa: BLE001
        pass
    cache = _cache()
    if cache is not None:
        try:
            cache.set(key, value, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass


def _shared_delete(key: str) -> None:
    try:
        from apps.integrations.ai.article_body_cache import _get_redis

        client = _get_redis()
        if client is not None:
            client.delete(key)
    except Exception:  # noqa: BLE001
        pass
    cache = _cache()
    if cache is not None:
        try:
            cache.delete(key)
        except Exception:  # noqa: BLE001
            pass


def health_cache_ttl() -> int:
    return max(
        15,
        int(getattr(settings, "NOTEBOOK_MODEL_HEALTH_CACHE_TTL", 45) or 45),
    )


def default_unhealthy_ttl() -> int:
    return max(
        20,
        int(getattr(settings, "NOTEBOOK_MODEL_UNHEALTHY_TTL", 60) or 60),
    )


def _norm_provider(name: str) -> str:
    p = str(name or "").strip().lower()
    if p in {"shopaikey", "shop-ai-key"}:
        return "shopaikey"
    return p


def mark_provider_unhealthy(
    provider: str,
    *,
    seconds: float | None = None,
    reason: str = "",
    latency_ms: float | None = None,
) -> dict[str, Any]:
    """Cooldown a provider after 402/429/timeout/empty so SPA skips it briefly."""
    p = _norm_provider(provider)
    if p not in PROVIDER_ORDER:
        return {"ok": False, "error": "unknown_provider", "provider": p}
    ttl = float(seconds) if seconds is not None else float(default_unhealthy_ttl())
    # 402 payment/quota — longer park; still not forever.
    reason_l = str(reason or "").casefold()
    if seconds is None and (
        "402" in reason_l or "payment" in reason_l or "quota" in reason_l
    ):
        ttl = max(ttl, 900.0)
    ttl = max(15.0, min(ttl, 3600.0))
    until = time.time() + ttl
    _shared_set(
        f"{_UNHEALTHY_PREFIX}{p}",
        {"until": until, "reason": str(reason or "")[:160]},
        timeout=int(ttl) + 5,
    )
    # Bust aggregate health cache so every worker sees the mark immediately.
    _shared_delete(_CACHE_KEY)
    logger.info(
        "notebook provider unhealthy %s ttl=%ss reason=%s",
        p,
        int(ttl),
        (reason or "")[:80],
    )
    try:
        from apps.integrations.ai.notebook_chat_metrics import (
            record_provider_attempt,
        )

        record_provider_attempt(
            p,
            success=False,
            latency_ms=latency_ms,
            reason=reason,
        )
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "provider": p, "ttl_sec": int(ttl), "until": until}


def mark_provider_success(
    provider: str,
    *,
    latency_ms: float | None = None,
) -> None:
    """Record last success so idle/ready providers sort ahead of cold ones."""
    p = _norm_provider(provider)
    if p not in PROVIDER_ORDER:
        return
    _shared_set(f"{_LAST_OK_PREFIX}{p}", time.time(), timeout=86400)
    try:
        from apps.integrations.ai.notebook_chat_metrics import (
            record_provider_attempt,
        )

        record_provider_attempt(p, success=True, latency_ms=latency_ms)
    except Exception:  # noqa: BLE001
        pass


def _provider_performance(provider: str) -> dict[str, Any]:
    try:
        from apps.integrations.ai.notebook_chat_metrics import (
            get_provider_metrics,
        )

        return get_provider_metrics(provider)
    except Exception:  # noqa: BLE001
        return {}


def _provider_marked_unhealthy(provider: str) -> dict[str, Any] | None:
    raw = _shared_get(f"{_UNHEALTHY_PREFIX}{provider}")
    if not isinstance(raw, dict):
        return None
    until = float(raw.get("until") or 0)
    if until <= time.time():
        return None
    return {
        "ready": False,
        "reason": "cooldown",
        "detail": str(raw.get("reason") or "")[:120],
        "until": until,
        "remaining_sec": max(0, int(until - time.time())),
    }


def _probe_cerebras() -> dict[str, Any]:
    """Prefer cerebras-proxy /health (Notebook path); else Django key pool."""
    proxy_url = (
        getattr(settings, "NOTEBOOK_CEREBRAS_PROXY_HEALTH_URL", "")
        or "http://cerebras-proxy:8088/health"
    ).strip()
    try:
        with httpx.Client(timeout=2.5) as client:
            resp = client.get(proxy_url)
            if resp.status_code < 500:
                data = resp.json() if resp.content else {}
                ready_n = int(data.get("ready") or 0)
                keys_n = int(data.get("keys") or 0)
                if ready_n > 0:
                    return {
                        "ready": True,
                        "reason": "proxy_ready",
                        "ready_keys": ready_n,
                        "keys": keys_n,
                    }
                if keys_n > 0:
                    return {
                        "ready": False,
                        "reason": "proxy_all_cooling",
                        "ready_keys": 0,
                        "keys": keys_n,
                    }
                return {
                    "ready": False,
                    "reason": "proxy_no_keys",
                    "ready_keys": 0,
                    "keys": 0,
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("cerebras proxy health probe failed: %s", exc)

    from apps.integrations.ai.cerebras_pool import (
        cerebras_enabled,
        cerebras_keys_configured,
        cerebras_keys_ready,
    )

    if not cerebras_keys_configured():
        return {"ready": False, "reason": "not_configured"}
    if not cerebras_enabled():
        # Keys exist but flag off — still usable for Notebook via proxy; treat
        # configured keys as soft-ready when pool has idle keys.
        if cerebras_keys_ready():
            return {"ready": True, "reason": "pool_ready_flag_off"}
        return {"ready": False, "reason": "disabled_and_cooling"}
    if cerebras_keys_ready():
        return {"ready": True, "reason": "pool_ready"}
    return {"ready": False, "reason": "pool_cooling"}


def _probe_shopaikey() -> dict[str, Any]:
    from apps.integrations.ai.shopaikey_pool import (
        shopaikey_enabled,
        shopaikey_keys_configured,
        shopaikey_keys_ready,
    )

    if not shopaikey_keys_configured():
        return {"ready": False, "reason": "not_configured"}
    if not shopaikey_enabled():
        return {"ready": False, "reason": "disabled"}
    if shopaikey_keys_ready():
        return {"ready": True, "reason": "configured"}
    return {"ready": False, "reason": "cooldown"}


def _probe_openrouter() -> dict[str, Any]:
    from apps.integrations.ai.openrouter_pool import (
        openrouter_enabled,
        openrouter_keys_configured,
        openrouter_keys_ready,
    )

    if not openrouter_keys_configured():
        return {"ready": False, "reason": "not_configured"}
    if not openrouter_enabled():
        # Notebook still registers OR models when keys exist; allow if idle.
        if openrouter_keys_ready():
            return {"ready": True, "reason": "keys_ready_flag_off"}
        return {"ready": False, "reason": "disabled"}
    if openrouter_keys_ready():
        return {"ready": True, "reason": "pool_ready"}
    return {"ready": False, "reason": "pool_cooling"}


def _probe_groq() -> dict[str, Any]:
    from apps.integrations.ai.groq_pool import (
        groq_budget_peek,
        groq_keys_configured,
        groq_keys_ready,
    )

    if not groq_keys_configured(pool="notebook"):
        return {"ready": False, "reason": "not_configured"}
    if not groq_keys_ready(pool="notebook"):
        return {"ready": False, "reason": "pool_cooling"}
    if not groq_budget_peek(pool="notebook"):
        return {"ready": False, "reason": "rpm_busy"}
    return {"ready": True, "reason": "pool_ready"}


def _probe_ollama() -> dict[str, Any]:
    base = (
        getattr(settings, "OLLAMA_BASE_URL", "") or "http://ollama:11434"
    ).rstrip("/")
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{base}/api/tags")
            if resp.status_code >= 500:
                return {"ready": False, "reason": f"http_{resp.status_code}"}
            data = resp.json() if resp.content else {}
            models = data.get("models") if isinstance(data, dict) else None
            count = len(models) if isinstance(models, list) else 0
            return {
                "ready": True,
                "reason": "tags_ok",
                "models": count,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "reason": f"unreachable:{type(exc).__name__}"}


_PROBES = {
    "shopaikey": _probe_shopaikey,
    "cerebras": _probe_cerebras,
    "openrouter": _probe_openrouter,
    "groq": _probe_groq,
    "ollama": _probe_ollama,
}


def probe_providers(*, use_cache: bool = True) -> dict[str, Any]:
    """Return readiness map for all Notebook chat/transform providers."""
    ttl = health_cache_ttl()
    if use_cache:
        hit = _shared_get(_CACHE_KEY)
        if isinstance(hit, dict) and hit.get("providers"):
            return {**hit, "cached": True}

    providers: dict[str, Any] = {}
    for name in PROVIDER_ORDER:
        marked = _provider_marked_unhealthy(name)
        if marked is not None:
            providers[name] = marked
            continue
        try:
            # Resolve probe by name each call so unittest.mock patches on
            # ``_probe_*`` apply (a static ``_PROBES`` dict would keep originals).
            probe_fn = {
                "shopaikey": _probe_shopaikey,
                "cerebras": _probe_cerebras,
                "openrouter": _probe_openrouter,
                "groq": _probe_groq,
                "ollama": _probe_ollama,
            }.get(name)
            if probe_fn is None:
                providers[name] = {"ready": False, "reason": "unknown_provider"}
            else:
                providers[name] = probe_fn()
        except Exception as exc:  # noqa: BLE001
            providers[name] = {
                "ready": False,
                "reason": f"probe_error:{type(exc).__name__}",
            }

    # Prefer recently successful *cloud* providers when both ready (idle bias).
    # Ollama must never sort ahead of a ready cloud provider — chat fast-path
    # was parking on local 1.5b after a prior ollama success bumped last_ok.
    last_ok: dict[str, float] = {}
    for name in PROVIDER_ORDER:
        if name == "ollama":
            continue
        try:
            val = _shared_get(f"{_LAST_OK_PREFIX}{name}")
            if val is not None:
                last_ok[name] = float(val)
        except Exception:  # noqa: BLE001
            pass

    ready = [p for p in PROVIDER_ORDER if providers.get(p, {}).get("ready")]
    ready_cloud = [p for p in ready if p != "ollama"]
    ready_local = [p for p in ready if p == "ollama"]
    def route_score(provider: str) -> tuple[float, float, int]:
        perf = _provider_performance(provider)
        attempts = int(perf.get("attempts") or 0)
        success_rate = float(perf.get("success_rate") or 0)
        latency_ms = float(perf.get("latency_ewma_ms") or 0)
        # Require a small sample before telemetry can override stable defaults.
        if attempts < 3:
            success_rate = 0.5
            latency_ms = 30_000.0
        return (
            -success_rate,
            latency_ms,
            PROVIDER_ORDER.index(provider),
        )

    ready_ordered = sorted(ready_cloud, key=route_score) + ready_local
    # Keep preference order for not-ready (SPA may still try as last resort).
    not_ready = [p for p in PROVIDER_ORDER if p not in ready_ordered]
    order = ready_ordered + not_ready

    payload = {
        "ok": True,
        "order": order,
        "preference": list(PROVIDER_ORDER),
        "providers": providers,
        "ttl_sec": ttl,
        "cached": False,
        "ts": time.time(),
    }
    _shared_set(_CACHE_KEY, payload, timeout=ttl)
    return payload


def list_healthy_chat_models(*, purpose: str = "chat") -> dict[str, Any]:
    """
    Public router entry: healthy providers ordered for chat or transform.

    Probes are shared; try-order preference differs: chat keeps Cerebras-first,
    Both paths prefer ShopAIKey, then free cloud fallbacks (skip cooling marks).
    """
    p = str(purpose or "chat").strip().lower()
    if p not in {"chat", "transform", "transformation"}:
        p = "chat"
    if p == "transformation":
        p = "transform"
    preference = (
        TRANSFORM_PROVIDER_ORDER if p == "transform" else PROVIDER_ORDER
    )
    probed = probe_providers(use_cache=True)
    # Re-sort ready cloud by purpose preference (probe cache may be chat-biased).
    providers_map = probed.get("providers") or {}
    ready = [name for name in preference if providers_map.get(name, {}).get("ready")]
    ready_cloud = [n for n in ready if n != "ollama"]
    ready_local = [n for n in ready if n == "ollama"]
    # Also include ready providers missing from preference (shouldn't happen).
    for name in PROVIDER_ORDER:
        if providers_map.get(name, {}).get("ready") and name not in ready_cloud + ready_local:
            if name == "ollama":
                ready_local.append(name)
            else:
                ready_cloud.append(name)
    healthy_cloud = ready_cloud
    healthy_local = ready_local
    cloud_pref = [n for n in preference if n != "ollama"]
    try_order = (
        healthy_cloud
        + [n for n in cloud_pref if n not in healthy_cloud]
        + healthy_local
        + (["ollama"] if "ollama" not in healthy_local else [])
    )
    # De-dupe while preserving order.
    seen: set[str] = set()
    try_order_unique: list[str] = []
    for n in try_order:
        if n in seen:
            continue
        seen.add(n)
        try_order_unique.append(n)
    return {
        **probed,
        "purpose": p,
        "preference": list(preference),
        "healthy": healthy_cloud + healthy_local,
        # SPA should try healthy cloud first; Ollama always last-resort.
        "try_order": try_order_unique,
    }
