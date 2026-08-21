"""Enrich last30days findings with Wigolo fetch when snippets are thin."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from apps.integrations.models import Last30DaysFinding

logger = logging.getLogger(__name__)

_THIN_SNIPPET = 80


def _thin(text: str) -> bool:
    return len(" ".join((text or "").split()).strip()) < _THIN_SNIPPET


def enrich_finding_body(finding: Last30DaysFinding) -> dict[str, Any]:
    """Fetch page body via Wigolo when title-only / thin snippet."""
    from apps.integrations.web_reader.wigolo import fetch_wigolo, wigolo_fetch_enabled

    if not wigolo_fetch_enabled():
        return {"id": finding.id, "skipped": True, "reason": "wigolo_fetch_disabled"}
    url = (finding.url or "").strip()
    if not url.startswith("http"):
        return {"id": finding.id, "skipped": True, "reason": "no_url"}
    if not _thin(finding.snippet) and (finding.snippet_vi or "").strip():
        return {"id": finding.id, "skipped": True, "reason": "already_rich"}

    payload = fetch_wigolo(
        url,
        max_chars=int(getattr(settings, "WIGOLO_FETCH_MAX_CHARS", 12000) or 12000),
    )
    meta = dict(finding.metadata or {})
    meta["wigolo_fetch"] = {
        "ok": bool(payload.get("ok")),
        "error": str(payload.get("error") or "")[:200],
        "title": str(payload.get("title") or "")[:512],
    }
    finding.metadata = meta
    if not payload.get("ok"):
        finding.save(update_fields=["metadata", "updated_at"])
        return {
            "id": finding.id,
            "ok": False,
            "error": payload.get("error"),
        }

    text = str(payload.get("text") or "").strip()
    # Keep a readable snippet for UI / translation / brief synthesis.
    snippet = " ".join(text.split())[:2400]
    if snippet:
        finding.snippet = snippet
    # Prefer fetched title only when original is placeholder.
    fetched_title = str(payload.get("title") or "").strip()
    if fetched_title and (
        not finding.title
        or finding.title == "(untitled)"
        or len(finding.title) < 8
    ):
        finding.title = fetched_title[:512]
    finding.save(update_fields=["snippet", "title", "metadata", "updated_at"])
    return {"id": finding.id, "ok": True, "chars": len(snippet)}


def enrich_findings(finding_ids: list[int], *, limit: int = 20) -> dict[str, Any]:
    ids = [int(i) for i in finding_ids if i][: max(1, min(int(limit or 20), 40))]
    stats = {"selected": len(ids), "ok": 0, "failed": 0, "skipped": 0}
    for fid in ids:
        try:
            finding = Last30DaysFinding.objects.get(pk=fid)
        except Last30DaysFinding.DoesNotExist:
            stats["skipped"] += 1
            continue
        try:
            result = enrich_finding_body(finding)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wigolo enrich finding=%s failed: %s", fid, exc)
            stats["failed"] += 1
            continue
        if result.get("skipped"):
            stats["skipped"] += 1
        elif result.get("ok"):
            stats["ok"] += 1
        else:
            stats["failed"] += 1
    return stats


def enqueue_finding_enrichment(finding_ids: list[int]) -> None:
    thin_ids: list[int] = []
    for fid in finding_ids:
        try:
            finding = Last30DaysFinding.objects.only("id", "snippet", "url").get(pk=fid)
        except Last30DaysFinding.DoesNotExist:
            continue
        if finding.url and _thin(finding.snippet):
            thin_ids.append(finding.id)
    if not thin_ids:
        return
    try:
        from apps.integrations.tasks import enrich_last30days_findings_task

        enrich_last30days_findings_task.delay(thin_ids)
    except Exception:  # noqa: BLE001
        logger.exception("enqueue last30days enrich failed ids=%s", thin_ids[:5])
