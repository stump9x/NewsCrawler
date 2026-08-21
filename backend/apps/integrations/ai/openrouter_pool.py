"""OpenRouter API key pool — rotate on rate limits. Never log raw keys.

OpenAI-compatible chat completions at https://openrouter.ai/api/v1.
Shared pool for translate + briefing (simpler than Groq's split pools).
Primary model is ``openrouter/free``; on failure/429 walk a free-model list.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RR_INDEX = 0
_LOCAL_COOLDOWN_UNTIL: dict[str, float] = {}
_LOCAL_LAST_CALL_AT = 0.0
_FREE_MODELS_CACHE: list[str] = []
_FREE_MODELS_CACHE_AT = 0.0

_COOLDOWN_CACHE_PREFIX = "openrouter:key_cool:"
_RPM_CACHE_KEY = "openrouter:rpm_window"
_LAST_CALL_CACHE_KEY = "openrouter:last_call_at"
_FREE_MODELS_CACHE_KEY = "openrouter:free_models"

# Seed fallbacks when live catalog is unavailable. Prefer :free chat IDs.
_DEFAULT_FREE_FALLBACKS = (
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "google/gemma-3-27b-it:free",
    "deepseek/deepseek-r1-0528:free",
    "meta-llama/llama-3.1-405b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "nvidia/llama-3.1-nemotron-70b-instruct:free",
)

_QUALITY_HINTS = (
    "llama-3.3",
    "llama-3.1-405",
    "qwen3",
    "qwen-2.5-72",
    "gemma-3-27",
    "deepseek-r1",
    "deepseek-chat",
    "mistral-small",
    "nemotron",
)


def _ns_prefix() -> str:
    ns = (getattr(settings, "OPENROUTER_POOL_NAMESPACE", "") or "").strip()
    return f"{ns}:" if ns else ""


def _cooldown_key(fp: str) -> str:
    return f"{_ns_prefix()}{_COOLDOWN_CACHE_PREFIX}{fp}"


def _rpm_key() -> str:
    return f"{_ns_prefix()}{_RPM_CACHE_KEY}"


def _last_call_key() -> str:
    return f"{_ns_prefix()}{_LAST_CALL_CACHE_KEY}"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_openrouter_api_keys(
    primary: str = "",
    multi: str = "",
) -> list[str]:
    """Parse OPENROUTER_API_KEY + OPENROUTER_API_KEYS (comma / newline / semicolon)."""
    chunks: list[str] = []
    if primary and str(primary).strip():
        chunks.append(str(primary).strip())
    raw = str(multi or "")
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        token = part.strip()
        if token:
            chunks.append(token)
    seen: set[str] = set()
    out: list[str] = []
    for key in chunks:
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def openrouter_api_keys() -> list[str]:
    """Resolve configured keys. Never log returned values."""
    return parse_openrouter_api_keys(
        getattr(settings, "OPENROUTER_API_KEY", "") or "",
        getattr(settings, "OPENROUTER_API_KEYS", "") or "",
    )


def openrouter_keys_configured() -> bool:
    return bool(openrouter_api_keys())


def openrouter_enabled() -> bool:
    if not bool(getattr(settings, "OPENROUTER_ENABLED", False)):
        return False
    return openrouter_keys_configured()


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:  # noqa: BLE001
        return None


def mark_openrouter_key_cooldown(key: str, *, seconds: float | None = None) -> None:
    """Temporarily skip a key after 429 / quota errors (shared via Redis)."""
    ttl = seconds
    if ttl is None:
        ttl = float(getattr(settings, "OPENROUTER_KEY_COOLDOWN_SEC", 120) or 120)
    ttl = max(20.0, float(ttl))
    fp = _fingerprint(key)
    until = time.time() + ttl
    cache = _cache()
    if cache is not None:
        try:
            cache.set(_cooldown_key(fp), until, timeout=int(ttl) + 5)
        except Exception:  # noqa: BLE001
            pass
    with _LOCK:
        _LOCAL_COOLDOWN_UNTIL[fp] = until
    logger.info("openrouter key cooldown %ss (fp=%s)", int(ttl), fp)


def openrouter_keys_ready() -> bool:
    return bool(_available_keys())


def _key_cooled_until(fp: str) -> float:
    cache = _cache()
    if cache is not None:
        try:
            val = cache.get(_cooldown_key(fp))
            if val is not None:
                return float(val)
        except Exception:  # noqa: BLE001
            pass
    with _LOCK:
        return float(_LOCAL_COOLDOWN_UNTIL.get(fp, 0.0))


def _available_keys(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    ready = []
    for key in openrouter_api_keys():
        until = _key_cooled_until(_fingerprint(key))
        if until <= now:
            ready.append(key)
    return ready


def acquire_openrouter_api_key() -> str | None:
    """Round-robin among keys not in cooldown."""
    global _RR_INDEX
    ready = _available_keys()
    if not ready:
        return None
    with _LOCK:
        idx = _RR_INDEX % len(ready)
        _RR_INDEX += 1
        return ready[idx]


def _is_rate_limit_status(status_code: int, payload: Any) -> bool:
    if status_code in {429, 503}:
        return True
    text = str(payload or "").casefold()
    return any(
        token in text
        for token in (
            "rate limit",
            "rate_limit",
            "too many requests",
            "quota",
            "free-models-per-day",
            "free tier",
        )
    )


def _cooldown_seconds_from_response(response: httpx.Response) -> float | None:
    header = (response.headers.get("retry-after") or "").strip()
    if header:
        try:
            return max(20.0, float(header))
        except ValueError:
            pass
    try:
        payload = response.json() if response.content else {}
    except Exception:  # noqa: BLE001
        payload = {}
    err = payload.get("error", payload) if isinstance(payload, dict) else {}
    if isinstance(err, dict):
        msg = str(err.get("message") or "").casefold()
        if "free-models-per-day" in msg or "daily" in msg:
            return 3600.0
    return None


def wait_for_openrouter_budget(*, block: bool = True) -> bool:
    """Milder pacing than Groq; still enforces min-interval + RPM across workers."""
    global _LOCAL_LAST_CALL_AT
    min_interval = max(
        0.0, float(getattr(settings, "OPENROUTER_MIN_INTERVAL_SEC", 1.0) or 0.0)
    )
    max_rpm = max(
        1, int(getattr(settings, "OPENROUTER_MAX_REQUESTS_PER_MIN", 30) or 30)
    )
    cache = _cache()

    while True:
        if cache is not None:
            try:
                count = cache.get(_rpm_key())
                if count is None:
                    cache.set(_rpm_key(), 0, timeout=60)
                    count = 0
                if int(count) >= max_rpm:
                    if not block:
                        return False
                    time.sleep(2.0)
                    continue
            except Exception:  # noqa: BLE001
                pass

        now = time.time()
        with _LOCK:
            last = _LOCAL_LAST_CALL_AT
        if cache is not None:
            try:
                remote_last = cache.get(_last_call_key())
                if remote_last is not None:
                    last = max(last, float(remote_last))
            except Exception:  # noqa: BLE001
                pass
        wait = min_interval - (now - last) if min_interval else 0.0
        if wait > 0:
            if not block:
                return False
            time.sleep(min(wait, 5.0))
            continue
        break

    now = time.time()
    with _LOCK:
        _LOCAL_LAST_CALL_AT = now
    if cache is not None:
        try:
            cache.set(_last_call_key(), now, timeout=120)
            try:
                cache.incr(_rpm_key())
            except ValueError:
                cache.set(_rpm_key(), 1, timeout=60)
        except Exception:  # noqa: BLE001
            pass
    return True


def _pricing_is_zero(value: Any) -> bool:
    if value is None:
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        text = str(value).strip().casefold()
        return text in {"0", "0.0", "0.00"}


def is_openrouter_free_model(entry: dict[str, Any] | Any) -> bool:
    """True when model is free (zero prompt+completion pricing or ``:free`` id)."""
    if not isinstance(entry, dict):
        mid = str(entry or "").strip()
        return mid.endswith(":free") or mid == "openrouter/free"
    mid = str(entry.get("id") or "").strip()
    if mid.endswith(":free") or mid == "openrouter/free":
        return True
    pricing = entry.get("pricing") or {}
    if not isinstance(pricing, dict):
        return False
    return _pricing_is_zero(pricing.get("prompt")) and _pricing_is_zero(
        pricing.get("completion")
    )


def _model_quality_rank(model_id: str) -> int:
    mid = model_id.casefold()
    for idx, hint in enumerate(_QUALITY_HINTS):
        if hint in mid:
            return idx
    # Deprioritize tiny / vision-only / embedding-ish names.
    if any(tok in mid for tok in ("embed", "vision", "whisper", "tts", "coder")):
        return 900
    return 500


def filter_free_chat_models(
    entries: list[Any],
    *,
    limit: int = 12,
) -> list[str]:
    """Pick free chat model IDs, preferring strong Llama/Qwen/Gemma/DeepSeek."""
    ids: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        if isinstance(entry, dict):
            if not is_openrouter_free_model(entry):
                continue
            mid = str(entry.get("id") or "").strip()
            # Skip non-chat modalities when architecture hints exist.
            arch = entry.get("architecture") or {}
            modality = ""
            if isinstance(arch, dict):
                modality = str(arch.get("modality") or "").casefold()
            if modality and "text" not in modality and "chat" not in modality:
                continue
        else:
            mid = str(entry or "").strip()
            if not is_openrouter_free_model(mid):
                continue
        if not mid or mid in seen or mid == "openrouter/free":
            continue
        seen.add(mid)
        ids.append(mid)
    ids.sort(key=_model_quality_rank)
    return ids[: max(1, int(limit))]


def _configured_fallback_models() -> list[str]:
    raw = str(getattr(settings, "OPENROUTER_FALLBACK_MODELS", "") or "")
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        mid = part.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def refresh_free_models(*, force: bool = False) -> list[str]:
    """Optionally refresh free model IDs from OpenRouter catalog (cached ~1h)."""
    global _FREE_MODELS_CACHE, _FREE_MODELS_CACHE_AT
    if not bool(getattr(settings, "OPENROUTER_REFRESH_FREE_MODELS", True)):
        return list(_DEFAULT_FREE_FALLBACKS)
    now = time.time()
    if not force and _FREE_MODELS_CACHE and (now - _FREE_MODELS_CACHE_AT) < 3600:
        return list(_FREE_MODELS_CACHE)
    cache = _cache()
    if not force and cache is not None:
        try:
            cached = cache.get(f"{_ns_prefix()}{_FREE_MODELS_CACHE_KEY}")
            if isinstance(cached, list) and cached:
                _FREE_MODELS_CACHE = [str(x) for x in cached if str(x).strip()]
                _FREE_MODELS_CACHE_AT = now
                return list(_FREE_MODELS_CACHE)
        except Exception:  # noqa: BLE001
            pass

    api_key = acquire_openrouter_api_key() or (
        openrouter_api_keys()[0] if openrouter_api_keys() else ""
    )
    if not api_key:
        return list(_DEFAULT_FREE_FALLBACKS)

    headers = _request_headers(api_key)
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                "https://openrouter.ai/api/v1/models",
                headers=headers,
            )
            data = response.json() if response.content else {}
    except Exception as exc:  # noqa: BLE001
        logger.info("openrouter free-model refresh failed: %s", exc)
        return list(_FREE_MODELS_CACHE or _DEFAULT_FREE_FALLBACKS)

    if response.status_code >= 400:
        logger.info("openrouter free-model refresh HTTP %s", response.status_code)
        return list(_FREE_MODELS_CACHE or _DEFAULT_FREE_FALLBACKS)

    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return list(_FREE_MODELS_CACHE or _DEFAULT_FREE_FALLBACKS)

    models = filter_free_chat_models(entries, limit=12)
    if not models:
        models = list(_DEFAULT_FREE_FALLBACKS)
    _FREE_MODELS_CACHE = models
    _FREE_MODELS_CACHE_AT = now
    if cache is not None:
        try:
            cache.set(
                f"{_ns_prefix()}{_FREE_MODELS_CACHE_KEY}",
                models,
                timeout=3600,
            )
        except Exception:  # noqa: BLE001
            pass
    logger.info("openrouter free models refreshed count=%s", len(models))
    return list(models)


def resolve_openrouter_models(*, primary: str | None = None) -> list[str]:
    """Primary ``openrouter/free`` then configured / discovered free fallbacks."""
    primary_model = (
        primary
        or getattr(settings, "OPENROUTER_MODEL", "openrouter/free")
        or "openrouter/free"
    ).strip() or "openrouter/free"
    ordered: list[str] = [primary_model]
    for mid in _configured_fallback_models():
        if mid not in ordered:
            ordered.append(mid)
    for mid in refresh_free_models():
        if mid not in ordered:
            ordered.append(mid)
    for mid in _DEFAULT_FREE_FALLBACKS:
        if mid not in ordered:
            ordered.append(mid)
    return ordered


def _request_headers(api_key: str) -> dict[str, str]:
    referer = (
        getattr(settings, "OPENROUTER_HTTP_REFERER", "") or "https://newscrawler.local"
    ).strip()
    title = (getattr(settings, "OPENROUTER_X_TITLE", "") or "NewsCrawler").strip()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer or "https://newscrawler.local",
        "X-Title": title or "NewsCrawler",
    }


def openrouter_chat_completion(
    *,
    messages: list[dict[str, str]],
    max_tokens: int = 200,
    temperature: float = 0.1,
    model: str | None = None,
    timeout: float | None = None,
    max_attempts: int | None = None,
    block_for_budget: bool = True,
    rotate_on_rate_limit: bool = True,
    try_fallback_models: bool = True,
) -> dict[str, Any]:
    """
    Call OpenRouter chat completions with key rotation + free-model fallbacks.

    Tries ``OPENROUTER_MODEL`` (default ``openrouter/free``) first, then the
    curated/discovered free list on failure or 429.
    """
    if not openrouter_enabled():
        raise RuntimeError("OpenRouter is disabled or has no API keys")

    ready = _available_keys()
    if not ready:
        raise RuntimeError("All OpenRouter API keys cooling down (rate limited)")

    if not wait_for_openrouter_budget(block=block_for_budget):
        raise RuntimeError("OpenRouter rate budget exhausted — defer")

    timeout = float(
        timeout
        if timeout is not None
        else (getattr(settings, "OPENROUTER_TIMEOUT_SEC", 45) or 45)
    )
    attempt_cap = int(
        max_attempts
        if max_attempts is not None
        else (getattr(settings, "OPENROUTER_MAX_KEY_ATTEMPTS", 2) or 2)
    )
    attempt_cap = max(1, min(attempt_cap if attempt_cap > 0 else 1, min(len(ready), 4)))

    models = (
        resolve_openrouter_models(primary=model)
        if try_fallback_models
        else [
            (
                model
                or getattr(settings, "OPENROUTER_MODEL", "openrouter/free")
                or "openrouter/free"
            ).strip()
        ]
    )
    # Cap model walk so one call does not thrash the free catalog.
    models = models[: max(1, min(6 if try_fallback_models else 1, len(models)))]

    url = "https://openrouter.ai/api/v1/chat/completions"
    errors: list[str] = []

    for model_id in models:
        body = {
            "model": model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        attempted: set[str] = set()
        for _ in range(attempt_cap):
            api_key = acquire_openrouter_api_key()
            if not api_key or api_key in attempted:
                remaining = [k for k in _available_keys() if k not in attempted]
                if not remaining:
                    break
                api_key = remaining[0]
            attempted.add(api_key)
            fp = _fingerprint(api_key)
            headers = _request_headers(api_key)
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, headers=headers, json=body)
                    data = response.json() if response.content else {}
            except httpx.HTTPError as exc:
                errors.append(f"{model_id}/{fp}: network {exc}")
                mark_openrouter_key_cooldown(api_key, seconds=60)
                continue

            if response.status_code >= 400:
                err = data.get("error", data) if isinstance(data, dict) else data
                errors.append(f"{model_id}/{fp}: HTTP {response.status_code}")
                if _is_rate_limit_status(response.status_code, err):
                    cool = _cooldown_seconds_from_response(response)
                    mark_openrouter_key_cooldown(api_key, seconds=cool)
                    if not rotate_on_rate_limit:
                        break
                    time.sleep(0.4)
                    continue
                if response.status_code in {401, 403}:
                    mark_openrouter_key_cooldown(api_key, seconds=3600)
                    continue
                # Model unavailable / not free anymore — try next model.
                if response.status_code in {400, 404}:
                    logger.info(
                        "openrouter model miss model=%s status=%s",
                        model_id,
                        response.status_code,
                    )
                    break
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {err}")

            choices = data.get("choices") or []
            text = ""
            if choices:
                message = choices[0].get("message") or {}
                text = str(message.get("content") or "").strip()
            used = str(
                (data.get("model") if isinstance(data, dict) else None) or model_id
            )
            return {
                "text": text,
                "model": used,
                "key_fp": fp,
                "raw_id": data.get("id") if isinstance(data, dict) else None,
                "provider": "openrouter",
            }

    raise RuntimeError(
        "OpenRouter exhausted key/model attempts: " + "; ".join(errors[:8])
    )
