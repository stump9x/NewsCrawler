"""ShopAIKey client dedicated to Notebook AI.

The gateway is OpenAI-compatible, but it is intentionally kept separate from
OpenRouter and the background briefing/translation pools.  Only Notebook
interactive paths import this module.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_LOCAL_COOLDOWN_UNTIL = 0.0
_COOLDOWN_CACHE_KEY = "notebook:shopaikey:cooldown"
_MODEL_COOLDOWN_PREFIX = "notebook:shopaikey:model_cooldown:"


def shopaikey_api_key() -> str:
    return str(getattr(settings, "NOTEBOOK_SHOPAIKEY_API_KEY", "") or "").strip()


def shopaikey_base_url() -> str:
    value = str(
        getattr(settings, "NOTEBOOK_SHOPAIKEY_BASE_URL", "")
        or "https://api.shopaikey.com/v1"
    ).strip()
    return value.rstrip("/")


def shopaikey_keys_configured() -> bool:
    return bool(shopaikey_api_key())


def shopaikey_enabled() -> bool:
    return bool(
        getattr(settings, "NOTEBOOK_SHOPAIKEY_ENABLED", True)
        and shopaikey_keys_configured()
    )


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:  # noqa: BLE001
        return None


def _cooldown_until() -> float:
    cache = _cache()
    if cache is not None:
        try:
            value = cache.get(_COOLDOWN_CACHE_KEY)
            if value is not None:
                return float(value)
        except Exception:  # noqa: BLE001
            pass
    return float(_LOCAL_COOLDOWN_UNTIL)


def mark_shopaikey_cooldown(*, seconds: float = 60.0) -> None:
    global _LOCAL_COOLDOWN_UNTIL
    ttl = max(15.0, min(float(seconds or 60.0), 3600.0))
    until = time.time() + ttl
    _LOCAL_COOLDOWN_UNTIL = until
    cache = _cache()
    if cache is not None:
        try:
            cache.set(_COOLDOWN_CACHE_KEY, until, timeout=int(ttl) + 5)
        except Exception:  # noqa: BLE001
            pass
    logger.info("ShopAIKey cooldown %ss", int(ttl))


def shopaikey_keys_ready() -> bool:
    return shopaikey_enabled() and _cooldown_until() <= time.time()


def _model_cooling(model_id: str) -> bool:
    try:
        cache = _cache()
        if cache is None:
            return False
        return float(cache.get(f"{_MODEL_COOLDOWN_PREFIX}{model_id}") or 0) > time.time()
    except Exception:  # noqa: BLE001
        return False


def _mark_model_cooldown(model_id: str, *, seconds: float = 180.0) -> None:
    ttl = max(15.0, min(float(seconds), 1800.0))
    try:
        cache = _cache()
        if cache is None:
            return
        cache.set(
            f"{_MODEL_COOLDOWN_PREFIX}{model_id}",
            time.time() + ttl,
            timeout=int(ttl) + 5,
        )
    except Exception:  # noqa: BLE001
        return


def _model(name: str, default: str) -> str:
    return str(getattr(settings, name, "") or default).strip() or default


def resolve_shopaikey_models(
    *,
    profile: str = "fast",
    primary: str | None = None,
    include_fallback: bool = True,
) -> list[str]:
    fast = _model("NOTEBOOK_SHOPAIKEY_MODEL_FAST", "qwen3-235b-a22b")
    fast_alt = _model(
        "NOTEBOOK_SHOPAIKEY_MODEL_FAST_FALLBACK", "qwen3-next-80b-a3b-instruct"
    )
    deep = _model(
        "NOTEBOOK_SHOPAIKEY_MODEL_DEEP", "qwen3-next-80b-a3b-instruct"
    )
    fallback = _model("NOTEBOOK_SHOPAIKEY_MODEL_FALLBACK", "gpt-5-mini")
    normalized = str(profile or "fast").strip().lower()
    candidates = [primary] if primary else []
    if normalized in {"deep", "analysis", "quality"}:
        candidates.extend((deep, fast, fallback))
    else:
        candidates.extend((fast, fast_alt, fallback))
    ordered: list[str] = []
    for item in candidates:
        model_id = str(item or "").strip()
        if model_id and model_id not in ordered:
            ordered.append(model_id)
    return ordered if include_fallback else ordered[:1]


def _error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:240]
        return str(error or payload)[:240]
    return str(payload or "")[:240]


def shopaikey_chat_completion(
    *,
    messages: list[dict[str, str]],
    max_tokens: int = 650,
    temperature: float = 0.15,
    model: str | None = None,
    profile: str = "fast",
    timeout: float | None = None,
    try_fallback_models: bool = True,
) -> dict[str, Any]:
    """Call ShopAIKey within one wall-clock budget.

    Model-channel failures may walk one fallback while time remains.  Auth,
    credit and rate-limit failures immediately leave the provider so the
    Notebook router can use Groq/OpenRouter without a retry storm.
    """
    if not shopaikey_enabled():
        raise RuntimeError("ShopAIKey is disabled or not configured")
    if not shopaikey_keys_ready():
        raise RuntimeError("ShopAIKey is cooling down")

    key = shopaikey_api_key()
    key_fp = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    wall_timeout = float(
        timeout
        if timeout is not None
        else getattr(settings, "NOTEBOOK_SHOPAIKEY_TIMEOUT_SECONDS", 9.0)
        or 9.0
    )
    wall_timeout = max(2.0, min(wall_timeout, 30.0))
    deadline = time.monotonic() + wall_timeout
    models = resolve_shopaikey_models(
        profile=profile,
        primary=model,
        include_fallback=try_fallback_models,
    )
    errors: list[str] = []
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    url = f"{shopaikey_base_url()}/chat/completions"

    for index, model_id in enumerate(models):
        if _model_cooling(model_id):
            errors.append(f"{model_id}:cooldown")
            continue
        remaining = deadline - time.monotonic()
        if remaining < 1.2:
            break
        body = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=max(1.0, remaining)) as client:
                response = client.post(url, headers=headers, json=body)
            data = response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{model_id}:network:{type(exc).__name__}")
            mark_shopaikey_cooldown(seconds=30)
            break

        if response.status_code >= 400:
            detail = _error_text(data)
            errors.append(f"{model_id}:HTTP{response.status_code}:{detail}")
            detail_l = detail.casefold()
            if response.status_code in {401, 402, 403}:
                mark_shopaikey_cooldown(seconds=900)
                break
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after") or "60"
                try:
                    cool = float(retry_after)
                except ValueError:
                    cool = 60.0
                mark_shopaikey_cooldown(seconds=cool)
                break
            # The catalog can list a model whose distributor channel is down.
            # Walk one configured alternative while the same wall budget remains.
            channel_miss = (
                response.status_code in {400, 404, 502, 503}
                or "no available" in detail_l
                or "无可用渠道" in detail
                or "distributor" in detail_l
            )
            if channel_miss and index + 1 < len(models):
                _mark_model_cooldown(model_id, seconds=180)
                continue
            break

        choices = data.get("choices") if isinstance(data, dict) else None
        message = choices[0].get("message") if choices else {}
        text = str((message or {}).get("content") or "").strip()
        if not text:
            errors.append(f"{model_id}:empty")
            _mark_model_cooldown(model_id, seconds=60)
            if index + 1 < len(models):
                continue
            break
        return {
            "text": text,
            "model": str(data.get("model") or model_id),
            "provider": "shopaikey",
            "key_fp": key_fp,
            "raw_id": data.get("id"),
        }

    raise RuntimeError(
        "ShopAIKey exhausted within timeout: "
        + ("; ".join(errors[:4]) if errors else "timeout")
    )
