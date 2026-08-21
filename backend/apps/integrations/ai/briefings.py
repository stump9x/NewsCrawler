from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.utils import timezone

from apps.core.text import prefer_my_for_united_states
from apps.intel.models import DataLeak, Indicator, Threat
from apps.integrations.ai.clients import generate_briefing_text
from apps.integrations.ai.wigolo_context import (
    format_wigolo_evidence_block,
    gather_briefing_web_context,
    wigolo_briefing_enabled,
)
from apps.integrations.models import AIBriefing

logger = logging.getLogger(__name__)

# Prefer Wire items that match the China / Indo-Pacific defense focus.
_CHINA_WIRE_RE = re.compile(
    r"china|chinese|pla|prc|taiwan|scs|south\s*china|biển\s*đông|"
    r"trung\s*quốc|đài\s*loan|philippine|west\s*philippine|ayungin|"
    r"scarborough|indo[- ]?pacific|coast\s*guard|pla\s*navy|eastern\s*theatre|"
    r"southern\s*theatre|rok|japan\s*self[- ]defense|aukus|quad",
    re.IGNORECASE,
)

_OUTPUT_STYLE = """
YÊU CẦU TRÌNH BÀY (bắt buộc):
- Viết tiếng Việt rõ ràng, CHI TIẾT, cụ thể — báo cáo tình báo hành chính–quân sự; không sáo rỗng.
- CẤM markdown đậm/nghiêng/heading: không **, không * đơn, không #, không tiêu đề kiểu **1. …:**.
- Cho phép liên kết nguồn dạng [tiêu đề](https://...) trong mục NGUỒN.
- Chỉ dùng chữ thường + số 1) 2) 3) hoặc gạch đầu dòng • .
- Tổng hợp ĐA CHIỀU từ Trạm tin tức: nước lớn (Mỹ, Trung Quốc…) và nước khác; quan hệ liên quốc gia;
  sự kiện nổi bật; mảng theo dõi; nguồn URL Wire — chỉ khi có trong bằng chứng.
- Mỗi đoạn sự kiện: 3–5 câu tiếng Việt đầy đủ (ai / cái gì / khi nào / ở đâu / vì sao).
- CẤM câu phân tích mơ hồ / tâm lý không có trong nguồn.
- Không chép metadata kỹ thuật (source=, kev=, cvss=, [medium], [high]).
- Không bịa; thiếu tin thì ghi «Chưa đủ bằng chứng trong cửa sổ».
- Báo cáo phải đủ dài và có cấu trúc rõ; tránh bản tin siêu ngắn.
- Mục NGUỒN bắt buộc: liệt kê • [tiêu đề](url) từ Wire đã dùng — không chỉ ghi tên báo.

CẤU TRÚC ĐẦU RA:
TIÊU ĐỀ
TỔNG QUAN
SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN
QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA
NỔI BẬT THEO MẢNG
THÔNG TIN LIÊN QUAN KHÁC
NHẬN ĐỊNH NGẮN
NGUỒN
""".strip()


def cleanup_briefing_queue(
    *,
    keep_id: int | None = None,
    stuck_minutes: int | None = None,
) -> dict[str, int]:
    """
    Drop failed rows and *stuck* pending jobs (no recent progress).

    Actively updating PENDING jobs are preserved so navigating away / starting
    another feature does not kill an in-flight briefing.
    Ready history is always preserved.
    """
    from django.conf import settings

    minutes = stuck_minutes
    if minutes is None:
        minutes = int(getattr(settings, "AI_BRIEFING_STUCK_MINUTES", 18) or 18)
    minutes = max(1, int(minutes))

    failed_qs = AIBriefing.objects.filter(status=AIBriefing.Status.FAILED)
    failed_n = failed_qs.count()
    failed_qs.delete()

    # Only revoke/delete pending that have gone quiet (stuck), never fresh ones.
    cutoff = timezone.now() - timedelta(minutes=minutes)
    stale_qs = AIBriefing.objects.filter(
        status=AIBriefing.Status.PENDING, updated_at__lt=cutoff
    )
    if keep_id:
        stale_qs = stale_qs.exclude(pk=keep_id)
    stale_meta = list(stale_qs.values_list("id", "raw_response"))
    stale_n = len(stale_meta)
    _revoke_briefing_tasks(stale_meta)
    stale_qs.delete()

    return {
        "failed_deleted": failed_n,
        "pending_deleted": stale_n,
    }


def _revoke_briefing_tasks(rows: list[tuple]) -> None:
    """Best-effort revoke Celery tasks recorded on briefing.raw_response."""
    try:
        from celery.result import AsyncResult
    except Exception:  # noqa: BLE001
        return
    for _bid, raw in rows:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id:
            continue
        try:
            AsyncResult(task_id).revoke(terminate=True)
        except Exception:  # noqa: BLE001
            logger.debug("could not revoke briefing task %s", task_id[:32])


def attach_briefing_task_id(briefing: AIBriefing, task_id: str) -> None:
    raw = dict(briefing.raw_response or {})
    raw["task_id"] = str(task_id)
    briefing.raw_response = raw
    briefing.save(update_fields=["raw_response", "updated_at"])


def set_briefing_progress(
    briefing: AIBriefing | int | None,
    *,
    message: str,
    pct: int,
) -> None:
    """Persist live progress for UI polling (best-effort)."""
    if briefing is None:
        return
    pk = briefing.pk if hasattr(briefing, "pk") else int(briefing)
    try:
        AIBriefing.objects.filter(pk=pk).update(
            progress=(message or "")[:255],
            progress_pct=max(0, min(100, int(pct))),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("briefing progress update failed id=%s: %s", pk, exc)


def checkpoint_briefing_draft(
    briefing: AIBriefing | int | None,
    *,
    focus: str,
    raw_draft: str,
    sources: list | None = None,
    warnings: list | None = None,
    pct: int = 75,
) -> None:
    """Save mid-pipeline draft so SoftTimeLimit can still produce a usable report."""
    if briefing is None or not (raw_draft or "").strip():
        return
    pk = briefing.pk if hasattr(briefing, "pk") else int(briefing)
    try:
        row = AIBriefing.objects.filter(pk=pk).first()
        if not row:
            return
        raw = dict(row.raw_response or {})
        raw["checkpoint"] = {
            "focus": (focus or "")[:500],
            "raw_draft": (raw_draft or "")[:22000],
            "sources": (sources or [])[:40],
            "warnings": (warnings or [])[:20],
        }
        AIBriefing.objects.filter(pk=pk).update(
            raw_response=raw,
            progress="Đã có bản thô — đang rà soát / chờ Groq…",
            progress_pct=max(0, min(100, int(pct))),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("briefing checkpoint failed id=%s: %s", pk, exc)


def finalize_briefing_on_timeout(
    briefing: AIBriefing, *, reason: str = "timeout"
) -> AIBriefing:
    """Prefer checkpoint draft + fast Groq; fall back to local template."""
    from apps.integrations.ai.briefing_pipeline import polish_dossier_with_groq

    raw = dict(briefing.raw_response or {})
    ck = raw.get("checkpoint") if isinstance(raw.get("checkpoint"), dict) else {}
    draft = str(ck.get("raw_draft") or "").strip()
    focus = str(ck.get("focus") or briefing.title or "").strip()
    sources = ck.get("sources") if isinstance(ck.get("sources"), list) else []
    warnings = list(ck.get("warnings") or [])
    warnings.append(f"Timeout Celery — xuất từ checkpoint ({reason})")

    if len(draft) >= 200:
        dossier = (
            f"FOCUS: {focus}\nSCOPE: keyword\n\n"
            f"=== WIGOLO DRAFT REPORT (pre-Groq; already filtered) ===\n{draft}"
        )
        try:
            polished = polish_dossier_with_groq(
                focus=focus or briefing.title,
                dossier=dossier,
                kind="keyword",
                scope="keyword",
                sources=sources,
                fast=True,
            )
            return _apply_pipeline_result(
                briefing,
                produced={
                    "title": briefing.title,
                    "focus": focus,
                    "content": polished.get("text") or draft,
                    "provider": f"{polished.get('provider') or 'local'}+timeout"[:32],
                    "threat_count": briefing.threat_count,
                    "warnings": warnings,
                    "meta": {
                        "warnings": warnings,
                        "polish_ok": polished.get("ok"),
                        "report_kind": "timeout_checkpoint",
                        "sources": sources,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("timeout polish failed id=%s: %s", briefing.pk, exc)
            briefing.content = normalize_briefing_prose(
                f"TIÊU ĐỀ\n{briefing.title}\n\nTỔNG QUAN\n"
                f"Báo cáo xuất từ bản thô (hết thời gian Celery).\n\n{draft}"
            )
            briefing.provider = "wigolo"
            briefing.status = AIBriefing.Status.READY
            briefing.progress = "Hoàn thành (timeout — bản thô)"
            briefing.progress_pct = 100
            briefing.error_message = ""
            raw["warnings"] = warnings[:20]
            raw["timeout_reason"] = reason[:200]
            briefing.raw_response = raw
            briefing.save(
                update_fields=[
                    "content",
                    "provider",
                    "status",
                    "progress",
                    "progress_pct",
                    "error_message",
                    "raw_response",
                    "updated_at",
                ]
            )
            return briefing

    return _finalize_local(briefing, reason=reason)


def _apply_pipeline_result(
    briefing: AIBriefing,
    *,
    produced: dict,
    extra_raw: dict | None = None,
) -> AIBriefing:
    """Persist pipeline output — always READY with cleaned prose."""
    from apps.integrations.ai.clients import is_local_llm_unavailable_text

    briefing.title = str(produced.get("title") or briefing.title)[:512]
    content = normalize_briefing_prose(str(produced.get("content") or ""))
    provider = str(produced.get("provider") or "local")[:32]
    # Never persist the "LLM tạm không khả dụng" banner when we already have
    # crawl evidence — replace with a short operational note.
    if is_local_llm_unavailable_text(content):
        ck = (briefing.raw_response or {}).get("checkpoint") or {}
        draft = str(ck.get("raw_draft") or "").strip()
        if len(draft) >= 200:
            content = normalize_briefing_prose(
                f"TIÊU ĐỀ\n{briefing.title}\n\n"
                f"TỔNG QUAN\nLLM chỉnh văn tạm chưa sẵn sàng — xuất bản thô đã thu thập.\n\n"
                f"{draft}"
            )
            provider = "wigolo"
        else:
            content = normalize_briefing_prose(
                f"TIÊU ĐỀ\n{briefing.title}\n\n"
                f"TỔNG QUAN\nChưa đủ nội dung đã crawl để xuất báo cáo. "
                f"Thử lại khi Groq/Ollama sẵn sàng.\n"
            )
            provider = "local"
    briefing.content = content
    briefing.provider = provider
    briefing.status = AIBriefing.Status.READY
    briefing.error_message = ""
    briefing.progress = "Hoàn thành"
    briefing.progress_pct = 100
    if produced.get("threat_count") is not None:
        briefing.threat_count = int(produced["threat_count"])
    meta = produced.get("meta") or {}
    warnings = list(produced.get("warnings") or meta.get("warnings") or [])
    raw: dict = {
        "pipeline": meta,
        "focus": produced.get("focus") or "",
        "sources": meta.get("sources") or [],
        "report_kind": meta.get("report_kind") or "detailed",
        "warnings": warnings[:20],
    }
    if extra_raw:
        raw.update(extra_raw)
    briefing.raw_response = {**(briefing.raw_response or {}), **raw}
    if warnings:
        briefing.progress = f"Hoàn thành — {len(warnings)} cảnh báo"
    briefing.save(
        update_fields=[
            "title",
            "content",
            "provider",
            "status",
            "error_message",
            "threat_count",
            "progress",
            "progress_pct",
            "raw_response",
            "updated_at",
        ]
    )
    return briefing


def _apply_briefing_result(
    briefing: AIBriefing,
    *,
    prompt: str,
    web_ctx: dict,
    extra_raw: dict | None = None,
) -> AIBriefing:
    """Legacy path kept for tests that patch generate_briefing_text directly."""
    try:
        result = generate_briefing_text(prompt)
    except Exception as exc:  # noqa: BLE001 — last-resort local digest
        from apps.integrations.ai.clients import _local_briefing

        logger.warning("briefing synthesize crashed id=%s: %s", briefing.pk, exc)
        result = {
            "provider": "local",
            "text": _local_briefing(prompt),
            "raw": {"mode": "exception_fallback", "error": str(exc)[:300]},
        }
    provider = str(result.get("provider") or "local")
    if web_ctx.get("evidence") or web_ctx.get("research"):
        provider = f"{provider}+wigolo"
    briefing.provider = provider[:32]
    briefing.content = normalize_briefing_prose(str(result.get("text") or ""))
    briefing.status = AIBriefing.Status.READY
    briefing.error_message = ""
    raw: dict = {
        "provider_meta": result.get("raw", {}),
        "prompt_chars": len(prompt),
        "wigolo": {
            "enabled": bool(web_ctx.get("enabled")),
            "mode": web_ctx.get("mode"),
            "evidence_count": len(web_ctx.get("evidence") or []),
            "evidence": (web_ctx.get("evidence") or [])[:12],
            "research_ok": bool((web_ctx.get("research") or {}).get("ok")),
        },
    }
    if extra_raw:
        raw.update(extra_raw)
    briefing.raw_response = raw
    briefing.save(
        update_fields=[
            "provider",
            "content",
            "status",
            "error_message",
            "raw_response",
            "updated_at",
        ]
    )
    return briefing


def _finalize_local(briefing: AIBriefing, *, reason: str) -> AIBriefing:
    """Guaranteed READY digest when worker times out mid-flight."""
    from apps.integrations.ai.clients import _local_briefing

    prompt = (
        f"# Interrupted briefing\n- title: {briefing.title}\n"
        f"- threats: {briefing.threat_count}\n- reason: {reason}\n"
    )
    briefing.provider = AIBriefing.Provider.LOCAL
    briefing.content = normalize_briefing_prose(_local_briefing(prompt))
    briefing.status = AIBriefing.Status.READY
    briefing.error_message = ""
    briefing.progress = "Hoàn thành (fallback local)"
    briefing.progress_pct = 100
    briefing.raw_response = {
        **(briefing.raw_response or {}),
        "provider_meta": {"mode": "timeout_local", "reason": reason[:200]},
    }
    briefing.save(update_fields=[
        "provider",
        "content",
        "status",
        "error_message",
        "progress",
        "progress_pct",
        "raw_response",
        "updated_at",
    ])
    return briefing


def collect_intel_snapshot(window_hours: int = 24) -> dict:
    since = timezone.now() - timedelta(hours=window_hours)
    threats_raw = list(
        Threat.objects.filter(published_at__gte=since).order_by(
            "-severity", "-published_at"
        )[:80]
    )
    threats = _rank_wire_threats(threats_raw, limit=80)
    indicators = list(
        Indicator.objects.filter(last_seen__gte=since, is_active=True).order_by("-last_seen")[:40]
    )
    leaks = list(
        DataLeak.objects.filter(discovered_at__gte=since).order_by("-severity", "-discovered_at")[
            :20
        ]
    )
    return {
        "since": since.isoformat(),
        "threats": threats,
        "indicators": indicators,
        "leaks": leaks,
    }


def _rank_wire_threats(threats: list, *, limit: int = 36) -> list:
    """Put China / Indo-Pacific defense Wire items first (Dòng tin focus)."""
    primary: list = []
    secondary: list = []
    for t in threats:
        blob = " ".join(
            [
                str(getattr(t, "title_vi", "") or ""),
                str(getattr(t, "title", "") or ""),
                str(getattr(t, "summary", "") or "")[:400],
            ]
        )
        if _CHINA_WIRE_RE.search(blob):
            primary.append(t)
        else:
            secondary.append(t)
    return (primary + secondary)[: max(1, limit)]


def _format_wire_item(t, *, index: int | None = None) -> str:
    """One Dòng tin row for the LLM — VI title first, short summary, no tech junk."""
    title_vi = " ".join((getattr(t, "title_vi", "") or "").split()).strip()
    title_en = " ".join((getattr(t, "title", "") or "").split()).strip()
    display = title_vi or title_en or "(không tiêu đề)"
    summary = " ".join((getattr(t, "summary", "") or "").split()).strip()[:320]
    published = ""
    if getattr(t, "published_at", None):
        try:
            published = t.published_at.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            published = str(t.published_at)[:10]
    severity = str(getattr(t, "severity", "") or "").strip()
    source = str(getattr(t, "source", "") or "").strip()
    prefix = f"{index}. " if index is not None else "• "
    lines = [f"{prefix}{display}"]
    if title_vi and title_en and title_vi.casefold() != title_en.casefold():
        lines.append(f"   Gốc EN: {title_en[:180]}")
    meta_bits = [b for b in (severity and f"mức={severity}", published and f"ngày={published}", source and f"kênh={source}") if b]
    if meta_bits:
        lines.append(f"   ({'; '.join(meta_bits)})")
    if summary:
        lines.append(f"   Tóm tắt Wire: {summary}")
    return "\n".join(lines)


def normalize_briefing_prose(text: str) -> str:
    """Clean Wire metadata noise; keep paired **bold** / *italic* for SPA render.

    Do not leave unpaired ``**`` as visible garbage. Paired emphasis is preserved
    so the frontend can render real bold/italic (not stripped plain text).
    Markdown source links ``[title](https://...)`` are preserved.
    """
    raw = prefer_my_for_united_states(
        (text or "").replace("\r\n", "\n")
    ).strip()
    if not raw:
        return ""

    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00MD{len(protected) - 1}\x00"

    # Protect markdown links and paired emphasis before stripping leftover markers.
    raw = re.sub(r"\[[^\]]+\]\(https?://[^)\s]+\)", _protect, raw, flags=re.I)
    for _ in range(4):
        prev = raw
        raw = re.sub(r"\*\*([^*]+)\*\*", _protect, raw)
        raw = re.sub(r"__([^_]+)__", _protect, raw)
        raw = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", _protect, raw)
        raw = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", _protect, raw)
        if raw == prev:
            break

    # Leftover unpaired markers (e.g. trailing **)
    raw = raw.replace("**", "")
    raw = re.sub(r"__+", "", raw)
    # Headings / bullets
    raw = re.sub(r"^#{1,6}\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^\s*[\*\-]\s+", "• ", raw, flags=re.MULTILINE)
    # Lone emphasis * left on a line (not mid-word)
    raw = re.sub(r"(?<!\w)\*(?!\w)", "", raw)
    # Tech leftovers models often echo from the evidence list
    raw = re.sub(r"\(source=[^)]*\)", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\bkev\s*=\s*(True|False)\b", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\bcvss\s*=\s*\S+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(
        r"\[(critical|high|medium|low|info)\]\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    raw = re.sub(r"[ \t]{2,}", " ", raw)
    raw = re.sub(r" *\n", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)

    for i, chunk in enumerate(protected):
        raw = raw.replace(f"\x00MD{i}\x00", chunk)
    return raw.strip()


def _append_wigolo_to_prompt(prompt: str, web_ctx: dict) -> str:
    parts = [prompt]
    evidence = web_ctx.get("evidence") if isinstance(web_ctx, dict) else None
    block = format_wigolo_evidence_block(evidence or [])
    if block:
        parts.append(block)
    research = web_ctx.get("research") if isinstance(web_ctx, dict) else None
    if isinstance(research, dict) and research.get("markdown"):
        parts.append(
            "\nOpen-web research (chỉ để đối chiếu; ưu tiên Dòng tin):\n"
            + normalize_briefing_prose(str(research["markdown"])[:4000])
        )
    return "\n".join(parts)


def _should_enrich_daily(threat_count: int) -> bool:
    """Use Wigolo open-web when enabled (cheap corroboration / fill thin windows)."""
    return wigolo_briefing_enabled()


def build_briefing_prompt(snapshot: dict, *, web_ctx: dict | None = None) -> str:
    lines = [
        "Nhiệm vụ: viết BÁO CÁO XU HƯỚNG ĐA CHIỀU từ Dòng tin (Trạm tin tức) bên dưới.",
        "Giọng văn: hành chính–quân sự — dài, cụ thể, tiếng Việt tự nhiên; không bịa.",
        f"Cửa sổ báo cáo từ: {snapshot['since']}",
        "",
        "BẰNG CHỨNG TRẠM TIN TỨC (PRIMARY — chỉ khẳng định sự kiện có ở đây):",
    ]
    threats = snapshot.get("threats") or []
    if threats:
        for idx, t in enumerate(threats, start=1):
            lines.append(_format_wire_item(t, index=idx))
    else:
        lines.append("• Không có bản tin Dòng tin trong cửa sổ.")

    lines.append("\nChỉ báo (IOC) — chỉ nêu nếu liên quan trực tiếp:")
    indicators = snapshot.get("indicators") or []
    if indicators:
        for i in indicators[:20]:
            lines.append(f"• {i.ioc_type}:{i.value}")
    else:
        lines.append("• Không có")

    lines.append("\nRò rỉ dữ liệu liên quan:")
    leaks = snapshot.get("leaks") or []
    if leaks:
        for leak in leaks[:12]:
            lines.append(
                f"• [{leak.severity}] {leak.title} — {leak.leak_type} / {leak.affected_domain}"
            )
    else:
        lines.append("• Không có")

    lines.append("")
    lines.append(_OUTPUT_STYLE)
    prompt = "\n".join(lines)
    if web_ctx:
        prompt = _append_wigolo_to_prompt(prompt, web_ctx)
    return prompt


def queue_ai_briefing(
    *,
    window_hours: int = 24,
    user=None,
    title: str | None = None,
) -> AIBriefing:
    """Create PENDING row immediately (API returns fast); Celery fills content."""
    from apps.integrations.ai.briefing_pipeline import resolve_focus

    cleaned = cleanup_briefing_queue()
    focus_meta = resolve_focus(kind="daily")
    briefing = AIBriefing.objects.create(
        title=title or focus_meta["title"],
        status=AIBriefing.Status.PENDING,
        window_hours=window_hours,
        threat_count=0,
        indicator_count=0,
        leak_count=0,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        content="",
        provider=AIBriefing.Provider.GROQ,
        progress="Đã xếp hàng — chờ xử lý",
        progress_pct=5,
        raw_response={"queued": True, "kind": "daily", "cleaned": cleaned},
    )
    return briefing


def fill_ai_briefing(briefing: AIBriefing) -> AIBriefing:
    """Heavy work: Wire + Wigolo dossier → Groq final style review."""
    if not AIBriefing.objects.filter(pk=briefing.pk).exists():
        raise AIBriefing.DoesNotExist(f"briefing {briefing.pk} was purged")
    window_hours = int(briefing.window_hours or 24)
    set_briefing_progress(briefing, message="Bắt đầu thu thập…", pct=8)
    try:
        from apps.integrations.ai.briefing_pipeline import produce_quality_briefing

        produced = produce_quality_briefing(
            kind="daily",
            window_hours=window_hours,
            briefing_id=briefing.pk,
            on_progress=lambda msg, pct: set_briefing_progress(
                briefing, message=msg, pct=pct
            ),
        )
        return _apply_pipeline_result(briefing, produced=produced)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fill_ai_briefing failed id=%s", briefing.pk)
        return _finalize_local(briefing, reason=str(exc))


def create_ai_briefing(
    *,
    window_hours: int = 24,
    user=None,
    title: str | None = None,
) -> AIBriefing:
    """Synchronous path (tests / beat). Prefer queue_ai_briefing + Celery for HTTP."""
    briefing = queue_ai_briefing(window_hours=window_hours, user=user, title=title)
    return fill_ai_briefing(briefing)


def queue_keyword_summary(
    *, keyword: str, window_hours: int = 168, user=None
) -> AIBriefing:
    keyword = (keyword or "").strip()
    cleaned = cleanup_briefing_queue()
    return AIBriefing.objects.create(
        title=f"Báo cáo: {keyword}"[:512],
        status=AIBriefing.Status.PENDING,
        window_hours=window_hours,
        threat_count=0,
        indicator_count=0,
        leak_count=0,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        content="",
        provider=AIBriefing.Provider.GROQ,
        progress="Đã xếp hàng — chờ xử lý",
        progress_pct=5,
        raw_response={
            "queued": True,
            "kind": "keyword",
            "keyword": keyword,
            "cleaned": cleaned,
        },
    )


def fill_keyword_summary(briefing: AIBriefing) -> AIBriefing:
    if not AIBriefing.objects.filter(pk=briefing.pk).exists():
        raise AIBriefing.DoesNotExist(f"briefing {briefing.pk} was purged")
    keyword = str((briefing.raw_response or {}).get("keyword") or "").strip()
    if not keyword:
        title = briefing.title or ""
        for prefix in ("Báo cáo:", "Tóm tắt:", "Keyword summary:"):
            if title.lower().startswith(prefix.lower()):
                keyword = title.split(":", 1)[-1].strip()
                break
    window_hours = int(briefing.window_hours or 168)
    set_briefing_progress(briefing, message="Bắt đầu thu thập…", pct=8)
    try:
        from apps.integrations.ai.briefing_pipeline import produce_quality_briefing

        produced = produce_quality_briefing(
            kind="keyword",
            keyword=keyword,
            window_hours=window_hours,
            briefing_id=briefing.pk,
            on_progress=lambda msg, pct: set_briefing_progress(
                briefing, message=msg, pct=pct
            ),
        )
        extra = {"keyword": keyword}
        intent = (produced.get("meta") or {}).get("keyword_intent")
        if intent:
            extra["keyword_intent"] = intent
            if intent.get("topic"):
                extra["keyword"] = str(intent["topic"])
        return _apply_pipeline_result(
            briefing,
            produced=produced,
            extra_raw=extra,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("fill_keyword_summary failed id=%s", briefing.pk)
        return _finalize_local(briefing, reason=str(exc))


def create_keyword_summary(*, keyword: str, window_hours: int = 168, user=None) -> AIBriefing:
    briefing = queue_keyword_summary(
        keyword=keyword, window_hours=window_hours, user=user
    )
    return fill_keyword_summary(briefing)


def queue_weekly_trending_digest(*, user=None) -> AIBriefing:
    from apps.integrations.ai.briefing_pipeline import resolve_focus

    cleaned = cleanup_briefing_queue()
    focus_meta = resolve_focus(kind="weekly")
    return AIBriefing.objects.create(
        title=focus_meta["title"],
        status=AIBriefing.Status.PENDING,
        window_hours=24 * 7,
        threat_count=0,
        indicator_count=0,
        leak_count=0,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        content="",
        provider=AIBriefing.Provider.GROQ,
        progress="Đã xếp hàng — chờ xử lý",
        progress_pct=5,
        raw_response={"queued": True, "kind": "weekly", "cleaned": cleaned},
    )


def fill_weekly_trending_digest(briefing: AIBriefing) -> AIBriefing:
    if not AIBriefing.objects.filter(pk=briefing.pk).exists():
        raise AIBriefing.DoesNotExist(f"briefing {briefing.pk} was purged")
    set_briefing_progress(briefing, message="Bắt đầu thu thập…", pct=8)
    try:
        from apps.integrations.ai.briefing_pipeline import produce_quality_briefing

        window_hours = int(briefing.window_hours or (24 * 7))
        produced = produce_quality_briefing(
            kind="weekly",
            window_hours=window_hours,
            briefing_id=briefing.pk,
            on_progress=lambda msg, pct: set_briefing_progress(
                briefing, message=msg, pct=pct
            ),
        )
        return _apply_pipeline_result(briefing, produced=produced)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fill_weekly_trending_digest failed id=%s", briefing.pk)
        return _finalize_local(briefing, reason=str(exc))


def create_weekly_trending_digest(*, user=None) -> AIBriefing:
    briefing = queue_weekly_trending_digest(user=user)
    return fill_weekly_trending_digest(briefing)
