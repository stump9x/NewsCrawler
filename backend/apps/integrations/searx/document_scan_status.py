"""Redis-backed live status for document PDF sweeps (UI progress / countdown)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

STATUS_KEY = "bs:docscan:status"
STATUS_TTL_SEC = 86400
# If UI stays on "queued" longer than this with no worker pickup, recover.
STALE_QUEUED_SEC = 45
STALE_RUNNING_SEC = 1500


def document_scan_interval_sec() -> int:
    """Seconds between automatic kicks (matches Celery beat schedule)."""
    return max(
        60,
        int(getattr(settings, "DOCUMENT_SCAN_INTERVAL_SEC", 1800) or 1800),
    )


def _redis():
    from apps.core.task_lock import _redis_client

    return _redis_client()


def _lock_held() -> bool:
    try:
        from apps.integrations.searx.document_scan_runner import LOCK_NAME
        from apps.core.task_lock import lock_redis_key

        return bool(_redis().get(lock_redis_key(LOCK_NAME)))
    except Exception:  # noqa: BLE001
        return False


def _default_status() -> dict[str, Any]:
    return {
        "state": "idle",  # idle | queued | running | waiting | error
        "percent": 0,
        "message": "",
        "task_id": "",
        "generation": 0,
        "total_keywords": 0,
        "done_keywords": 0,
        "current_keyword": "",
        "current_query": "",
        "created_this_run": 0,
        "hits_this_run": 0,
        "cooldown_skipped": 0,
        "recent_created_ids": [],
        "started_at": None,
        "finished_at": None,
        "next_scan_at": None,
        "error": "",
        "updated_at": None,
    }


def _read_raw_status() -> dict[str, Any]:
    status = _default_status()
    try:
        raw = _redis().get(STATUS_KEY)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                status.update(parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan status read failed: %s", exc)
    return status


def _recover_stuck_status(status: dict[str, Any]) -> dict[str, Any]:
    """Clear orphaned queued/running UI state when the worker never advanced."""
    state = str(status.get("state") or "idle")
    if state not in {"queued", "running"}:
        return status
    try:
        updated = float(status.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated = 0.0
    age = time.time() - updated if updated else 10_000
    locked = _lock_held()
    if state == "queued" and age >= STALE_QUEUED_SEC and not locked:
        logger.warning(
            "document_scan recovering stale queued status age=%.0fs task=%s",
            age,
            status.get("task_id"),
        )
        return set_document_scan_status(
            state="error",
            percent=0,
            message="Worker không nhận task quét — đã hủy chờ. Bấm Quét ngay để chạy lại.",
            finished_at=time.time(),
            next_scan_at=time.time() + 30,
            current_keyword="",
            current_query="",
            error="stale_queued",
            seconds_until_next=30,
        )
    if state == "running" and age >= STALE_RUNNING_SEC and not locked:
        logger.warning(
            "document_scan recovering stale running status age=%.0fs",
            age,
        )
        return set_document_scan_status(
            state="error",
            percent=int(status.get("percent") or 0),
            message="Quét bị treo — đã hủy. Bấm Quét ngay để chạy lại.",
            finished_at=time.time(),
            next_scan_at=time.time() + 30,
            current_keyword="",
            current_query="",
            error="stale_running",
            seconds_until_next=30,
        )
    return status


def get_document_scan_status() -> dict[str, Any]:
    status = _recover_stuck_status(_read_raw_status())

    # Derive waiting countdown when idle/waiting after a finished run.
    now = time.time()
    next_at = status.get("next_scan_at")
    state = str(status.get("state") or "idle")
    if state in {"idle", "waiting"} and next_at:
        try:
            next_ts = float(next_at)
        except (TypeError, ValueError):
            next_ts = 0.0
        remaining = max(0, int(next_ts - now))
        status["seconds_until_next"] = remaining
        if remaining > 0 and state == "idle":
            status["state"] = "waiting"
            status["message"] = f"Chờ lượt quét mới sau {max(1, (remaining + 59) // 60)} phút"
        elif remaining <= 0 and state == "waiting":
            status["state"] = "idle"
            status["message"] = "Sẵn sàng quét"
            status["seconds_until_next"] = 0
    elif state == "error":
        try:
            next_ts = float(next_at or 0)
        except (TypeError, ValueError):
            next_ts = 0.0
        status["seconds_until_next"] = max(0, int(next_ts - now)) if next_ts else 0
    else:
        status["seconds_until_next"] = int(status.get("seconds_until_next") or 0)
    return status


def set_document_scan_status(**fields: Any) -> dict[str, Any]:
    status = _read_raw_status()
    status.update(fields)
    status["updated_at"] = time.time()
    try:
        _redis().set(STATUS_KEY, json.dumps(status, default=str), ex=STATUS_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_scan status write failed: %s", exc)
    return status


def mark_scan_queued(*, task_id: str = "", generation: int = 0) -> dict[str, Any]:
    return set_document_scan_status(
        state="queued",
        percent=0,
        message="Đang đợi worker bắt đầu quét…",
        task_id=task_id or "",
        generation=int(generation or 0),
        total_keywords=0,
        done_keywords=0,
        current_keyword="",
        current_query="",
        created_this_run=0,
        hits_this_run=0,
        cooldown_skipped=0,
        recent_created_ids=[],
        started_at=None,
        finished_at=None,
        error="",
        seconds_until_next=0,
    )


def mark_scan_running(
    *,
    total_keywords: int,
    generation: int | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "state": "running",
        "percent": 0 if total_keywords else 100,
        "message": "Đang quét tài liệu…",
        "total_keywords": int(total_keywords or 0),
        "done_keywords": 0,
        "current_keyword": "",
        "current_query": "",
        "created_this_run": 0,
        "hits_this_run": 0,
        "cooldown_skipped": 0,
        "recent_created_ids": [],
        "started_at": time.time(),
        "finished_at": None,
        "next_scan_at": None,
        "error": "",
        "seconds_until_next": 0,
    }
    if generation is not None:
        fields["generation"] = int(generation)
    if task_id is not None:
        fields["task_id"] = str(task_id)
    return set_document_scan_status(**fields)


def mark_scan_progress(
    *,
    done: int,
    total: int,
    keyword: str = "",
    query: str = "",
    created_delta: int = 0,
    hits_delta: int = 0,
    cooldown: bool = False,
    created_ids: list[int] | None = None,
) -> dict[str, Any]:
    status = _read_raw_status()
    total = max(0, int(total or 0))
    done = max(0, min(int(done or 0), total or int(done or 0)))
    percent = 100 if total <= 0 else int(round(100.0 * done / total))
    created = int(status.get("created_this_run") or 0) + max(0, int(created_delta or 0))
    hits = int(status.get("hits_this_run") or 0) + max(0, int(hits_delta or 0))
    cool = int(status.get("cooldown_skipped") or 0) + (1 if cooldown else 0)
    recent = list(status.get("recent_created_ids") or [])
    for doc_id in created_ids or []:
        try:
            doc_id_int = int(doc_id)
        except (TypeError, ValueError):
            continue
        if doc_id_int not in recent:
            recent.append(doc_id_int)
    recent = recent[-40:]
    message = f"Đang quét “{keyword}”…" if keyword else "Đang quét tài liệu…"
    if created:
        message = f"{message} · phát hiện {created} tài liệu mới"
    return set_document_scan_status(
        state="running",
        percent=min(99, percent) if done < total else 100,
        message=message,
        done_keywords=done,
        total_keywords=total,
        current_keyword=keyword or "",
        current_query=query or "",
        created_this_run=created,
        hits_this_run=hits,
        cooldown_skipped=cool,
        recent_created_ids=recent,
        seconds_until_next=0,
    )


def mark_scan_finished(
    *,
    created: int = 0,
    error: str = "",
    superseded: bool = False,
) -> dict[str, Any]:
    now = time.time()
    interval = document_scan_interval_sec()
    next_at = now + interval
    if error and not superseded:
        return set_document_scan_status(
            state="error",
            percent=100,
            message=f"Quét lỗi: {error}",
            finished_at=now,
            next_scan_at=next_at,
            current_keyword="",
            current_query="",
            error=str(error)[:500],
            seconds_until_next=interval,
        )
    created_n = int(created or 0)
    message = (
        f"Hoàn tất · phát hiện {created_n} tài liệu mới"
        if created_n
        else "Hoàn tất · không có tài liệu mới"
    )
    return set_document_scan_status(
        state="waiting",
        percent=100,
        message=message,
        finished_at=now,
        next_scan_at=next_at,
        current_keyword="",
        current_query="",
        created_this_run=created_n if created_n else None,
        error="",
        seconds_until_next=interval,
    )
