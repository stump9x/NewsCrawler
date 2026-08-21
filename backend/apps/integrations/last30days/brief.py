"""Synthesize a multi-dimensional Vietnamese research brief from last30days findings.

Grounded in collected findings only — does not invent facts. Style matches the
project's administrative–military briefing register.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

from apps.integrations.models import Last30DaysFinding, Last30DaysResearch

logger = logging.getLogger(__name__)

# Section headers expected in the finished report (Vietnamese mil/admin tone).
BRIEF_SECTION_HEADERS = (
    "TIÊU ĐỀ",
    "TỔNG QUAN",
    "SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN",
    "QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA",
    "NỔI BẬT THEO MẢNG",
    "NHẬN ĐỊNH NGẮN",
    "NGUỒN",
)


def brief_enabled() -> bool:
    return bool(getattr(settings, "LAST30DAYS_BRIEF_ENABLED", True))


def _cap_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _max_findings() -> int:
    # Default raised so the synthesizer sees a real corpus, not a thin sample.
    return _cap_int("LAST30DAYS_BRIEF_MAX_FINDINGS", 80, lo=10, hi=200)


def _snippet_chars() -> int:
    return _cap_int("LAST30DAYS_BRIEF_SNIPPET_CHARS", 700, lo=120, hi=2000)


def _evidence_chars() -> int:
    return _cap_int("LAST30DAYS_BRIEF_EVIDENCE_CHARS", 36000, lo=4000, hi=80000)


def _max_tokens() -> int:
    return _cap_int("LAST30DAYS_BRIEF_MAX_TOKENS", 4500, lo=1200, hi=8000)


def _min_brief_chars() -> int:
    return _cap_int("LAST30DAYS_BRIEF_MIN_CHARS", 900, lo=200, hi=5000)


def _finding_title(finding: Last30DaysFinding) -> str:
    vi = (getattr(finding, "title_vi", None) or "").strip()
    if vi and getattr(finding, "title_vi_status", "") == "ok":
        return vi
    if vi:
        return vi
    return (finding.title or "").strip() or "(untitled)"


def _finding_snippet(finding: Last30DaysFinding) -> str:
    vi = (getattr(finding, "snippet_vi", None) or "").strip()
    raw = (finding.snippet or "").strip()
    text = vi or raw
    return " ".join(text.split())[: _snippet_chars()]


def _lookback_phrase(research: Last30DaysResearch) -> str:
    days = max(1, int(research.lookback_days or 30))
    if days == 1:
        return "đúng 24 giờ gần đây"
    return f"{days} ngày gần đây"


def collect_brief_findings(research: Last30DaysResearch) -> list[Last30DaysFinding]:
    """Findings inside lookback, ranked for synthesis (score then recency)."""
    from apps.integrations.last30days.service import findings_within_lookback_q

    qs = (
        research.findings.filter(findings_within_lookback_q(research))
        .order_by("-score", "-published_at", "-id")
    )
    return list(qs[: _max_findings()])


def build_findings_evidence_pack(
    research: Last30DaysResearch,
    *,
    findings: list[Last30DaysFinding] | None = None,
) -> str:
    """Assemble a dense evidence block for the LLM (titles, snippets, URLs)."""
    rows = findings if findings is not None else collect_brief_findings(research)
    lines: list[str] = [
        f"CHỦ ĐỀ: {research.topic}",
        f"CỬA SỔ: {_lookback_phrase(research)}",
        f"SỐ MỤC ĐƯA VÀO TỔNG HỢP: {len(rows)}",
        "",
        "=== BẰNG CHỨNG ĐÃ THU THẬP (chỉ dùng các mục dưới; không bịa thêm) ===",
    ]
    for idx, finding in enumerate(rows, start=1):
        title = _finding_title(finding)
        snippet = _finding_snippet(finding)
        url = (finding.url or "").strip()
        source = (finding.source or "web").strip()
        pub = ""
        if finding.published_at:
            try:
                pub = finding.published_at.strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                pub = str(finding.published_at)[:10]
        lines.append(f"{idx}) [{source}] {title}")
        if pub:
            lines.append(f"   ngày: {pub}")
        if snippet:
            lines.append(f"   nội dung: {snippet}")
        if url:
            lines.append(f"   url: {url}")
    pack = "\n".join(lines).strip()
    return pack[: _evidence_chars()]


def build_research_brief_prompt(
    *,
    topic: str,
    evidence: str,
    lookback_days: int = 30,
    finding_count: int = 0,
) -> str:
    """Prompt for a fuller multi-dimensional Vietnamese trend brief."""
    topic = " ".join((topic or "").split()).strip() or "Xu hướng"
    days = max(1, min(int(lookback_days or 30), 90))
    n = max(0, int(finding_count or 0))
    sections = "\n".join(BRIEF_SECTION_HEADERS)
    window = "đúng 24 giờ gần đây" if days == 1 else f"{days} ngày gần đây"
    return f"""
Nhiệm vụ: Viết BÁO CÁO XU HƯỚNG / NGHIÊN CỨU CHỦ ĐỀ bằng tiếng Việt, văn phong hành chính–quân sự,
DÀI VÀ ĐẶC (nhiều đoạn / đủ mục), tổng hợp đa chiều từ NHIỀU mục bằng chứng — không phải 3–5 câu mỏng
hay danh sách tiêu đề.

CHỦ ĐỀ: {topic}
CỬA SỔ: {window}
SỐ MỤC BẰNG CHỨNG: {n}

CẤU TRÚC BẮT BUỘC (đúng thứ tự, mỗi mục là tiêu đề riêng trên một dòng):
{sections}

YÊU CẦU NỘI DUNG:
1) TỔNG QUAN — 1–2 đoạn (khoảng 6–12 câu) nêu bức tranh tổng thể trong cửa sổ, các dòng sự kiện
   chính và mối liên hệ giữa chúng; không liệt kê headline suông.
2) SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN — nêu các nước/thực thể lớn xuất hiện trong bằng chứng
   (Mỹ, Trung Quốc, và các nước khác khi có: Nga, Nhật, Ấn, ASEAN, EU…). Mỗi nước/thực thể:
   1 đoạn hoặc vài câu đầy đủ (ai / cái gì / khi nào / ở đâu / vì sao) bám bằng chứng.
3) QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA — nối các câu chuyện: cạnh tranh Mỹ–Trung, liên minh,
   diễn tập, đàm phán, trừng phạt, chuỗi cung ứng… Chỉ nêu liên kết có căn cứ trong bằng chứng;
   chỉ rõ mối quan hệ who–what–where giữa các tin.
4) NỔI BẬT THEO MẢNG — nhóm theo lĩnh vực có trong bằng chứng (quốc phòng / ngoại giao /
   kinh tế–thương mại / an ninh / điểm nóng khu vực / công nghệ–không gian–mạng…). Mỗi mảng
   có nội dung đủ dài; bỏ mảng không có dữ liệu.
5) NHẬN ĐỊNH NGẮN — 1 đoạn (4–8 câu) chỉ nối các sự kiện đã nêu; không suy đoán tâm lý,
   không dự báo không có căn cứ, không câu sáo.
6) NGUỒN — liệt kê các bài đã dùng, mỗi dòng • [tiêu đề](https://url-thật)
   từ bằng chứng; CẤM bịa domain / URL.

QUY TẮC CỨNG:
- CHỈ viết điều có trong BẰNG CHỨNG bên dưới. Không bịa sự kiện, số liệu, tuyên bố, hay quan hệ.
- Tổng hợp xuyên suốt nhiều mục; nêu liên kết giữa tin khi bằng chứng cho phép.
- Mỗi đoạn sự kiện: câu đầy đủ tiếng Việt (ai/cái gì/khi nào/ở đâu/vì sao) — tránh one-liner.
- Độ dài mục tiêu: báo cáo đủ dày (thường ≥ 900–2000+ chữ), nhiều đoạn rõ ràng — không tóm tắt siêu ngắn.
- CẤM markdown đậm/nghiêng/heading: không **, không * đơn, không #. Dùng 1) 2) 3) và • khi cần liệt kê.
- Cho phép liên kết nguồn dạng [tiêu đề](https://...) trong mục NGUỒN.
- Giữ URL https:// trên dòng riêng sau sự kiện khi trích dẫn cụ thể.
- Nếu bằng chứng mỏng về một mục cấu trúc: ghi ngắn «Chưa đủ bằng chứng trong cửa sổ» rồi chuyển mục khác;
  không bịa để lấp chỗ trống.

BẰNG CHỨNG:
{evidence}
""".strip()


def _local_brief_from_findings(
    research: Last30DaysResearch,
    findings: list[Last30DaysFinding],
) -> str:
    """Structured fallback when cloud LLM is unavailable."""
    topic = research.topic or "Xu hướng"
    days = research.lookback_days or 30
    by_source: dict[str, int] = {}
    for f in findings:
        src = (f.source or "web").strip() or "web"
        by_source[src] = by_source.get(src, 0) + 1
    source_bits = ", ".join(f"{k}: {v}" for k, v in sorted(by_source.items()))

    lines = [
        "TIÊU ĐỀ",
        f"Báo cáo xu hướng: {topic} ({days} ngày)",
        "",
        "TỔNG QUAN",
        (
            f"Trong cửa sổ {days} ngày, hệ thống thu thập {len(findings)} mục liên quan "
            f"chủ đề «{topic}» (phân bổ nguồn: {source_bits or 'không rõ'}). "
            "Dưới đây là các điểm nổi bật đã ghi nhận từ bằng chứng; bản LLM đầy đủ "
            "tạm không khả dụng nên đây là bản cấu trúc từ tiêu đề/đoạn trích."
        ),
        "",
        "SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN",
        "• Chưa phân nhóm quốc gia tự động — xem các mục bằng chứng bên dưới.",
        "",
        "QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA",
        "• Chưa đủ tổng hợp liên kết liên quốc gia (chế độ local).",
        "",
        "NỔI BẬT THEO MẢNG",
    ]
    for idx, finding in enumerate(findings[:25], start=1):
        title = _finding_title(finding)
        snippet = _finding_snippet(finding)
        url = (finding.url or "").strip()
        src = (finding.source or "").strip()
        lines.append(f"{idx}) [{src}] {title}")
        if snippet:
            lines.append(f"   • {snippet[:400]}")
        if url:
            lines.append(f"   {url}")
    lines.extend(
        [
            "",
            "NHẬN ĐỊNH NGẮN",
            "• Bản này chưa qua LLM tổng hợp đa chiều — cần chạy lại khi nhà cung cấp AI sẵn sàng.",
            "",
            "NGUỒN",
        ]
    )
    seen: set[str] = set()
    for finding in findings:
        url = (finding.url or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"[\r\n\[\]]+", " ", _finding_title(finding)).strip() or "Nguồn"
        title = " ".join(title.split())[:200]
        lines.append(f"• [{title}]({url})")
        if len(seen) >= 40:
            break
    if not seen:
        lines.append("(không có URL)")
    return "\n".join(lines).strip()


def _ensure_findings_nguon(text: str, findings: list[Last30DaysFinding]) -> str:
    """Append/replace NGUỒN with real finding URLs as markdown links."""
    body = (text or "").rstrip()
    lines: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        url = (finding.url or "").strip()
        if not url.startswith("http") or url.casefold() in seen:
            continue
        seen.add(url.casefold())
        title = re.sub(r"[\r\n\[\]]+", " ", _finding_title(finding)).strip() or "Nguồn"
        title = " ".join(title.split())[:200]
        lines.append(f"• [{title}]({url})")
        if len(lines) >= 40:
            break
    if not lines:
        return body
    # Replace weak/empty NGUỒN
    m = re.search(r"(?ims)^NGUỒN\b(.*)\Z", body)
    if m:
        section = m.group(1) or ""
        urls = re.findall(r"https?://", section, flags=re.I)
        md_links = re.findall(r"\[[^\]]+\]\(https?://", section, flags=re.I)
        if len(urls) >= 2 and len(md_links) >= 2:
            return body
        body = body[: m.start()].rstrip()
    return "\n".join([body, "", "NGUỒN", *lines]).strip()


def synthesize_research_brief(
    research: Last30DaysResearch,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Build brief_markdown from collected findings (LLM preferred, local fallback).

    Returns {ok, chars, provider, finding_count, error?}.
    """
    if not force and not brief_enabled():
        return {"ok": False, "error": "disabled", "chars": 0, "finding_count": 0}

    findings = collect_brief_findings(research)
    if not findings:
        return {"ok": False, "error": "no_findings", "chars": 0, "finding_count": 0}

    evidence = build_findings_evidence_pack(research, findings=findings)
    prompt = build_research_brief_prompt(
        topic=research.topic,
        evidence=evidence,
        lookback_days=int(research.lookback_days or 30),
        finding_count=len(findings),
    )

    text = ""
    provider = "local"
    error = ""
    try:
        from apps.integrations.ai.briefings import normalize_briefing_prose
        from apps.integrations.ai.clients import (
            AIProviderError,
            generate_briefing_text,
            is_local_llm_unavailable_text,
        )

        result = generate_briefing_text(
            prompt,
            max_tokens=_max_tokens(),
            allow_wigolo_fallback=False,
            prefer_fast_model=False,
            prefer_long_context=True,
            allow_local_fallback=False,
            retry_rounds=int(
                getattr(settings, "AI_BRIEFING_LLM_RETRY_ROUNDS", 3) or 3
            ),
        )
        provider = str(result.get("provider") or "")
        text = normalize_briefing_prose(str(result.get("text") or ""))
        if provider == "local" or is_local_llm_unavailable_text(text):
            raise AIProviderError("brief returned local LLM-unavailable stub")
        if len(text) < _min_brief_chars():
            raise AIProviderError(
                f"brief too short ({len(text)} < {_min_brief_chars()})"
            )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:220]
        logger.warning(
            "last30days brief LLM failed research=%s: %s — local fallback",
            research.pk,
            error,
        )
        text = _local_brief_from_findings(research, findings)
        provider = "local_fallback"

    text = _ensure_findings_nguon(text, findings)
    text = (text or "").strip()[:50000]
    if not text:
        return {
            "ok": False,
            "error": error or "empty_brief",
            "chars": 0,
            "finding_count": len(findings),
        }

    research.brief_markdown = text
    raw = dict(research.raw_report or {})
    raw["research_brief"] = {
        "ok": True,
        "provider": provider,
        "finding_count": len(findings),
        "evidence_chars": len(evidence),
        "chars": len(text),
        "error": error,
    }
    research.raw_report = raw
    research.save(update_fields=["brief_markdown", "raw_report", "updated_at"])
    return {
        "ok": True,
        "chars": len(text),
        "provider": provider,
        "finding_count": len(findings),
        "error": error,
    }
