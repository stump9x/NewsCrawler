"""Groq API key pool — rotate on rate limits. Never log raw keys.

Cooldowns and RPM caps are Redis-backed so Celery workers / backend / ingest
share the same budget and do not stampede keys after a 429.

Task pools (translate / briefing / notebook / default) keep separate local RPM
budgets and round-robin indexes so high-volume title translate does not share
quota with TPM-heavy briefing or Notebook.
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
# Per-pool round-robin indexes (process-local).
_RR_INDEX: dict[str, int] = {}
# Process-local fallback when cache is unavailable.
_LOCAL_COOLDOWN_UNTIL: dict[str, float] = {}
_LOCAL_LAST_CALL_AT: dict[str, float] = {}

_COOLDOWN_CACHE_PREFIX = "groq:key_cool:"
_RPM_CACHE_KEY = "groq:rpm_window"
_LAST_CALL_CACHE_KEY = "groq:last_call_at"

_VALID_POOLS = frozenset({"default", "translate", "briefing", "notebook"})


def _norm_pool(pool: str | None) -> str:
    name = (pool or "default").strip().lower() or "default"
    if name not in _VALID_POOLS:
        return "default"
    return name


def _ns_prefix() -> str:
    ns = (getattr(settings, "GROQ_POOL_NAMESPACE", "") or "").strip()
    return f"{ns}:" if ns else ""


def _cooldown_key(fp: str) -> str:
    return f"{_ns_prefix()}{_COOLDOWN_CACHE_PREFIX}{fp}"


def _rpm_key(pool: str = "default") -> str:
    p = _norm_pool(pool)
    base = f"{_ns_prefix()}{_RPM_CACHE_KEY}"
    return base if p == "default" else f"{base}:{p}"


def _last_call_key(pool: str = "default") -> str:
    p = _norm_pool(pool)
    base = f"{_ns_prefix()}{_LAST_CALL_CACHE_KEY}"
    return base if p == "default" else f"{base}:{p}"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_groq_api_keys(
    primary: str = "",
    multi: str = "",
) -> list[str]:
    """Parse GROQ_API_KEY + GROQ_API_KEYS (comma / newline / semicolon)."""
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


def groq_api_keys(pool: str = "default") -> list[str]:
    """Resolve keys for a task pool. Never log returned values."""
    p = _norm_pool(pool)
    fallback = parse_groq_api_keys(
        getattr(settings, "GROQ_API_KEY", "") or "",
        getattr(settings, "GROQ_API_KEYS", "") or "",
    )
    if p == "translate":
        multi = getattr(settings, "GROQ_API_KEYS_TRANSLATE", "") or ""
        if str(multi).strip():
            return parse_groq_api_keys("", multi)
        return fallback
    if p == "briefing":
        multi = getattr(settings, "GROQ_API_KEYS_BRIEFING", "") or ""
        if str(multi).strip():
            return parse_groq_api_keys("", multi)
        return fallback
    if p == "notebook":
        keys = parse_groq_api_keys(
            getattr(settings, "NOTEBOOK_GROQ_API_KEY", "") or "",
            getattr(settings, "NOTEBOOK_GROQ_API_KEYS", "") or "",
        )
        if bool(getattr(settings, "NOTEBOOK_GROQ_STRICT_POOL", True)):
            return keys
        return keys or fallback
    return fallback


def groq_keys_configured(pool: str = "default") -> bool:
    return bool(groq_api_keys(pool=pool))


def _cache():
    try:
        from django.core.cache import cache

        return cache
    except Exception:  # noqa: BLE001
        return None


def mark_groq_key_cooldown(key: str, *, seconds: float | None = None) -> None:
    """Temporarily skip a key after 429 / quota errors (shared via Redis)."""
    ttl = seconds
    if ttl is None:
        ttl = float(getattr(settings, "GROQ_KEY_COOLDOWN_SEC", 480) or 480)
    ttl = max(30.0, float(ttl))
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
    logger.info("groq key cooldown %ss (fp=%s)", int(ttl), fp)


def groq_keys_ready(pool: str = "default") -> bool:
    """True when at least one key is not in cooldown."""
    return bool(_available_keys(pool=pool))


def groq_budget_peek(pool: str = "default") -> bool:
    """
    True if a Groq call would be allowed *without* waiting or reserving a slot.

    Used by ingest to skip inline Groq when the shared RPM budget is already full.
    """
    p = _norm_pool(pool)
    min_interval = max(
        0.0, float(getattr(settings, "GROQ_MIN_INTERVAL_SEC", 3.5) or 0.0)
    )
    max_rpm = max(1, int(getattr(settings, "GROQ_MAX_REQUESTS_PER_MIN", 12) or 12))
    if not _available_keys(pool=p):
        return False
    cache = _cache()
    if cache is not None:
        try:
            count = cache.get(_rpm_key(p))
            if count is not None and int(count) >= max_rpm:
                return False
            remote_last = cache.get(_last_call_key(p))
            if remote_last is not None and min_interval:
                if time.time() - float(remote_last) < min_interval:
                    return False
        except Exception:  # noqa: BLE001
            pass
    with _LOCK:
        last = _LOCAL_LAST_CALL_AT.get(p, 0.0)
    if min_interval and last and (time.time() - last) < min_interval:
        return False
    return True


def _parse_retry_after_text(text: str) -> float | None:
    """Parse Groq messages like 'Please try again in 1h12m35.424s' / '29m48.48s'."""
    import re

    raw = str(text or "")
    m = re.search(
        r"try again in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+(?:\.\d+)?)\s*s)?",
        raw,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = float(m.group(3) or 0.0)
    total = hours * 3600.0 + minutes * 60.0 + seconds
    if total <= 0:
        return None
    # Cap so one bad parse cannot park a key for days.
    return max(30.0, min(total + 5.0, 6 * 3600.0))


def _cooldown_seconds_from_response(response: httpx.Response) -> float | None:
    """Prefer Retry-After / body hints so we do not recycle keys too early."""
    header = (response.headers.get("retry-after") or "").strip()
    if header:
        try:
            return max(30.0, float(header))
        except ValueError:
            pass
    try:
        payload = response.json() if response.content else {}
    except Exception:  # noqa: BLE001
        payload = {}
    err = payload.get("error", payload) if isinstance(payload, dict) else {}
    if isinstance(err, dict):
        for key in ("retry_after", "retry-after"):
            raw = err.get(key)
            if raw is None:
                continue
            try:
                return max(30.0, float(raw))
            except (TypeError, ValueError):
                continue
        msg = str(err.get("message") or "")
        parsed = _parse_retry_after_text(msg)
        if parsed is not None:
            return parsed
        # Daily token quota: park the key for a long cooldown.
        if "tokens per day" in msg.casefold() or "tpd" in msg.casefold():
            return max(parsed or 0.0, 3600.0)
    return None


def clear_groq_key_cooldowns(pool: str = "default") -> None:
    cache = _cache()
    with _LOCK:
        fps = list(_LOCAL_COOLDOWN_UNTIL.keys())
        _LOCAL_COOLDOWN_UNTIL.clear()
    if cache is not None:
        for fp in fps:
            try:
                cache.delete(_cooldown_key(fp))
            except Exception:  # noqa: BLE001
                pass
        for key in groq_api_keys(pool=pool):
            try:
                cache.delete(_cooldown_key(_fingerprint(key)))
            except Exception:  # noqa: BLE001
                pass


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


def _available_keys(
    now: float | None = None,
    *,
    pool: str = "default",
) -> list[str]:
    now = time.time() if now is None else now
    keys = groq_api_keys(pool=pool)
    ready = []
    for key in keys:
        until = _key_cooled_until(_fingerprint(key))
        if until <= now:
            ready.append(key)
    return ready


def acquire_groq_api_key(pool: str = "default") -> str | None:
    """Round-robin among keys not in cooldown (per pool)."""
    p = _norm_pool(pool)
    ready = _available_keys(pool=p)
    if not ready:
        return None
    with _LOCK:
        idx = _RR_INDEX.get(p, 0) % len(ready)
        _RR_INDEX[p] = _RR_INDEX.get(p, 0) + 1
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
            "tokens per day",
            "tpm",
            "rpm",
        )
    )


def wait_for_groq_budget(*, block: bool = True, pool: str = "default") -> bool:
    """
    Enforce per-pool min-interval + RPM across all processes.

    Returns False if budget exhausted and block=False (caller should defer).
    """
    p = _norm_pool(pool)
    min_interval = max(
        0.0, float(getattr(settings, "GROQ_MIN_INTERVAL_SEC", 3.5) or 0.0)
    )
    max_rpm = max(1, int(getattr(settings, "GROQ_MAX_REQUESTS_PER_MIN", 12) or 12))
    cache = _cache()

    # RPM window
    while True:
        if cache is not None:
            try:
                count = cache.get(_rpm_key(p))
                if count is None:
                    cache.set(_rpm_key(p), 0, timeout=60)
                    count = 0
                if int(count) >= max_rpm:
                    if not block:
                        return False
                    time.sleep(3.0)
                    continue
            except Exception:  # noqa: BLE001
                pass

        # Min spacing between calls
        now = time.time()
        with _LOCK:
            last = _LOCAL_LAST_CALL_AT.get(p, 0.0)
        if cache is not None:
            try:
                remote_last = cache.get(_last_call_key(p))
                if remote_last is not None:
                    last = max(last, float(remote_last))
            except Exception:  # noqa: BLE001
                pass
        wait = min_interval - (now - last) if min_interval else 0.0
        if wait > 0:
            if not block:
                return False
            time.sleep(min(wait, 8.0))
            continue
        break

    # Reserve a slot
    now = time.time()
    with _LOCK:
        _LOCAL_LAST_CALL_AT[p] = now
    if cache is not None:
        try:
            cache.set(_last_call_key(p), now, timeout=120)
            try:
                cache.incr(_rpm_key(p))
            except ValueError:
                cache.set(_rpm_key(p), 1, timeout=60)
        except Exception:  # noqa: BLE001
            pass
    return True


def groq_chat_completion(
    *,
    messages: list[dict[str, str]],
    max_tokens: int = 200,
    temperature: float = 0.1,
    model: str | None = None,
    timeout: float | None = None,
    max_attempts: int | None = None,
    block_for_budget: bool = True,
    rotate_on_rate_limit: bool = False,
    pool: str = "default",
) -> dict[str, Any]:
    """
    Call Groq chat completions with key rotation + shared rate budget.

    Caps key attempts so one 429 wave cannot burn the entire key pool.
    Set rotate_on_rate_limit=True for rare high-value calls (AI briefings)
    so a different org key can succeed after a single-key 429/TPD.
    """
    p = _norm_pool(pool)
    keys = groq_api_keys(pool=p)
    if not keys:
        raise RuntimeError("No Groq API keys configured")
    ready = _available_keys(pool=p)
    if not ready:
        # Briefings (rotate_on_rate_limit): wait for at least one key instead of
        # immediately falling through to the "LLM tạm không khả dụng" stub.
        if rotate_on_rate_limit:
            wait_budget = float(
                getattr(settings, "GROQ_BRIEFING_KEY_WAIT_SEC", 90) or 90
            )
            wait_budget = max(0.0, min(wait_budget, 300.0))
            deadline = time.time() + wait_budget
            while time.time() < deadline:
                time.sleep(min(5.0, max(0.5, deadline - time.time())))
                ready = _available_keys(pool=p)
                if ready:
                    logger.info(
                        "groq briefing resumed after key cooldown wait (ready=%s pool=%s)",
                        len(ready),
                        p,
                    )
                    break
        if not ready:
            raise RuntimeError("All Groq API keys cooling down (rate limited)")

    if not wait_for_groq_budget(block=block_for_budget, pool=p):
        raise RuntimeError("Groq rate budget exhausted — defer translate")

    model = model or (
        getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
        or "llama-3.3-70b-versatile"
    )
    timeout = float(
        timeout
        if timeout is not None
        else (getattr(settings, "GROQ_TIMEOUT_SEC", 12) or 12)
    )
    attempt_cap = int(
        max_attempts
        if max_attempts is not None
        else (getattr(settings, "GROQ_MAX_KEY_ATTEMPTS", 1) or 1)
    )
    # Title translate stays conservative (≤2). Briefings may raise via max_attempts.
    hard_cap = min(len(ready), 6 if rotate_on_rate_limit else 2)
    attempt_cap = max(1, min(attempt_cap if attempt_cap > 0 else 1, hard_cap))

    url = "https://api.groq.com/openai/v1/chat/completions"
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }

    errors: list[str] = []
    attempted: set[str] = set()
    for _ in range(attempt_cap):
        api_key = acquire_groq_api_key(pool=p)
        if not api_key or api_key in attempted:
            remaining = [k for k in _available_keys(pool=p) if k not in attempted]
            if not remaining:
                break
            api_key = remaining[0]
        attempted.add(api_key)
        fp = _fingerprint(api_key)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=body)
                data = response.json() if response.content else {}
        except httpx.HTTPError as exc:
            errors.append(f"{fp}: network {exc}")
            mark_groq_key_cooldown(api_key, seconds=60)
            continue

        if response.status_code >= 400:
            err = data.get("error", data) if isinstance(data, dict) else data
            errors.append(f"{fp}: HTTP {response.status_code}")
            # Payload too large — do NOT rotate keys / cooldown; caller must shrink.
            if response.status_code == 413 or (
                isinstance(err, dict)
                and "too large" in str(err.get("message") or "").casefold()
            ):
                raise RuntimeError(f"Groq HTTP 413: payload too large ({fp})")
            if _is_rate_limit_status(response.status_code, err):
                cool = _cooldown_seconds_from_response(response)
                mark_groq_key_cooldown(api_key, seconds=cool)
                if not rotate_on_rate_limit:
                    # Title path: stop — same-org keys share TPD/RPM.
                    break
                # Briefing path: short pause then try another key (often other org).
                time.sleep(0.6)
                continue
            if response.status_code in {401, 403}:
                mark_groq_key_cooldown(api_key, seconds=3600)
                continue
            raise RuntimeError(f"Groq HTTP {response.status_code}: {err}")

        choices = data.get("choices") or []
        text = ""
        if choices:
            message = choices[0].get("message") or {}
            text = str(message.get("content") or "").strip()
        return {
            "text": text,
            "model": model,
            "key_fp": fp,
            "raw_id": data.get("id"),
        }

    raise RuntimeError(
        "Groq exhausted API key attempts: " + "; ".join(errors[:6])
    )
