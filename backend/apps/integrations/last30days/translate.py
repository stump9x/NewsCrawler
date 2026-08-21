"""Translate last30days finding titles/snippets — Groq first, Google/Ollama fallback."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.core.text import prefer_my_for_united_states
from apps.integrations.ai.translate import (
    TitleTranslateError,
    build_google_translate_client,
    cached_document_or_threat_translation,
    google_translate_title,
    groq_translate_title,
    is_google_circuit_open,
    looks_vietnamese,
    note_groq_failure,
    note_groq_success,
    ollama_fallback_available,
    ollama_translate_title,
    prefer_groq_translate,
    title_hash,
)
from apps.integrations.models import Last30DaysFinding

logger = logging.getLogger(__name__)

_OK = "ok"
_PENDING = "pending"
_SKIPPED = "skipped"
_FAILED = "failed"


def last30days_translate_enabled() -> bool:
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return False
    return bool(getattr(settings, "LAST30DAYS_TRANSLATE_ENABLED", True))


def _persist(
    finding: Last30DaysFinding,
    *,
    title_vi: str,
    status: str,
    provider: str,
    snippet_vi: str | None = None,
) -> None:
    finding.title_vi = prefer_my_for_united_states(title_vi)[:512]
    finding.title_vi_status = status
    finding.title_vi_provider = (provider or "")[:64]
    finding.title_vi_translated_at = timezone.now()
    fields = [
        "title_vi",
        "title_vi_status",
        "title_vi_provider",
        "title_vi_translated_at",
        "title_hash",
        "updated_at",
    ]
    if snippet_vi is not None:
        finding.snippet_vi = prefer_my_for_united_states(snippet_vi)[:4000]
        fields.append("snippet_vi")
    finding.save(update_fields=fields)


def _translate_snippet(text: str) -> str:
    """Best-effort snippet VI via the same title translators (truncated)."""
    raw = " ".join((text or "").split()).strip()
    if not raw or looks_vietnamese(raw):
        return raw
    clipped = raw[:700]
    try:
        if prefer_groq_translate():
            return groq_translate_title(clipped)[:2000]
    except TitleTranslateError:
        pass
    try:
        if not is_google_circuit_open():
            with build_google_translate_client() as client:
                return google_translate_title(clipped, client=client)[:2000]
    except TitleTranslateError:
        pass
    if ollama_fallback_available():
        try:
            return ollama_translate_title(clipped)[:2000]
        except TitleTranslateError:
            pass
    return ""


def _finding_is_stuck(finding: Last30DaysFinding) -> bool:
    stuck_sec = max(
        60, int(getattr(settings, "TITLE_TRANSLATE_STUCK_SEC", 900) or 900)
    )
    ref = finding.created_at or finding.updated_at or timezone.now()
    return (timezone.now() - ref).total_seconds() >= stuck_sec


def translate_last30days_finding(
    finding: Last30DaysFinding,
    *,
    force: bool = False,
    google_client: httpx.Client | None = None,
    allow_fallback: bool = False,
) -> dict[str, Any]:
    """Groq-first title translation; Google → Ollama after stuck window."""
    # Best-effort Wigolo fetch when snippet is title-only.
    try:
        from .enrich import enrich_finding_body

        if len(" ".join((finding.snippet or "").split())) < 80 and finding.url:
            enrich_finding_body(finding)
            finding.refresh_from_db()
    except Exception:  # noqa: BLE001
        pass

    title = (finding.title or "").strip()
    if not title or title == "(untitled)":
        finding.title_hash = title_hash(title) if title else ""
        _persist(
            finding,
            title_vi=title,
            status=_SKIPPED,
            provider="empty",
            snippet_vi=finding.snippet or "",
        )
        return {"id": finding.id, "status": _SKIPPED, "provider": "empty"}

    finding.title_hash = title_hash(title)

    if (
        not force
        and (finding.title_vi or "").strip()
        and finding.title_vi_status in {_OK, _SKIPPED}
    ):
        return {
            "id": finding.id,
            "status": finding.title_vi_status,
            "provider": finding.title_vi_provider,
            "cached": True,
        }

    if looks_vietnamese(title):
        snip = finding.snippet or ""
        snip_vi = snip if looks_vietnamese(snip) else (snip and _translate_snippet(snip) or snip)
        _persist(
            finding,
            title_vi=title,
            status=_SKIPPED,
            provider="skip_vi",
            snippet_vi=snip_vi,
        )
        return {"id": finding.id, "status": _SKIPPED, "provider": "skip_vi"}

    cached = cached_document_or_threat_translation(title)
    if cached and not force:
        vi, status, provider = cached
        snip = finding.snippet or ""
        snip_vi = ""
        if snip:
            snip_vi = snip if looks_vietnamese(snip) else (_translate_snippet(snip) or "")
        _persist(
            finding,
            title_vi=vi,
            status=status if status in {_OK, _SKIPPED} else _OK,
            provider=provider,
            snippet_vi=snip_vi,
        )
        return {
            "id": finding.id,
            "status": finding.title_vi_status,
            "provider": provider,
            "cached": True,
        }

    stuck = allow_fallback or _finding_is_stuck(finding)
    snippet_vi: str | None = None

    if prefer_groq_translate():
        try:
            draft = groq_translate_title(title)
            note_groq_success()
            provider = (
                f"groq:{getattr(settings, 'GROQ_MODEL', 'llama-3.3-70b-versatile')}"
            )
            if finding.snippet and not looks_vietnamese(finding.snippet):
                try:
                    snippet_vi = _translate_snippet(finding.snippet) or None
                except Exception:  # noqa: BLE001
                    snippet_vi = None
            elif finding.snippet:
                snippet_vi = finding.snippet
            _persist(
                finding,
                title_vi=draft,
                status=_OK,
                provider=provider[:64],
                snippet_vi=snippet_vi,
            )
            return {
                "id": finding.id,
                "status": _OK,
                "provider": provider[:64],
            }
        except TitleTranslateError as exc:
            logger.info("last30days groq failed id=%s: %s", finding.id, exc)
            note_groq_failure(reason=str(exc)[:120])
            if not stuck:
                finding.title_vi_status = _PENDING
                finding.title_vi_provider = "awaiting_groq"
                finding.save(
                    update_fields=[
                        "title_hash",
                        "title_vi_status",
                        "title_vi_provider",
                        "updated_at",
                    ]
                )
                return {
                    "id": finding.id,
                    "status": _PENDING,
                    "provider": "awaiting_groq",
                }

    # Fallback path (stuck / Groq unavailable).
    owned = False
    client = google_client
    if client is None and not is_google_circuit_open():
        client = build_google_translate_client()
        owned = True
    try:
        if client is not None and not is_google_circuit_open():
            try:
                draft = google_translate_title(title, client=client)
                provider = "google"
                if finding.snippet and not looks_vietnamese(finding.snippet):
                    try:
                        snippet_vi = google_translate_title(
                            " ".join(finding.snippet.split())[:700],
                            client=client,
                        )[:2000]
                    except TitleTranslateError:
                        snippet_vi = None
                elif finding.snippet:
                    snippet_vi = finding.snippet
                _persist(
                    finding,
                    title_vi=draft,
                    status=_OK,
                    provider=provider,
                    snippet_vi=snippet_vi,
                )
                return {"id": finding.id, "status": _OK, "provider": provider}
            except TitleTranslateError as exc:
                logger.info("last30days google failed id=%s: %s", finding.id, exc)

        if ollama_fallback_available():
            try:
                draft = ollama_translate_title(title)
                provider = "ollama-fallback"
                if finding.snippet and not looks_vietnamese(finding.snippet):
                    try:
                        snippet_vi = ollama_translate_title(
                            " ".join(finding.snippet.split())[:700]
                        )[:2000]
                    except TitleTranslateError:
                        snippet_vi = None
                elif finding.snippet:
                    snippet_vi = finding.snippet
                _persist(
                    finding,
                    title_vi=draft,
                    status=_OK,
                    provider=provider,
                    snippet_vi=snippet_vi,
                )
                return {"id": finding.id, "status": _OK, "provider": provider}
            except TitleTranslateError as exc:
                logger.info("last30days ollama failed id=%s: %s", finding.id, exc)
    finally:
        if owned and client is not None:
            client.close()

    finding.title_vi_status = _FAILED if stuck else _PENDING
    finding.title_vi_provider = "awaiting_google" if stuck else "awaiting_groq"
    finding.save(
        update_fields=[
            "title_hash",
            "title_vi_status",
            "title_vi_provider",
            "updated_at",
        ]
    )
    return {
        "id": finding.id,
        "status": finding.title_vi_status,
        "provider": finding.title_vi_provider,
    }


def translate_last30days_findings(
    finding_ids: list[int] | None = None,
    *,
    limit: int = 25,
    allow_fallback: bool = False,
) -> dict[str, Any]:
    """Batch-translate pending last30days findings (Groq-paced)."""
    if not last30days_translate_enabled():
        return {"skipped": True, "reason": "disabled"}

    qs = Last30DaysFinding.objects.all().order_by("id")
    if finding_ids:
        qs = qs.filter(id__in=[int(i) for i in finding_ids if i])
    else:
        qs = qs.filter(
            Q(title_vi_status=_PENDING)
            | Q(title_vi_status=_FAILED)
            | Q(title_vi="")
        ).exclude(title_vi_status=_SKIPPED)

    selected = list(qs[: max(1, min(int(limit or 25), 40))])
    stats: dict[str, Any] = {
        "selected": len(selected),
        "ok": 0,
        "pending": 0,
        "skipped": 0,
        "failed": 0,
        "providers": {},
    }
    if not selected:
        return stats

    with build_google_translate_client() as google_client:
        for finding in selected:
            try:
                result = translate_last30days_finding(
                    finding,
                    google_client=google_client,
                    allow_fallback=allow_fallback or _finding_is_stuck(finding),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("last30days translate id=%s failed", finding.id)
                stats["failed"] += 1
                stats["providers"]["error"] = stats["providers"].get("error", 0) + 1
                continue
            status = str(result.get("status") or "")
            provider = str(result.get("provider") or "")[:64]
            if status == _OK:
                stats["ok"] += 1
            elif status == _SKIPPED:
                stats["skipped"] += 1
            elif status == _PENDING:
                stats["pending"] += 1
            else:
                stats["failed"] += 1
            key = provider.split(":", 1)[0] or status
            stats["providers"][key] = stats["providers"].get(key, 0) + 1
    return stats


def enqueue_last30days_title_translations(finding_ids: list[int]) -> None:
    ids = [int(i) for i in finding_ids if i]
    if not ids or not last30days_translate_enabled():
        return
    try:
        from apps.integrations.tasks import translate_last30days_titles_task

        translate_last30days_titles_task.delay(ids)
    except Exception:  # noqa: BLE001
        logger.exception(
            "enqueue_last30days_title_translations failed ids=%s", ids[:5]
        )
