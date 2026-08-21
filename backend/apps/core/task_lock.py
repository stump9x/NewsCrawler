"""Redis single-flight locks to prevent overlapping Celery work."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Iterator

from django.conf import settings

logger = logging.getLogger(__name__)

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _redis_client():
    from redis import Redis

    url = (
        getattr(settings, "REDIS_URL", "")
        or getattr(settings, "CELERY_BROKER_URL", "")
        or "redis://localhost:6379/0"
    )
    return Redis.from_url(url, decode_responses=True)


def lock_redis_key(lock_key: str) -> str:
    return f"bs:lock:{lock_key}"


def force_release_lock(lock_key: str) -> bool:
    """Delete a single-flight lock unconditionally (cancel / replace semantics)."""
    key = lock_redis_key(lock_key)
    try:
        client = _redis_client()
        return bool(client.delete(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("force_release_lock failed key=%s: %s", lock_key, exc)
        return False


@contextmanager
def single_flight(lock_key: str, *, ttl_sec: int = 600) -> Iterator[bool]:
    """Yield True when this caller owns the lock; False when another run is active."""
    key = lock_redis_key(lock_key)
    token = uuid.uuid4().hex
    client = None
    acquired = False
    try:
        client = _redis_client()
        acquired = bool(client.set(key, token, nx=True, ex=max(1, int(ttl_sec))))
    except Exception as exc:  # noqa: BLE001 — never block work if Redis hiccups
        logger.warning("single_flight acquire failed key=%s: %s", lock_key, exc)
        yield True
        return

    try:
        if not acquired:
            logger.info("single_flight skip key=%s (already running)", lock_key)
        yield acquired
    finally:
        if acquired and client is not None:
            try:
                client.eval(_RELEASE_LUA, 1, key, token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("single_flight release failed key=%s: %s", lock_key, exc)
