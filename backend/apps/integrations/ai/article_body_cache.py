"""Redis persistence for Notebook AI crawled article bodies.

RAM-safe: large plain-text bodies live in Redis DB 2 (not process memory /
LocMem). Cap per body, TTL (default 3h), bounded key index for eviction.
Falls back to Django cache only when Redis is unreachable (tests / local).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

logger = logging.getLogger(__name__)

# v6: pick real <main> over tiny related <article> cards; reject title-only cache.
_KEY_PREFIX = "nb:crawl:v6:"
_INDEX_KEY = "nb:crawl:v6:index"
_MIN_BODY = 400
_DEFAULT_TTL = 10_800  # 3 hours
_DEFAULT_MAX_CHARS = 80_000
_DEFAULT_MAX_KEYS = 64

_redis_client = None
_redis_failed = False


def crawl_cache_ttl() -> int:
    return int(
        getattr(settings, "NOTEBOOK_CRAWL_CACHE_TTL_SEC", _DEFAULT_TTL) or _DEFAULT_TTL
    )


def crawl_cache_max_chars() -> int:
    return int(
        getattr(settings, "NOTEBOOK_CRAWL_CACHE_MAX_CHARS", _DEFAULT_MAX_CHARS)
        or _DEFAULT_MAX_CHARS
    )


def crawl_cache_max_keys() -> int:
    return int(
        getattr(settings, "NOTEBOOK_CRAWL_CACHE_MAX_KEYS", _DEFAULT_MAX_KEYS)
        or _DEFAULT_MAX_KEYS
    )


def crawl_cache_enabled() -> bool:
    return bool(getattr(settings, "NOTEBOOK_CRAWL_CACHE_ENABLED", True))


def _redis_url() -> str:
    explicit = str(getattr(settings, "NOTEBOOK_CRAWL_CACHE_REDIS_URL", "") or "").strip()
    if explicit:
        return explicit
    base = str(
        getattr(settings, "REDIS_URL", "")
        or getattr(settings, "CELERY_BROKER_URL", "")
        or "redis://localhost:6379/0"
    ).strip()
    try:
        parts = urlsplit(base)
        return urlunsplit((parts.scheme, parts.netloc, "/2", "", ""))
    except Exception:  # noqa: BLE001
        return "redis://localhost:6379/2"


def _get_redis():
    """Lazy Redis client (DB 2). Returns None if unavailable."""
    global _redis_client, _redis_failed
    if _redis_failed:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis

        client = redis.Redis.from_url(
            _redis_url(),
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=2.0,
        )
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "article body cache: Redis unavailable (%s) — Django cache fallback",
            type(exc).__name__,
        )
        _redis_failed = True
        return None


def reset_redis_client_for_tests() -> None:
    """Clear lazy Redis singleton (unit tests)."""
    global _redis_client, _redis_failed
    _redis_client = None
    _redis_failed = False


def normalize_article_url(url: str) -> str:
    """Stable URL key: lower host, drop fragment, trim trailing slash."""
    raw = str(url or "").strip()
    if not raw or not raw.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:  # noqa: BLE001
        return raw.split("#", 1)[0].rstrip("/")
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()
    if not netloc:
        return ""
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def normalize_source_id(source_id: str) -> str:
    s = str(source_id or "").strip()
    if not s:
        return ""
    if s.startswith("source:"):
        return s
    if ":" not in s:
        return f"source:{s}"
    return s


def article_cache_key(
    *,
    source_id: str = "",
    url: str = "",
    notebook_id: str = "",
) -> str:
    sid = normalize_source_id(source_id)
    nurl = normalize_article_url(url)
    if not sid and not nurl:
        return ""
    nb = str(notebook_id or "").strip()
    material = f"{sid}|{nurl}|{nb}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(material).hexdigest()[:40]
    return f"{_KEY_PREFIX}{digest}"


def _cap_text(text: str) -> str:
    s = " ".join(str(text or "").split())
    return s[: crawl_cache_max_chars()]


def _django_cache_get(key: str) -> dict[str, Any] | None:
    try:
        from django.core.cache import cache

        hit = cache.get(key)
        return hit if isinstance(hit, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("article body LocMem get failed: %s", type(exc).__name__)
        return None


def _django_cache_set(key: str, payload: dict[str, Any], ttl: int) -> bool:
    try:
        from django.core.cache import cache

        cache.set(key, payload, timeout=int(ttl))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("article body LocMem set failed: %s", type(exc).__name__)
        return False


def _store_get(key: str) -> dict[str, Any] | None:
    """Redis first; on any Redis error soft-fall to LocMem (never raise)."""
    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "article body Redis get failed (%s) — LocMem fallback",
                type(exc).__name__,
            )
    return _django_cache_get(key)


def _store_set(key: str, payload: dict[str, Any], ttl: int) -> bool:
    """Prefer Redis; always try LocMem so cache misses never hard-fail digest."""
    r = _get_redis()
    redis_ok = False
    if r is not None:
        try:
            r.setex(key, int(ttl), json.dumps(payload, ensure_ascii=False))
            redis_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "article body Redis set failed (%s) — LocMem fallback",
                type(exc).__name__,
            )
    loc_ok = _django_cache_set(key, payload, ttl)
    return redis_ok or loc_ok


def _store_delete(key: str) -> bool:
    deleted = False
    r = _get_redis()
    if r is not None:
        try:
            r.delete(key)
            deleted = True
        except Exception:  # noqa: BLE001
            pass
    try:
        from django.core.cache import cache

        cache.delete(key)
        deleted = True
    except Exception:  # noqa: BLE001
        pass
    return deleted

def _touch_index(key: str, ttl: int) -> None:
    """Maintain a small LRU-ish index; drop oldest when over max keys."""
    max_keys = crawl_cache_max_keys()
    if max_keys <= 0:
        return
    r = _get_redis()
    now = time.time()
    try:
        if r is not None:
            pipe = r.pipeline()
            pipe.zadd(_INDEX_KEY, {key: now})
            pipe.expire(_INDEX_KEY, max(int(ttl) * 2, int(ttl) + 3600))
            pipe.execute()
            card = int(r.zcard(_INDEX_KEY) or 0)
            if card > max_keys:
                victims = r.zrange(_INDEX_KEY, 0, card - max_keys - 1) or []
                if victims:
                    r.zrem(_INDEX_KEY, *victims)
                    r.delete(*victims)
            return

        from django.core.cache import cache

        index = cache.get(_INDEX_KEY) or []
        if not isinstance(index, list):
            index = []
        index = [
            item for item in index if isinstance(item, dict) and item.get("k") != key
        ]
        index.append({"k": key, "t": now})
        overflow = len(index) - max_keys
        if overflow > 0:
            for stale_item in index[:overflow]:
                sk = stale_item.get("k")
                if sk and sk != key:
                    cache.delete(sk)
            index = index[overflow:]
        cache.set(_INDEX_KEY, index, timeout=max(ttl * 2, ttl + 3600))
    except Exception as exc:  # noqa: BLE001
        logger.debug("article body cache index touch failed: %s", type(exc).__name__)


def get_cached_article_body(
    *,
    source_id: str = "",
    url: str = "",
    notebook_id: str = "",
) -> dict[str, Any] | None:
    """Return cached payload or None. Does not crawl."""
    if not crawl_cache_enabled():
        return None
    key = article_cache_key(source_id=source_id, url=url, notebook_id=notebook_id)
    if not key:
        return None
    hit = _store_get(key)
    if not hit:
        return None
    text = _cap_text(str(hit.get("text") or ""))
    if len(text) < _MIN_BODY:
        return None
    return {
        "ok": True,
        "text": text,
        "title": str(hit.get("title") or "")[:400],
        "url": str(hit.get("url") or normalize_article_url(url)),
        "source_id": str(hit.get("source_id") or normalize_source_id(source_id)),
        "notebook_id": str(hit.get("notebook_id") or notebook_id or ""),
        "backend": str(hit.get("backend") or "cache"),
        "chars": len(text),
        "cache_hit": True,
        "stored_at": hit.get("stored_at"),
        "cache_key": key,
    }


def set_cached_article_body(
    *,
    text: str,
    source_id: str = "",
    url: str = "",
    notebook_id: str = "",
    title: str = "",
    backend: str = "",
) -> dict[str, Any] | None:
    """Store capped plain-text body. Returns stored meta or None if too short."""
    if not crawl_cache_enabled():
        return None
    # Re-clean on write so cache never holds image URLs / HTML chrome.
    try:
        from apps.integrations.web_reader.article_text import extract_article_text

        cleaned = extract_article_text(
            text, title_hint=title, max_chars=crawl_cache_max_chars()
        )
        body = str(cleaned.get("text") or "")
        title = str(cleaned.get("title") or title or "")[:400]
    except Exception:  # noqa: BLE001
        body = _cap_text(text)
    body = _cap_text(body)
    if len(body) < _MIN_BODY:
        return None
    key = article_cache_key(source_id=source_id, url=url, notebook_id=notebook_id)
    if not key:
        return None
    ttl = crawl_cache_ttl()
    payload = {
        "text": body,
        "title": str(title or "")[:400],
        "url": normalize_article_url(url),
        "source_id": normalize_source_id(source_id),
        "notebook_id": str(notebook_id or "").strip(),
        "backend": str(backend or "")[:80],
        "chars": len(body),
        "stored_at": time.time(),
    }
    if not _store_set(key, payload, ttl):
        return None
    _touch_index(key, ttl)
    return {
        "ok": True,
        "cache_key": key,
        "chars": len(body),
        "ttl_sec": ttl,
        "cache_hit": False,
    }


def invalidate_cached_article_body(
    *,
    source_id: str = "",
    url: str = "",
    notebook_id: str = "",
) -> bool:
    key = article_cache_key(source_id=source_id, url=url, notebook_id=notebook_id)
    if not key:
        return False
    return _store_delete(key)


def resolve_article_bodies(
    items: list[dict[str, Any]],
    *,
    crawl_on_miss: bool = False,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Resolve bodies for many sources — same pipeline as Transformation preview.

    Order per item:
      1. Redis/Django crawl cache (TTL ~3h) — Chat must hit this after Transform
      2. Clean Open Notebook ``existing_text`` / ``body`` when usable (not chrome)
      3. Optional live crawl (``crawl_on_miss`` / ``refresh``) via shared cleaner
         + Wigolo escalation on captcha

    Each item: ``{source_id, url, notebook_id?, title?, existing_text?/body?}``.
    """
    from apps.integrations.ai.notebook_digest import _fetch_article_with_retry
    from apps.integrations.web_reader.article_text import (
        extract_article_text,
        is_usable_article_body,
        looks_like_page_chrome,
        looks_like_title_only,
    )

    out: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        source_id = normalize_source_id(str(raw.get("source_id") or raw.get("id") or ""))
        url = str(raw.get("url") or "").strip()
        notebook_id = str(raw.get("notebook_id") or "").strip()
        title = str(raw.get("title") or "").strip()[:400]
        existing_raw = str(
            raw.get("existing_text")
            or raw.get("body")
            or raw.get("full_text")
            or ""
        )
        base = {
            "source_id": source_id,
            "url": url,
            "notebook_id": notebook_id,
            "title": title,
            "ok": False,
            "text": "",
            "chars": 0,
            "cache_hit": False,
            "crawled": False,
            "backend": "",
            "error": "",
        }
        if not url.startswith("http") and not source_id and not existing_raw.strip():
            base["error"] = "missing_url"
            out.append(base)
            continue

        if not refresh:
            hit = get_cached_article_body(
                source_id=source_id, url=url, notebook_id=notebook_id
            )
            if hit:
                cached_text = str(hit.get("text") or "")
                # Title-only / title+dek cache must not short-circuit a live crawl.
                if looks_like_title_only(cached_text, title or str(hit.get("title") or "")):
                    invalidate_cached_article_body(
                        source_id=source_id, url=url, notebook_id=notebook_id
                    )
                else:
                    out.append(
                        {
                            **base,
                            "ok": True,
                            "text": hit["text"],
                            "title": hit.get("title") or title,
                            "chars": hit["chars"],
                            "cache_hit": True,
                            "crawled": False,
                            "backend": "cache",
                        }
                    )
                    continue

        if refresh:
            invalidate_cached_article_body(
                source_id=source_id, url=url, notebook_id=notebook_id
            )

        # 2) Clean stored Open Notebook body (same cleaner as Transform).
        stored_text = ""
        stored_title = title
        if existing_raw.strip():
            try:
                cleaned = extract_article_text(
                    existing_raw,
                    title_hint=title,
                    max_chars=crawl_cache_max_chars(),
                )
                stored_text = _cap_text(str(cleaned.get("text") or ""))
                stored_title = str(cleaned.get("title") or title)[:400]
            except Exception:  # noqa: BLE001
                stored_text = _cap_text(existing_raw)
        if (
            is_usable_article_body(stored_text, min_chars=_MIN_BODY)
            and not looks_like_title_only(stored_text, stored_title or title)
        ):
            try:
                set_cached_article_body(
                    text=stored_text,
                    source_id=source_id,
                    url=url,
                    notebook_id=notebook_id,
                    title=stored_title,
                    backend="stored",
                )
            except Exception:  # noqa: BLE001
                pass
            out.append(
                {
                    **base,
                    "ok": True,
                    "text": stored_text,
                    "title": stored_title,
                    "chars": len(stored_text),
                    "cache_hit": False,
                    "crawled": False,
                    "backend": "stored",
                }
            )
            continue

        if not crawl_on_miss and not refresh:
            base["error"] = "cache_miss"
            out.append(base)
            continue

        if not url.startswith("http"):
            base["error"] = "missing_url"
            out.append(base)
            continue

        try:
            fetch = _fetch_article_with_retry(url)
        except Exception as exc:  # noqa: BLE001
            base["error"] = type(exc).__name__
            out.append(base)
            continue

        text = _cap_text(str(fetch.get("text") or ""))
        title_f = str(fetch.get("title") or title)[:400]
        # Re-clean + optional AI extract when chrome remains.
        try:
            from apps.integrations.ai.notebook_digest import ai_extract_main_article

            cleaned = extract_article_text(
                text, title_hint=title_f, max_chars=crawl_cache_max_chars()
            )
            text = _cap_text(str(cleaned.get("text") or text))
            title_f = str(cleaned.get("title") or title_f)[:400]
            if looks_like_page_chrome(text) or len(text) < _MIN_BODY:
                ai = ai_extract_main_article(
                    str(fetch.get("text") or text),
                    title=title_f,
                    max_chars=crawl_cache_max_chars(),
                )
                if ai.get("ok") and len(str(ai.get("text") or "")) >= _MIN_BODY:
                    text = _cap_text(str(ai["text"]))
                    title_f = str(ai.get("title") or title_f)[:400]
                    fetch = {
                        **fetch,
                        "backend": f"{fetch.get('backend') or 'crawl'}+{ai.get('provider') or 'ai'}",
                    }
        except Exception:  # noqa: BLE001
            pass

        ok = bool(fetch.get("ok")) and len(text) >= _MIN_BODY
        if ok and looks_like_title_only(text, title_f):
            ok = False
            base["error"] = "title_only"
        if ok:
            stored = set_cached_article_body(
                text=text,
                source_id=source_id,
                url=url,
                notebook_id=notebook_id,
                title=title_f,
                backend=str(fetch.get("backend") or "crawl"),
            )
            # Prefer post-cache cleaned body when available.
            if stored and stored.get("chars"):
                hit = get_cached_article_body(
                    source_id=source_id, url=url, notebook_id=notebook_id
                )
                if hit and hit.get("text"):
                    text = hit["text"]
                    title_f = str(hit.get("title") or title_f)[:400]
            out.append(
                {
                    **base,
                    "ok": True,
                    "text": text,
                    "title": title_f,
                    "chars": len(text),
                    "cache_hit": False,
                    "crawled": True,
                    "backend": str(fetch.get("backend") or "crawl"),
                }
            )
        else:
            # Last resort: weak stored text if crawl failed.
            if len(stored_text) >= _MIN_BODY:
                out.append(
                    {
                        **base,
                        "ok": True,
                        "text": stored_text,
                        "title": stored_title,
                        "chars": len(stored_text),
                        "cache_hit": False,
                        "crawled": False,
                        "backend": "stored_weak",
                    }
                )
            else:
                base["error"] = str(fetch.get("error") or "empty")[:160]
                base["backend"] = str(fetch.get("backend") or "")
                out.append(base)
    return out
