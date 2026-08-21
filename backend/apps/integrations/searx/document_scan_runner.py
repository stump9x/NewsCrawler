"""Replace-style document scan kicks — no Celery backlog of stale scans.

Old queued / failed / superseded runs are revoked or generation-bumped so they
no longer matter. Only the latest kick should execute work.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import current_app
from django.conf import settings

logger = logging.getLogger(__name__)

LOCK_NAME = "integrations.scan_document_pdfs"
TASK_NAME = "integrations.scan_document_pdfs"
ACTIVE_TASK_KEY = "bs:docscan:active_task"
GENERATION_KEY = "bs:docscan:generation"


def _redis():
    from apps.core.task_lock import _redis_client

    return _redis_client()


def document_scan_lock_ttl_sec() -> int:
    return max(300, int(getattr(settings, "DOCUMENT_SCAN_LOCK_TTL_SEC", 1200) or 1200))


def current_generation() -> int:
    try:
        raw = _redis().get(GENERATION_KEY)
        return int(raw or 0)
    except Exception:  # noqa: BLE001
        return 0


def bump_generation() -> int:
    try:
        client = _redis()
        value = int(client.incr(GENERATION_KEY))
        client.expire(GENERATION_KEY, 86400)
        return value
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan bump_generation failed: %s", exc)
        return current_generation() + 1


def is_current_generation(generation: int | None) -> bool:
    if generation is None:
        return True
    try:
        return int(generation) == current_generation()
    except (TypeError, ValueError):
        return False


def _clear_active_task_id() -> None:
    try:
        _redis().delete(ACTIVE_TASK_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan clear active task failed: %s", exc)


def _store_active_task_id(task_id: str) -> None:
    try:
        _redis().set(ACTIVE_TASK_KEY, task_id, ex=document_scan_lock_ttl_sec() + 600)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan store active task failed: %s", exc)


def _active_task_id() -> str | None:
    try:
        value = _redis().get(ACTIVE_TASK_KEY)
        return str(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def _task_name_from_entry(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or "")
    if name:
        return name
    request = entry.get("request")
    if isinstance(request, dict):
        return str(request.get("name") or "")
    return ""


def _task_id_from_entry(entry: dict[str, Any]) -> str:
    task_id = str(entry.get("id") or "")
    if task_id:
        return task_id
    request = entry.get("request")
    if isinstance(request, dict):
        return str(request.get("id") or "")
    return ""


def _collect_worker_scan_task_ids() -> list[str]:
    ids: list[str] = []
    try:
        inspect = current_app.control.inspect(timeout=1.0)
        if inspect is None:
            return ids
        for payload in (
            inspect.active() or {},
            inspect.reserved() or {},
            inspect.scheduled() or {},
        ):
            for entries in payload.values():
                for entry in entries or []:
                    if not isinstance(entry, dict):
                        continue
                    if _task_name_from_entry(entry) != TASK_NAME:
                        continue
                    task_id = _task_id_from_entry(entry)
                    if task_id:
                        ids.append(task_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan inspect failed: %s", exc)
    # Preserve order, drop dupes.
    seen: set[str] = set()
    out: list[str] = []
    for task_id in ids:
        if task_id not in seen:
            seen.add(task_id)
            out.append(task_id)
    return out


def purge_document_scan_broker_messages() -> int:
    """Drop pending Celery broker messages for document scan (Redis list)."""
    removed = 0
    try:
        client = _redis()
        # Default Celery Redis transport queue name.
        for queue_name in ("celery",):
            raw_items = client.lrange(queue_name, 0, -1)
            if not isinstance(raw_items, (list, tuple)) or not raw_items:
                continue
            keep: list[Any] = []
            for raw in raw_items:
                text = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, (bytes, bytearray))
                    else str(raw)
                )
                if TASK_NAME in text:
                    removed += 1
                    continue
                keep.append(raw)
            if removed:
                pipe = client.pipeline()
                pipe.delete(queue_name)
                if keep:
                    pipe.rpush(queue_name, *keep)
                pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan broker purge failed: %s", exc)
    if removed:
        logger.info("document_scan purged %s broker message(s)", removed)
    return removed


def revoke_stale_document_scans(
    *,
    reason: str = "replace",
    terminate_running: bool = True,
) -> dict[str, Any]:
    """
    Invalidate prior document-scan work.

    - Bumps generation so any leftover Celery messages become no-ops.
    - Optionally terminates the previously tracked / worker-held tasks.
    - Clears the single-flight lock so a fresh run can start immediately.
    - Purges pending scan messages from the broker queue.
    """
    from apps.core.task_lock import force_release_lock

    revoked: list[str] = []
    targets: list[str] = []
    active = _active_task_id()
    if active:
        targets.append(active)
    targets.extend(_collect_worker_scan_task_ids())

    seen: set[str] = set()
    for task_id in targets:
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        if terminate_running:
            try:
                current_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                revoked.append(task_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("document_scan revoke %s failed: %s", task_id, exc)

    purged = purge_document_scan_broker_messages()
    generation = bump_generation()
    force_release_lock(LOCK_NAME)
    _clear_active_task_id()
    logger.info(
        "document_scan revoke reason=%s generation=%s revoked=%s purged=%s terminate=%s",
        reason,
        generation,
        revoked,
        purged,
        terminate_running,
    )
    return {
        "reason": reason,
        "generation": generation,
        "revoked": revoked,
        "purged": purged,
        "terminate_running": terminate_running,
    }


def enqueue_document_scan(
    *,
    limit_per_keyword: int | None = None,
    force: bool = False,
    attempt: int = 1,
    countdown: int = 0,
    terminate_running: bool = True,
) -> dict[str, Any]:
    """
    Start exactly one fresh document scan.

    Cancels/supersedes older kicks first so failed or stuck runs do not linger
    in the Celery queue.
    """
    if not bool(getattr(settings, "DOCUMENT_SCAN_ENABLED", False)):
        return {
            "skipped": True,
            "reason": "document_scan_disabled",
            "task_id": None,
            "status": "disabled",
            "generation": 0,
        }

    # Import lazily to avoid circular import with tasks.py.
    from apps.integrations.tasks import scan_document_pdfs

    meta = revoke_stale_document_scans(
        reason="enqueue",
        terminate_running=terminate_running,
    )
    generation = int(meta["generation"])
    async_result = scan_document_pdfs.apply_async(
        kwargs={
            "limit_per_keyword": limit_per_keyword,
            "force": bool(force),
            "generation": generation,
            "attempt": max(1, int(attempt or 1)),
        },
        countdown=max(0, int(countdown or 0)),
    )
    _store_active_task_id(async_result.id)
    try:
        from apps.integrations.searx.document_scan_status import mark_scan_queued

        mark_scan_queued(task_id=async_result.id, generation=generation)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan mark queued failed: %s", exc)
    return {
        "task_id": async_result.id,
        "status": "started",
        "generation": generation,
        "revoked": meta.get("revoked") or [],
        "attempt": max(1, int(attempt or 1)),
        "countdown": max(0, int(countdown or 0)),
    }
