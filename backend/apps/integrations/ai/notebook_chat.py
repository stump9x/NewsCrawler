"""Notebook AI chat helpers: light Groq polish + Ollama unload + social fast path.

Used by the SPA after Open Notebook returns a draft that is too short or
rambling. Groq is last-resort polish only (notebook key pool) — never the
default chat path. Ollama unload keeps RAM free after local fallback chats.

Social/chitchat turns (greetings, thanks, identity) skip crawl/grounding and
use a single fast Groq GPT-OSS 20B reply instead of the full notebook cascade.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_MIN_GOOD_CHARS = 80
_MAX_GOOD_CHARS = 4500
_RAMBLE_MARKERS = (
    "tóm lại một lần nữa",
    "như đã đề cập ở trên",
    "in conclusion",
    "to summarize everything",
    "as mentioned above",
)

# Social / chitchat — greetings, lifestyle, small-talk (EN + VI). Not knowledge/source Qs.
_SOCIAL_CHITCHAT_MAX_CHARS = 220
_SOCIAL_CHITCHAT_RE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:xin\s+)?chào(?:\s+(?:bạn|anh|chị|em|mọi\s+người|cả\s+nhà))?[!?.…]*|"
    r"hello[!?.…]*|hi+[!?.…]*|hey[!?.…]*|hola[!?.…]*|yo[!?.…]*|"
    r"alo[!?.…]*|có\s+(?:đó|ai)\s+không\s*[?？!]*|"
    r"bạn\s+là\s+ai\s*[?？!]*|"
    r"who\s+are\s+you\s*[?？!]*|"
    r"bạn\s+tên\s+(?:là\s+)?gì\s*[?？!]*|"
    r"giới\s+thiệu\s+(?:về\s+)?bạn\s*[?？!]*|"
    r"bạn\s+làm\s+được\s+gì\s*[?？!]*|"
    r"what\s+can\s+you\s+do\s*[?？!]*|"
    r"bạn\s+giúp\s+(?:được\s+)?gì\s*[?？!]*|"
    r"cảm\s+ơn(?:\s+(?:bạn|nhiều|nhé|nha|em|anh|chị))?!*[!.…]*|"
    r"thanks?(?:\s+you)?[!?.…]*|thank\s+you[!?.…]*|"
    r"tạm\s+biệt[!?.…]*|bye+[!?.…]*|goodbye[!?.…]*|see\s+ya?!*[!.…]*|"
    r"hẹn\s+gặp\s+lại[!?.…]*|"
    r"bạn\s+(?:khỏe|ổn)\s+không\s*[?？!]*|"
    r"how\s+are\s+you\s*[?？!]*|"
    r"(?:khỏe|ổn)\s+không\s*[?？!]*|"
    r"ok(?:ay)?[!?.…]*|oke[!?.…]*|được[!?.…]*|ừ+m?!*[!.…]*|uhm+[!?.…]*|hmm+[!?.…]*|"
    r"test(?:ing)?[!?.…]*|ping[!?.…]*|"
    r"good\s+morning[!?.…]*|good\s+evening[!?.…]*|chào\s+buổi\s+(?:sáng|chiều|tối)[!?.…]*"
    r")\s*$"
)
# Lifestyle / đời sống small-talk (must stay off the crawl/digest path).
_SOCIAL_LIFESTYLE_RE = re.compile(
    r"(?i)(?:"
    r"h[oô]m\s+nay\s+(?:th[eế]\s+n[aà]o|ra\s+sao|th[eế]\s+n[aà]o\s+r[oồ]i)|"
    r"b[aạ]n\s+th[ií]ch\s+(?:g[iì]|c[aá]i\s+g[iì]|nh[uữ]ng\s+g[iì])|"
    r"k[eể]\s+(?:cho\s+t[oôi]\s+)?(?:một\s+)?chuy[eệ]n\s+vui|"
    r"k[eể]\s+chuy[eệ]n|"
    r"bu[oồ]n\s+(?:qu[aá]|kh[oô]ng)|ch[aá]n\s+qu[aá]|m[eệ]t\s+(?:qu[aá]|kh[oô]ng)|"
    r"th[oờ]i\s+ti[eế]t\s+(?:h[oô]m\s+nay|th[eế]\s+n[aà]o)|"
    r"l[aàm]\s+g[iì]\s+(?:vui|đi)|ăn\s+g[iì]\s+(?:ngon|b[aây]\s+gi[oờ])|"
    r"tư\s+v[aấ]n\s+(?:t[aâm]\s+s[uự]|đ[oờ]i\s+s[oố]ng)|"
    r"how(?:'s|\s+is)\s+(?:your\s+)?day|"
    r"what\s+do\s+you\s+(?:like|enjoy)|"
    r"tell\s+me\s+a\s+(?:joke|funny\s+story|story)|"
    r"good\s+(?:luck|night|vibes)|"
    r"ng[uủ]\s+ngon|ch[uú]c\s+(?:ng[uủ]\s+ngon|một\s+ngày\s+vui)|"
    r"b[aạ]n\s+(?:đang\s+)?(?:làm\s+gì|nghĩ\s+gì)|"
    r"c[oó]\s+khuy[eê]n\s+(?:gì|nh[ư]\s+gì)\s+(?:kh[oô]ng)?"
    r")"
)
# Anything that smells like notebook/source knowledge → full path.
_SOCIAL_KNOWLEDGE_BLOCK_RE = re.compile(
    r"(?i)(?:"
    r"tóm\s*tắt|tom\s*tat|n[ộo]i\s*dung|nguồn|bài\s+viết|bài\s+báo|"
    r"so\s*sánh|phân\s*tích|đối\s*chiếu|trích\s*dẫn|"
    r"summarize|summarise|summary|overview|digest|article|source|"
    r"compare|analy[sz]e|explain|giải\s*thích|"
    r"cho\s+tôi\s+biết\s+về|tell\s+me\s+about|what\s+(?:is|does|about)|"
    r"notebook|crawl|đọc\s+(?:bài|nguồn|link)|"
    r"https?://|www\.|\.com|\.vn|link\b"
    r")"
)


def is_social_chitchat_query(text: str) -> bool:
    """True for greetings / lifestyle small-talk that need no notebook grounding."""
    raw = str(text or "").strip()
    if not raw or "\n" in raw:
        return False
    q = " ".join(raw.split()).strip()
    if not q or len(q) > _SOCIAL_CHITCHAT_MAX_CHARS:
        return False
    if _SOCIAL_KNOWLEDGE_BLOCK_RE.search(q):
        return False
    if _SOCIAL_CHITCHAT_RE.match(q):
        return True
    if _SOCIAL_LIFESTYLE_RE.search(q):
        return True
    # Short casual small-talk without knowledge cues (đời sống).
    if len(q) <= 100 and not re.search(
        r"(?i)\b(?:pentagon|oracle|diu|g-?bam|ndaa|army|navy|drone|missile)\b",
        q,
    ):
        if re.search(
            r"(?i)(?:\?|không|nhỉ|nhé|hả|sao|gì|thế|vậy|đi|vui|buồn|thích)",
            q,
        ) and not re.search(
            r"(?i)(?:bài|nguồn|tóm|phân\s*tích|so\s*sánh|nội\s*dung)",
            q,
        ):
            # Require at least a conversational cue, not a bare keyword.
            if re.search(
                r"(?i)(?:bạn|mình|tôi|yourself|you|hôm\s+nay|cuối\s+tuần|"
                r"thích|kể|chuyện|tâm\s+sự|tư\s+vấn)",
                q,
            ):
                return True
    return False


def reply_social_chitchat(
    *,
    message: str,
    max_tokens: int = 120,
) -> dict[str, Any]:
    """
    Fast ShopAIKey reply with one Groq fallback.

    No crawl, no Open Notebook context, no multi-provider race, no polish.
    """
    from apps.integrations.ai.groq_pool import (
        groq_chat_completion,
        groq_keys_configured,
    )
    from apps.integrations.ai.shopaikey_pool import (
        shopaikey_chat_completion,
        shopaikey_enabled,
    )

    q = " ".join(str(message or "").split()).strip()
    if not q:
        return {"ok": False, "text": "", "error": "message_required", "social": True}
    if not is_social_chitchat_query(q):
        return {
            "ok": False,
            "text": "",
            "error": "not_social_chitchat",
            "social": False,
        }
    if not shopaikey_enabled() and not groq_keys_configured(pool="notebook"):
        return {
            "ok": False,
            "text": "",
            "error": "notebook_chat_provider_not_configured",
            "social": True,
        }

    fast = (
        getattr(settings, "NOTEBOOK_GROQ_CHAT_MODEL", "")
        or getattr(settings, "GROQ_MODEL_FAST", "")
        or "openai/gpt-oss-20b"
    ).strip() or "openai/gpt-oss-20b"

    messages = [
        {
            "role": "system",
            "content": (
                "Bạn là Trợ lý ảo của Notebook AI (NewsCrawler). "
                "Trả lời giao tiếp xã hội / đời sống ngắn gọn, lịch sự, bằng tiếng Việt "
                "(trừ khi người dùng nói tiếng Anh thì đáp cùng ngôn ngữ). "
                "1–3 câu, tự nhiên — không lan man, không bịa nội dung nguồn, "
                "không yêu cầu đọc bài/crawl. Nếu hỏi bạn là ai: nói bạn là trợ lý "
                "Notebook AI giúp hỏi đáp kiến thức dựa trên nguồn trong notebook."
            ),
        },
        {"role": "user", "content": q[:240]},
    ]
    out_tokens = max(40, min(int(max_tokens or 120), 160))
    attempts: list[tuple[str, str]] = []
    if shopaikey_enabled():
        try:
            result = shopaikey_chat_completion(
                messages=messages,
                max_tokens=out_tokens,
                temperature=0.6,
                profile="fast",
                timeout=6.0,
                try_fallback_models=False,
            )
            text = str(result.get("text") or "").strip()
            if text:
                return {
                    "ok": True,
                    "text": text,
                    "social": True,
                    "provider": "shopaikey",
                    "model": result.get("model") or "qwen-flash",
                    "fast": True,
                }
            attempts.append(("shopaikey", "empty_reply"))
        except Exception as exc:  # noqa: BLE001
            attempts.append(("shopaikey", str(exc)[:120]))

    if groq_keys_configured(pool="notebook"):
        try:
            result = groq_chat_completion(
                messages=messages,
                max_tokens=out_tokens,
                temperature=0.6,
                model=fast,
                timeout=4.0,
                max_attempts=1,
                rotate_on_rate_limit=True,
                pool="notebook",
            )
            text = str(result.get("text") or "").strip()
            if text:
                return {
                    "ok": True,
                    "text": text,
                    "social": True,
                    "provider": "groq",
                    "model": result.get("model") or fast,
                    "fast": True,
                }
            attempts.append(("groq", "empty_reply"))
        except Exception as exc:  # noqa: BLE001
            attempts.append(("groq", str(exc)[:120]))

    logger.warning("notebook social chitchat failed: %s", attempts)
    return {
        "ok": False,
        "text": "",
        "error": "; ".join(f"{p}:{e}" for p, e in attempts)[:180],
        "social": True,
        "provider": attempts[-1][0] if attempts else "",
        "model": "",
    }


def answer_quality_issue(text: str) -> str | None:
    """Return a short reason if the answer should be rolled/polished, else None."""
    body = (text or "").strip()
    if not body:
        return "empty"
    # Strip common citation wrappers for length check.
    plain = re.sub(r"\[(?:source|insight):[^\]]+\]", "", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) < _MIN_GOOD_CHARS:
        return "too_short"
    if len(plain) > _MAX_GOOD_CHARS:
        return "too_long"
    lower = plain.casefold()
    if any(m in lower for m in _RAMBLE_MARKERS):
        return "rambling"
    # Many repeated paragraphs ≈ ramble.
    paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    if len(paras) >= 8 and len(plain) > 1800:
        return "rambling"
    return None


def polish_notebook_answer(
    *,
    question: str,
    draft: str,
    max_tokens: int = 700,
) -> dict[str, Any]:
    """
    Vietnamese quality repair via ShopAIKey deep model, then one Groq fallback.

    Only call when ``answer_quality_issue(draft)`` is set — not every message.
    """
    from apps.integrations.ai.groq_pool import (
        groq_chat_completion,
        groq_keys_configured,
    )
    from apps.integrations.ai.shopaikey_pool import (
        shopaikey_chat_completion,
        shopaikey_enabled,
    )

    issue = answer_quality_issue(draft) or "quality"
    if not shopaikey_enabled() and not groq_keys_configured(pool="notebook"):
        return {
            "ok": False,
            "text": draft,
            "polished": False,
            "issue": issue,
            "error": "notebook_chat_provider_not_configured",
        }

    # Prefer fast GPT-OSS 20B for light polish — avoid burning GPT-OSS 120B / OSINT translate quota.
    fast = (
        getattr(settings, "NOTEBOOK_GROQ_CHAT_MODEL", "")
        or getattr(settings, "GROQ_MODEL_FAST", "")
        or "openai/gpt-oss-20b"
    ).strip() or "openai/gpt-oss-20b"
    prompt = (
        "Bạn là trợ lý OSINT trong Notebook. Viết lại câu trả lời tiếng Việt bên dưới cho "
        "rõ, tập trung, dễ hiểu, bám sát nguồn trong bản nháp (không bịa thêm, không lan man). "
        "Giữ trích dẫn [source:…] / [insight:…] nếu có. Độ dài vừa phải (2–5 đoạn ngắn hoặc gạch đầu dòng). "
        f"Lý do chỉnh: {issue}.\n\n"
        f"Câu hỏi:\n{question.strip()[:1200]}\n\n"
        f"Bản nháp:\n{draft.strip()[:6000]}\n"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Trả lời tiếng Việt, đúng trọng tâm nguồn đã chọn/context. "
                "Rõ ràng, tương tác, không lan man ngoài nguồn."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    out_tokens = max(200, min(int(max_tokens or 700), 1200))
    started = time.monotonic()
    last_err = ""
    if shopaikey_enabled():
        try:
            result = shopaikey_chat_completion(
                messages=messages,
                max_tokens=out_tokens,
                temperature=0.2,
                profile="deep",
                timeout=7.0,
                try_fallback_models=False,
            )
            text = str(result.get("text") or "").strip()
            if text and answer_quality_issue(text) not in {"empty", "too_short"}:
                return {
                    "ok": True,
                    "text": text,
                    "polished": True,
                    "issue": issue,
                    "provider": "shopaikey",
                    "model": result.get("model") or "qwen3-next-80b-a3b-instruct",
                }
            last_err = "shopaikey_polish_empty_or_short"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:180]
            logger.warning("notebook ShopAIKey polish failed: %s", exc)

    remaining = 11.0 - (time.monotonic() - started)
    if groq_keys_configured(pool="notebook") and remaining >= 1.5:
        try:
            result = groq_chat_completion(
                messages=messages,
                max_tokens=out_tokens,
                temperature=0.2,
                model=fast,
                timeout=min(4.0, remaining),
                max_attempts=1,
                rotate_on_rate_limit=True,
                pool="notebook",
            )
            text = str(result.get("text") or "").strip()
            if text and answer_quality_issue(text) not in {"empty", "too_short"}:
                return {
                    "ok": True,
                    "text": text,
                    "polished": True,
                    "issue": issue,
                    "provider": "groq",
                    "model": result.get("model") or fast,
                }
            last_err = "groq_polish_empty_or_short"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:180]
            logger.warning("notebook Groq polish failed: %s", exc)

    return {
        "ok": False,
        "text": draft,
        "polished": False,
        "issue": issue,
        "error": last_err or "polish_failed",
    }


def unload_ollama_chat_models() -> dict[str, Any]:
    """Stop common Notebook chat models in Ollama (keeps images on disk)."""
    base = (
        getattr(settings, "OLLAMA_BASE_URL", "") or "http://ollama:11434"
    ).rstrip("/")
    models = [
        str(getattr(settings, "NOTEBOOK_QWEN_CHAT_MODEL", "") or "qwen2.5:3b"),
        str(
            getattr(settings, "NOTEBOOK_QWEN_CHAT_MODEL_FAST", "")
            or "qwen2.5:1.5b"
        ),
        "qwen2.5:3b",
        "qwen2.5:1.5b",
    ]
    # Dedupe while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for mid in models:
        mid = (mid or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        ordered.append(mid)

    stopped: list[str] = []
    errors: list[str] = []
    try:
        with httpx.Client(timeout=8.0) as client:
            for mid in ordered:
                try:
                    # Ollama 0.5+: POST /api/generate keep_alive=0 unloads.
                    resp = client.post(
                        f"{base}/api/generate",
                        json={
                            "model": mid,
                            "prompt": "",
                            "keep_alive": 0,
                            "stream": False,
                        },
                    )
                    if resp.status_code < 500:
                        stopped.append(mid)
                    else:
                        errors.append(f"{mid}:HTTP{resp.status_code}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{mid}:{type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ollama unload failed: %s", exc)
        return {"ok": False, "stopped": stopped, "errors": [str(exc)[:120]]}

    return {
        "ok": True,
        "stopped": stopped,
        "errors": errors[:6],
    }
