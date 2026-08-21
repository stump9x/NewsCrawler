"""Cerebras Cloud API key pool — rotate on rate limits. Never log raw keys.

OpenAI-compatible chat completions at https://api.cerebras.ai/v1.
Used for Notebook AI (via Open Notebook openai_compatible) and optional
briefing mid-tier between Groq and OpenRouter.
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

_COOLDOWN_CACHE_PREFIX = "cerebras:key_cool:"
_RPM_CACHE_KEY = "cerebras:rpm_window"
_LAST_CALL_CACHE_KEY = "cerebras:last_call_at"

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
# Live public catalog (Jul 2026): Llama retired; prefer long-context production.
_DEFAULT_MODELS = (
    "gpt-oss-120b",  # production, 131k ctx
    "gemma-4-31b",  # 131k ctx
    "zai-glm-4.7",  # preview, 131k ctx
)

_USER_AGENT = "NewsCrawler/1.0 (+https://newscrawler.local)"


def _ns_prefix() -> str:
    ns = (getattr(settings, "CEREBRAS_POOL_NAMESPACE", "") or "").strip()
    return f"{ns}:" if ns else ""


def _cooldown_key(fp: str) -> str:
    return f"{_ns_prefix()}{_COOLDOWN_CACHE_PREFIX}{fp}"


def _rpm_key() -> str:
    return f"{_ns_prefix()}{_RPM_CACHE_KEY}"


def _last_call_key() -> str:
    return f"{_ns_prefix()}{_LAST_CALL_CACHE_KEY}"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_cerebras_api_keys(
    primary: str = "",
    multi: str = "",
) -> list[str]:
    """Parse CEREBRAS_API_KEY + CEREBRAS_API_KEYS (comma / newline / semicolon)."""
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


def cerebras_api_keys() -> list[str]:
    """Resolve configured keys. Never log returned values."""
    return parse_cerebras_api_keys(
        getattr(settings, "CEREBRAS_API_KEY", "") or "",
        getattr(settings, "CEREBRAS_API_KEYS", "") or "",
    )


def cerebras_keys_configured() -> bool:
    return bool(cerebras_api_keys())


def cerebras_enabled() -> bool:
    if not bool(getattr(settings, "CEREBRAS_ENABLED", False)):
        return False
    return cerebras_keys_configured()


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:  # noqa: BLE001
        return None


def mark_cerebras_key_cooldown(key: str, *, seconds: float | None = None) -> None:
    """Temporarily skip a key after 429 / quota / payment errors (shared via Redis)."""
    ttl = seconds
    if ttl is None:
        ttl = float(getattr(settings, "CEREBRAS_KEY_COOLDOWN_SEC", 120) or 120)
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
    logger.info("cerebras key cooldown %ss (fp=%s)", int(ttl), fp)


def cerebras_keys_ready() -> bool:
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
    for key in cerebras_api_keys():
        until = _key_cooled_until(_fingerprint(key))
        if until <= now:
            ready.append(key)
    return ready


def acquire_cerebras_api_key() -> str | None:
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
    if status_code == 402:
        return True
    text = str(payload or "").casefold()
    return any(
        token in text
        for token in (
            "rate limit",
            "rate_limit",
            "too many requests",
            "quota",
            "payment required",
            "payment_required",
            "free tier",
            "tokens per minute",
            "requests per minute",
        )
    )


def _cooldown_seconds_from_response(response: httpx.Response) -> float | None:
    if response.status_code == 402:
        return 3600.0
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
        msg = str(err.get("message") or err.get("type") or "").casefold()
        if "payment" in msg or "quota" in msg or "daily" in msg:
            return 3600.0
    code = ""
    if isinstance(payload, dict):
        code = str(payload.get("code") or "").casefold()
        if not code and isinstance(payload.get("error"), dict):
            code = str(payload["error"].get("code") or "").casefold()
    if "payment" in code:
        return 3600.0
    return None


def wait_for_cerebras_budget(*, block: bool = True) -> bool:
    """Mild pacing: min-interval + RPM across workers."""
    global _LOCAL_LAST_CALL_AT
    min_interval = max(
        0.0, float(getattr(settings, "CEREBRAS_MIN_INTERVAL_SEC", 0.5) or 0.0)
    )
    max_rpm = max(
        1, int(getattr(settings, "CEREBRAS_MAX_REQUESTS_PER_MIN", 60) or 60)
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
                    time.sleep(1.0)
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


def _configured_fallback_models() -> list[str]:
    raw = str(getattr(settings, "CEREBRAS_FALLBACK_MODELS", "") or "")
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        mid = part.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def resolve_cerebras_models(*, primary: str | None = None) -> list[str]:
    """Primary CEREBRAS_MODEL then configured / default long-context fallbacks."""
    primary_model = (
        primary
        or getattr(settings, "CEREBRAS_MODEL", "gpt-oss-120b")
        or "gpt-oss-120b"
    ).strip() or "gpt-oss-120b"
    ordered: list[str] = [primary_model]
    for mid in _configured_fallback_models():
        if mid not in ordered:
            ordered.append(mid)
    for mid in _DEFAULT_MODELS:
        if mid not in ordered:
            ordered.append(mid)
    return ordered


def _request_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }


def cerebras_chat_completion(
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
    Call Cerebras chat completions with key rotation + model fallbacks.

    Tries ``CEREBRAS_MODEL`` (default ``gpt-oss-120b``) first, then fallbacks.
    """
    if not cerebras_enabled():
        raise RuntimeError("Cerebras is disabled or has no API keys")

    ready = _available_keys()
    if not ready:
        raise RuntimeError("All Cerebras API keys cooling down (rate limited)")

    if not wait_for_cerebras_budget(block=block_for_budget):
        raise RuntimeError("Cerebras rate budget exhausted — defer")

    timeout = float(
        timeout
        if timeout is not None
        else (getattr(settings, "CEREBRAS_TIMEOUT_SEC", 45) or 45)
    )
    attempt_cap = int(
        max_attempts
        if max_attempts is not None
        else (getattr(settings, "CEREBRAS_MAX_KEY_ATTEMPTS", 2) or 2)
    )
    attempt_cap = max(1, min(attempt_cap if attempt_cap > 0 else 1, min(len(ready), 4)))

    models = (
        resolve_cerebras_models(primary=model)
        if try_fallback_models
        else [
            (
                model
                or getattr(settings, "CEREBRAS_MODEL", "gpt-oss-120b")
                or "gpt-oss-120b"
            ).strip()
        ]
    )
    models = models[: max(1, min(4 if try_fallback_models else 1, len(models)))]

    base = (
        getattr(settings, "CEREBRAS_BASE_URL", "") or CEREBRAS_BASE_URL
    ).strip().rstrip("/")
    url = f"{base}/chat/completions"
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
            api_key = acquire_cerebras_api_key()
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
                mark_cerebras_key_cooldown(api_key, seconds=60)
                continue

            if response.status_code >= 400:
                err = data.get("error", data) if isinstance(data, dict) else data
                errors.append(f"{model_id}/{fp}: HTTP {response.status_code}")
                if _is_rate_limit_status(response.status_code, err):
                    cool = _cooldown_seconds_from_response(response)
                    mark_cerebras_key_cooldown(api_key, seconds=cool)
                    if not rotate_on_rate_limit:
                        break
                    time.sleep(0.3)
                    continue
                if response.status_code in {401, 403}:
                    mark_cerebras_key_cooldown(api_key, seconds=3600)
                    continue
                if response.status_code in {400, 404}:
                    logger.info(
                        "cerebras model miss model=%s status=%s",
                        model_id,
                        response.status_code,
                    )
                    break
                raise RuntimeError(f"Cerebras HTTP {response.status_code}: {err}")

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
                "provider": "cerebras",
            }

    raise RuntimeError(
        "Cerebras exhausted key/model attempts: " + "; ".join(errors[:8])
    )
