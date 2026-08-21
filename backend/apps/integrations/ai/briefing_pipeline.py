"""High-quality briefing pipeline: Wire + Wigolo gather/crawl/draft → long-context polish.

Flow:
1) Select Dòng tin (defense-biased when no keyword) in a strict time window
2) Optional multilingual open-web (keyword only / thin Wire) — never invent from it
3) Fetch full/partial article bodies (Wire first, then web)
4) Extract 3–5 long substantive sentences per article → RAW draft
5) Cerebras/OpenRouter synthesize compact Vietnamese report (~2–3 A4 pages)
6) Groq ALWAYS final-polishes: VN-only clarity and/or condense to 4500–8000 body chars
"""

from __future__ import annotations

import json
import html
import logging
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://", re.I)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")

# Multi-dimensional Vietnamese intel report sections (daily / weekly / keyword).
TREND_SECTION_HEADERS = (
    "TIÊU ĐỀ",
    "TỔNG QUAN",
    "SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN",
    "QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA",
    "NỔI BẬT THEO MẢNG",
    "THÔNG TIN LIÊN QUAN KHÁC",
    "NHẬN ĐỊNH NGẮN",
    "NGUỒN",
)

# Long prose prompts: word/char thresholds that trigger Groq intent refine.
_KEYWORD_INTENT_LONG_WORDS = 12
_KEYWORD_INTENT_LONG_CHARS = 100

_VI_PROMPT_NOISE = (
    "hãy",
    "hay",
    "viết",
    "tạo",
    "làm",
    "cho",
    "tôi",
    "một",
    "báo",
    "cáo",
    "tóm",
    "tắt",
    "tómtắt",
    "phân",
    "tích",
    "về",
    "các",
    "của",
    "và",
    "hoặc",
    "trong",
    "tuần",
    "tháng",
    "ngày",
    "gần",
    "đây",
    "dựa",
    "trên",
    "nguồn",
    "tin",
    "please",
    "write",
    "create",
    "make",
    "report",
    "summary",
    "summarize",
    "about",
    "regarding",
    "based",
    "on",
    "the",
    "a",
    "an",
    "for",
    "me",
    "with",
    "from",
    "using",
    "sources",
)

_DEFENSE_SEARCH_HINT = (
    "military defense strategy cyber warfare naval missile Indo-Pacific"
)

_DEFENSE_WIRE_TERMS = (
    "defense",
    "defence",
    "military",
    "army",
    "navy",
    "air force",
    "missile",
    "submarine",
    "drone",
    "PLA",
    "NATO",
    "cyber",
    "warfare",
    "weapon",
    "nuclear",
    "exercise",
    "drill",
    "quốc phòng",
    "quân sự",
    "hải quân",
    "không quân",
    "tên lửa",
    "tác chiến mạng",
    "chiến lược",
    "chiến lược quân sự",
    "xu hướng tác chiến",
    "diễn tập",
    "doctrine",
    "A2/AD",
    "ISR",
    "munitions",
    "deterrence",
    "force posture",
    "vũ khí",
    "an ninh mạng",
    "Biển Đông",
    "Taiwan",
    "Đài Loan",
)


def _fetch_cap() -> int:
    # A quick briefing is interactive: use the best few bodies and reuse Wire
    # summaries for the rest. Fetching 80-100 URLs made one report take minutes.
    configured = int(getattr(settings, "AI_BRIEFING_FETCH_MAX", 12) or 12)
    return max(6, min(configured, 16))


def _fetch_chars() -> int:
    """Per-article body budget for crawl → digest (cloud long-context friendly)."""
    return max(
        4000,
        min(int(getattr(settings, "AI_BRIEFING_FETCH_CHARS", 14000) or 14000), 20000),
    )


def _digest_sentence_count() -> int:
    """Target 3–5 substantive sentences per article in digests."""
    configured = int(getattr(settings, "AI_BRIEFING_DIGEST_SENTENCES", 5) or 5)
    return max(3, min(configured, 5))


def _dossier_body_chars() -> int:
    """How much article body to keep in the dossier after digests are built."""
    return max(
        2000,
        min(
            int(getattr(settings, "AI_BRIEFING_DOSSIER_BODY_CHARS", 4500) or 4500),
            _fetch_chars(),
        ),
    )


def _search_limit() -> int:
    configured = int(getattr(settings, "AI_BRIEFING_SEARCH_LIMIT", 60) or 60)
    return max(10, min(configured, 200))


def _wire_limit() -> int:
    """Max Wire items selected for a briefing (drives UI "Số bản tin").

    Prefer AI_BRIEFING_MAX_WIRE_ITEMS; fall back to legacy AI_BRIEFING_WIRE_LIMIT.
    Never exceed WIRE_MAX_ITEMS; soft ceiling 500 for pipeline stability.
    """
    preferred = getattr(settings, "AI_BRIEFING_MAX_WIRE_ITEMS", None)
    legacy = getattr(settings, "AI_BRIEFING_WIRE_LIMIT", None)
    configured = preferred if preferred is not None else legacy
    if configured is None:
        configured = 200
    wire_max = int(getattr(settings, "WIRE_MAX_ITEMS", 2000) or 2000)
    return max(10, min(int(configured or 200), wire_max, 500))


def _wigolo_max_sources() -> int:
    configured = int(
        getattr(settings, "WIGOLO_BRIEFING_RESEARCH_MAX_SOURCES", 80) or 80
    )
    return max(8, min(configured, _wire_limit()))


def _source_list_cap() -> int:
    configured = int(getattr(settings, "AI_BRIEFING_REPORT_SOURCE_LIMIT", 0) or 0)
    return _wire_limit() if configured <= 0 else max(10, min(configured, _wire_limit()))


def _model_wire_limit() -> int:
    """Evidence items sent to the LLM; metadata can retain a larger corpus."""
    configured = int(getattr(settings, "AI_BRIEFING_MODEL_WIRE_LIMIT", 0) or 0)
    return _wire_limit() if configured <= 0 else max(10, min(configured, _wire_limit()))


def resolve_briefing_window_hours(kind: str, window_hours: int | None = None) -> int:
    """Strict lookback: daily=24h, weekly=7d; keyword keeps requested (clamped)."""
    kind = (kind or "daily").strip().lower()
    if kind == "daily":
        return 24
    if kind == "weekly":
        return 24 * 7
    try:
        hours = int(window_hours if window_hours is not None else 168)
    except (TypeError, ValueError):
        hours = 168
    return max(24, min(hours, 720))


def _window_label(kind: str, window_hours: int) -> str:
    kind = (kind or "daily").strip().lower()
    if kind == "daily" or window_hours <= 24:
        return "đúng 24 giờ gần đây"
    if kind == "weekly" or window_hours <= 24 * 7:
        return "đúng 7 ngày (168 giờ) gần đây"
    days = max(1, round(window_hours / 24))
    return f"{days} ngày ({window_hours} giờ) gần đây"


def _search_time_range(kind: str, window_hours: int) -> str:
    """Align open-web search window with briefing lookback when web is used."""
    kind = (kind or "daily").strip().lower()
    if kind == "daily" or window_hours <= 36:
        return "day"
    if kind == "weekly" or window_hours <= 200:
        return "week"
    return "month"


def resolve_focus(
    *,
    kind: str,
    keyword: str = "",
    threat_titles: list[str] | None = None,
) -> dict[str, str]:
    """
    Derive the user-facing focus for this briefing.

    Keyword → follow the query. General daily/weekly → defense / military / cyber.
    """
    kw = " ".join((keyword or "").split()).strip()
    kind = (kind or "daily").strip().lower()
    if kw:
        return {
            "focus": kw,
            "title": f"Báo cáo: {kw}"[:512],
            "search_hint": kw,
            "scope": "keyword",
        }
    themes = [t for t in (threat_titles or []) if t][:5]
    theme_hint = "; ".join(themes[:3]) if themes else ""
    if kind == "weekly":
        focus = (
            "Chủ đề nổi bật trong tuần — tổng hợp đa chiều từ Trạm tin tức "
            "(quân sự, quốc phòng, chiến lược, tác chiến mạng, ngoại giao…)"
            + (f" (Dòng tin: {theme_hint})" if theme_hint else "")
        )
        return {
            "focus": focus,
            "title": f"Chủ đề nổi bật trong tuần ({timezone.now().date().isoformat()})",
            "search_hint": theme_hint or _DEFENSE_SEARCH_HINT,
            "scope": "general_defense",
        }
    focus = (
        "Báo cáo xu hướng ngày (24 giờ) — tổng hợp đa chiều từ Trạm tin tức "
        "(quân sự, quốc phòng, chiến lược, tác chiến mạng, ngoại giao…)"
        + (f" (trọng tâm Dòng tin: {themes[0][:80]})" if themes else "")
    )
    return {
        "focus": focus,
        "title": f"Báo cáo xu hướng ngày — 24h ({timezone.now().date().isoformat()})",
        "search_hint": themes[0] if themes else _DEFENSE_SEARCH_HINT,
        "scope": "general_defense",
    }


def _parse_intent_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(raw)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _clean_phrase_list(raw: Any, *, limit: int = 8, max_len: int = 80) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = " ".join(str(item or "").split()).strip(" -•\t,;.")
        if len(text) < 2 or len(text) > max_len:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _keyword_looks_long(prompt: str) -> bool:
    words = [w for w in (prompt or "").split() if w]
    return len(words) >= _KEYWORD_INTENT_LONG_WORDS or len(prompt) >= _KEYWORD_INTENT_LONG_CHARS


def heuristic_keyword_intent(user_prompt: str) -> dict[str, Any]:
    """Deterministic fallback when Groq is unavailable or prompt is already short."""
    raw = " ".join((user_prompt or "").split()).strip()
    if not raw:
        return {
            "topic": "",
            "search_hint": "",
            "match_phrases": [],
            "must_tokens": [],
            "exclude_tokens": [],
            "raw_prompt": "",
            "source": "empty",
        }

    # Prefer the first sentence / clause as the topic core.
    first = re.split(r"[.!?\n;]| — | – ", raw, maxsplit=1)[0].strip() or raw
    tokens = [t.strip(" ,:;\"'()[]") for t in first.split() if t.strip(" ,:;\"'()[]")]
    content = [
        t
        for t in tokens
        if len(t) >= 2 and t.casefold() not in _VI_PROMPT_NOISE
    ]
    topic = " ".join(content[:8]).strip() or first[:120] or raw[:120]
    # Keep multi-word phrases for icontains; drop ultra-generic singles.
    match_phrases: list[str] = []
    if len(topic.split()) >= 2:
        match_phrases.append(topic)
    for t in content:
        if len(t) >= 4 and t.casefold() not in {p.casefold() for p in match_phrases}:
            match_phrases.append(t)
    match_phrases = match_phrases[:6]
    if not match_phrases:
        match_phrases = [topic or raw[:80]]

    must_tokens = [
        t.casefold()
        for t in content
        if t.isascii() and len(t) > 2 and t.casefold() not in _VI_PROMPT_NOISE
    ][:6]

    return {
        "topic": topic[:160],
        "search_hint": topic[:160],
        "match_phrases": match_phrases,
        "must_tokens": must_tokens,
        "exclude_tokens": [],
        "raw_prompt": raw[:800],
        "source": "heuristic",
    }


def refine_keyword_intent(user_prompt: str) -> dict[str, Any]:
    """
    Groq semantic pre-check: turn a long user briefing prompt into a tight intent.

    Uses the briefing key pool. Falls back to heuristics on any failure.
    Shape:
      topic, search_hint, match_phrases, must_tokens, exclude_tokens, raw_prompt, source
    """
    base = heuristic_keyword_intent(user_prompt)
    raw = base["raw_prompt"]
    if not raw:
        return base
    # Short queries are already tight — skip the LLM round-trip.
    if not _keyword_looks_long(raw):
        base["source"] = "short"
        return base
    if not bool(getattr(settings, "AI_BRIEFING_KEYWORD_INTENT", True)):
        return base

    try:
        from apps.integrations.ai.groq_pool import (
            groq_chat_completion,
            groq_keys_configured,
        )
        from apps.integrations.ai.openrouter_pool import (
            openrouter_chat_completion,
            openrouter_enabled,
        )
    except Exception:  # noqa: BLE001
        return base

    model = str(
        getattr(settings, "GROQ_BRIEFING_FALLBACK_MODEL", "llama-3.1-8b-instant")
        or "llama-3.1-8b-instant"
    )
    timeout = float(
        getattr(settings, "GROQ_BRIEFING_TIMEOUT_SEC", None)
        or getattr(settings, "GROQ_TIMEOUT_SEC", 12)
        or 12
    )
    # Intent calls should stay cheap — cap wait/timeout.
    timeout = max(12.0, min(timeout, 45.0))
    prompt = (
        "User wrote a long OSINT briefing request (often Vietnamese). "
        "Extract a TIGHT semantic query. Return ONLY JSON:\n"
        '{\n'
        '  "topic": "short core topic (VI or EN, <=12 words)",\n'
        '  "search_keywords": ["2-6 short search phrases"],\n'
        '  "must_tokens": ["2-6 content tokens true hits should contain"],\n'
        '  "exclude": ["noise concepts to avoid matching"]\n'
        "}\n"
        "Rules: no markdown; do not invent unrelated topics; drop instruction fluff "
        '(write/report/summarize/please/hãy viết báo cáo…); keep named entities '
        "(places, units, systems, orgs).\n\n"
        f"Request:\n{raw[:1200]}"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You extract precise OSINT search intent. "
                "Output valid JSON only."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    result_text = ""
    source = "heuristic"
    if groq_keys_configured(pool="briefing"):
        try:
            result = groq_chat_completion(
                messages=messages,
                max_tokens=280,
                temperature=0.1,
                model=model,
                timeout=timeout,
                block_for_budget=True,
                pool="briefing",
            )
            result_text = str(result.get("text") or "")
            source = "groq"
        except Exception as exc:  # noqa: BLE001
            logger.info("keyword intent groq skipped: %s", exc)

    if not result_text and openrouter_enabled():
        try:
            result = openrouter_chat_completion(
                messages=messages,
                max_tokens=280,
                temperature=0.1,
                timeout=min(
                    timeout,
                    float(getattr(settings, "OPENROUTER_TIMEOUT_SEC", 45) or 45),
                ),
                rotate_on_rate_limit=True,
                try_fallback_models=True,
            )
            result_text = str(result.get("text") or "")
            source = "openrouter"
        except Exception as exc:  # noqa: BLE001
            logger.info("keyword intent openrouter skipped: %s", exc)

    if not result_text:
        return base

    data = _parse_intent_json(result_text)
    if not data:
        logger.info("keyword intent %s: unparseable response", source)
        return base

    topic = " ".join(str(data.get("topic") or "").split()).strip()[:160]
    phrases = _clean_phrase_list(
        data.get("search_keywords") or data.get("match_phrases"),
        limit=6,
        max_len=100,
    )
    must_raw = data.get("must_tokens") if isinstance(data.get("must_tokens"), list) else []
    must_tokens = [
        t.casefold()
        for t in (str(x).strip() for x in must_raw)
        if 2 < len(t) <= 40
    ][:8]
    exclude = _clean_phrase_list(data.get("exclude"), limit=6, max_len=60)

    if not topic and not phrases:
        return base
    if topic and topic.casefold() not in {p.casefold() for p in phrases}:
        phrases = [topic, *phrases][:6]
    if not phrases:
        phrases = base["match_phrases"]

    return {
        "topic": topic or base["topic"],
        "search_hint": (topic or phrases[0] if phrases else base["search_hint"])[:160],
        "match_phrases": phrases or base["match_phrases"],
        "must_tokens": must_tokens or base["must_tokens"],
        "exclude_tokens": [e.casefold() for e in exclude],
        "raw_prompt": raw[:800],
        "source": source,
    }


def optional_groq_plan_queries(focus: str, *, scope: str = "keyword") -> list[str]:
    """Optional tiny Groq call to expand search queries; falls back to heuristics."""
    focus = " ".join((focus or "").split()).strip()
    if not focus:
        return []
    if not bool(getattr(settings, "AI_BRIEFING_GROQ_PLAN", False)):
        return _multilingual_search_queries(focus, scope=scope)
    try:
        from apps.integrations.ai.clients import groq_complete

        result = groq_complete(
            (
                "Given this OSINT focus, reply with ONLY 5 short web search queries "
                "in different languages when useful (EN, VI, ZH, JA, RU) — one per line, "
                f"no numbering, no markdown:\n{focus}"
            ),
            max_tokens=120,
            model=str(
                getattr(settings, "GROQ_BRIEFING_FALLBACK_MODEL", "llama-3.1-8b-instant")
                or "llama-3.1-8b-instant"
            ),
        )
        lines = [
            " ".join(ln.split()).strip(" -•\t")
            for ln in str(result.get("text") or "").splitlines()
            if ln.strip()
        ]
        out = [ln for ln in lines if 3 <= len(ln) <= 120][:5]
        return out or _multilingual_search_queries(focus, scope=scope)
    except Exception as exc:  # noqa: BLE001
        logger.info("briefing groq plan skipped: %s", exc)
        return _multilingual_search_queries(focus, scope=scope)


def _multilingual_search_queries(focus: str, *, scope: str = "keyword") -> list[str]:
    """Fan out EN + related-language queries for ~1-month web search."""
    base = focus[:100]
    if scope == "general_defense":
        queries = [
            "military defense strategy news Indo-Pacific",
            "cyber warfare military operations latest",
            "海军 军事 国防 演习",
            "防衛 軍事 サイバー",
            "оборона военный кибер",
            f"{base} quốc phòng quân sự tác chiến mạng",
        ]
    else:
        queries = [
            base,
            f"{base} latest developments",
            f"{base} military OR defense OR cyber",
            f"{base} 军事 OR 国防 OR 演习",
            f"{base} 防衛 OR 軍事",
            f"{base} оборона OR военный",
            f"{base} quốc phòng OR quân sự OR cyber",
        ]
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out[:8]


def _heuristic_queries(focus: str) -> list[str]:
    return _multilingual_search_queries(focus, scope="keyword")


def _threat_url(threat) -> str:
    url = str(getattr(threat, "source_url", "") or "").strip()
    return url if _URL_RE.match(url) else ""


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        url = (raw or "").strip()
        if not _URL_RE.match(url):
            continue
        key = url.rstrip("/").casefold()
        host = urlparse(url).netloc.casefold()
        if "github.com" in host:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out



def _split_sentences(text: str) -> list[str]:
    raw = " ".join((text or "").split())
    if not raw:
        return []
    parts = re.split(r"(?<=[\.!\?。！？])\s+", raw)
    out: list[str] = []
    for p in parts:
        s = p.strip(" •-\t")
        # Prefer long substantive sentences; allow up to ~520 chars.
        if 55 <= len(s) <= 520:
            out.append(s)
        elif 40 <= len(s) < 55 and len(out) < 2:
            # Keep a short opener only when body is thin.
            out.append(s)
    return out


def _digest_article_sentences(
    body: str,
    *,
    focus: str = "",
    title: str = "",
    n: int | None = None,
) -> list[str]:
    """Extract 3–5 long substantive sentences from article body (no LLM — stable)."""
    target = max(3, min(int(n if n is not None else _digest_sentence_count()), 5))
    sentences = _split_sentences(body)
    if not sentences:
        short = " ".join((body or "").split())[:480]
        return [short] if short else []
    focus_tokens = {
        t.casefold()
        for t in re.findall(r"[\wÀ-ỹ]{3,}", f"{focus} {title}", flags=re.I)
    }
    defense = {t.casefold() for t in _DEFENSE_WIRE_TERMS}

    scored: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences[:120]):
        low = sent.casefold()
        words = set(re.findall(r"[\wÀ-ỹ]{3,}", low))
        overlap = len(words & focus_tokens)
        def_hits = len(words & defense)
        # Prefer longer substantive sentences; lightly boost early ones.
        length_score = min(len(sent), 400) / 400.0
        pos_boost = 1.2 if idx < 4 else (1.08 if idx < 10 else 1.0)
        fact_hint = 0.35 if re.search(
            r"\b(20\d{2}|january|february|march|april|may|june|july|august|"
            r"september|october|november|december|said|announced|launched|"
            r"conducted|deployed|according|reported|nói|công bố|triển khai)\b",
            low,
        ) else 0.0
        score = overlap * 3.0 + def_hits * 1.5 + length_score * 1.4 + pos_boost + fact_hint
        scored.append((score, idx, sent))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked: list[str] = []
    seen: set[str] = set()
    for _, _, sent in scored:
        key = sent[:90].casefold()
        if key in seen:
            continue
        seen.add(key)
        picked.append(sent)
        if len(picked) >= target:
            break
    if len(picked) < 3:
        for sent in sentences:
            if sent not in picked:
                picked.append(sent)
            if len(picked) >= 3:
                break
    return picked[:target]


def _assemble_raw_draft_from_digests(
    *,
    focus: str,
    scope: str,
    wire_digests: list[dict[str, Any]],
    web_digests: list[dict[str, Any]],
    window_label: str = "",
) -> str:
    """Bản thô có cấu trúc từ 3–5 câu dài/bài — cloud/Groq tổng hợp đa chiều."""
    sections = "\n".join(TREND_SECTION_HEADERS)
    win = window_label or "cửa sổ báo cáo"
    lines = [
        "BẢN THÔ (từ NỘI DUNG BÀI ĐÃ ĐỌC / metadata Trạm tin tức — chưa chuẩn hóa văn phong)",
        f"CHỦ ĐỀ: {focus}",
        f"PHẠM VI: {scope}",
        f"CỬA SỔ: {win}",
        "",
        "=== PRIMARY: TRẠM TIN TỨC / DÒNG TIN (chỉ đây là bằng chứng sự kiện chính) ===",
        f"Số mục: {len(wire_digests)}",
    ]
    if wire_digests:
        for i, row in enumerate(wire_digests, start=1):
            lines.append(f"{i}) {row.get('title') or '(không tiêu đề)'}")
            for s in row.get("digest") or []:
                lines.append(f"   • {s}")
            if row.get("url"):
                lines.append(f"   Nguồn: {row['url']}")
    else:
        lines.append("(chưa có mục Dòng tin trong cửa sổ)")

    lines.extend(
        [
            "",
            "=== THÔNG TIN LIÊN QUAN KHÁC (web / đối chiếu — KHÔNG bịa sự kiện chính từ đây) ===",
            f"Số mục: {len(web_digests)}",
        ]
    )
    if web_digests:
        for i, row in enumerate(web_digests, start=1):
            lines.append(f"{i}) {row.get('title') or '(không tiêu đề)'}")
            for s in row.get("digest") or []:
                lines.append(f"   • {s}")
            if row.get("url"):
                lines.append(f"   Nguồn: {row['url']}")
    else:
        lines.append("(không có / không dùng)")

    lines.extend(
        [
            "",
            "GHI CHÚ CHO LLM TỔNG HỢP ĐA CHIỀU",
            f"• Cửa sổ thời gian: {win} — chỉ dùng tin trong cửa sổ.",
            "• CHỈ khẳng định sự kiện có trong mục PRIMARY (Trạm tin tức). Không bịa, không chung chung.",
            "• Ba trục nội dung: (1) từng tin Wire quan trọng; (2) mối liên kết giữa các tin;",
            "  (3) các quốc gia xuất hiện trên Trạm tin tức — kèm quan hệ liên quốc gia / mảng theo dõi.",
            "• TOÀN BỘ tường thuật bằng tiếng Việt hành chính–quân sự; CẤM đoạn/câu tiếng Anh (trừ tên riêng/URL).",
            "• Độ dài thân bài ~2–3 trang A4 (khoảng 4500–8000 ký tự trước NGUỒN); gọn, rõ, không luận dài.",
            "• Mỗi đoạn sự kiện: 2–4 câu tiếng Việt đầy đủ (ai/cái gì/khi nào/ở đâu/vì sao); không one-liner.",
            "• Nếu thiếu tin cho một mục cấu trúc: ghi «Chưa đủ bằng chứng trong cửa sổ» — không pad.",
            "• CẤM markdown ** * #. Xuất đúng các tiêu đề:",
            sections,
        ]
    )
    return "\n".join(lines)


def select_wire_threats(
    *,
    keyword: str = "",
    window_hours: int = 24,
    limit: int | None = None,
    defense_bias: bool = False,
    match_phrases: list[str] | None = None,
    must_tokens: list[str] | None = None,
    exclude_tokens: list[str] | None = None,
    strict: bool = False,
) -> list:
    """Pull Dòng tin items relevant to the focus query.

    When ``strict`` is True (keyword briefings), do not fall back to the full
    Wire window if phrase matching yields nothing — that caused false positives
    on long prompts.
    """
    from django.db.models import Q

    from apps.intel.models import Threat

    limit = int(limit or _wire_limit())
    since = timezone.now() - timedelta(hours=max(1, int(window_hours or 24)))
    qs = Threat.objects.filter(published_at__gte=since, wire_relevant=True)
    kw = " ".join((keyword or "").split()).strip()
    phrases = [
        " ".join(str(p or "").split()).strip()
        for p in (match_phrases if match_phrases is not None else ([kw] if kw else []))
    ]
    phrases = [p for p in phrases if len(p) >= 2][:8]
    must = [t.casefold() for t in (must_tokens or []) if len(str(t).strip()) > 2][:8]
    exclude = [
        t.casefold() for t in (exclude_tokens or []) if len(str(t).strip()) > 2
    ][:8]

    def _text_blob(row) -> str:
        return " ".join(
            [
                str(getattr(row, "title", "") or ""),
                str(getattr(row, "title_vi", "") or ""),
                str(getattr(row, "summary", "") or ""),
            ]
        ).casefold()

    def _passes_token_gates(row) -> bool:
        blob = _text_blob(row)
        if exclude and any(tok in blob for tok in exclude):
            return False
        if must and not any(tok in blob for tok in must):
            return False
        return True

    if phrases:
        phrase_q = Q()
        for phrase in phrases:
            phrase_q |= (
                Q(title__icontains=phrase)
                | Q(title_vi__icontains=phrase)
                | Q(summary__icontains=phrase)
            )
        filtered = list(
            qs.filter(phrase_q).order_by("-wire_priority", "-severity", "-published_at")[
                : max(limit * 3, limit)
            ]
        )
        rows = [r for r in filtered if _passes_token_gates(r)][:limit]
        if rows:
            return rows
        if strict:
            return []
        # Non-strict (legacy): only fall back when a single short keyword missed.
        if kw and not match_phrases and len(kw.split()) <= 4:
            return list(
                qs.order_by("-wire_priority", "-severity", "-published_at")[:limit]
            )
        return []

    if defense_bias:
        defense_q = Q()
        for term in _DEFENSE_WIRE_TERMS:
            defense_q |= (
                Q(title__icontains=term)
                | Q(title_vi__icontains=term)
                | Q(summary__icontains=term)
            )
        defense_rows = list(
            qs.filter(defense_q).order_by(
                "-wire_priority", "-severity", "-published_at"
            )[:limit]
        )
        if len(defense_rows) >= max(5, limit // 3):
            return defense_rows
        have = {r.pk for r in defense_rows}
        for row in qs.order_by("-wire_priority", "-severity", "-published_at")[
            :limit
        ]:
            if row.pk in have:
                continue
            defense_rows.append(row)
            if len(defense_rows) >= limit:
                break
        return defense_rows

    return list(qs.order_by("-wire_priority", "-severity", "-published_at")[:limit])


def _wire_digests_from_rows(
    rows: list[dict[str, Any]],
    *,
    focus: str,
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Ensure every Wire row contributes a digest (body or metadata)."""
    out = list(existing or [])
    have_urls = {
        str(d.get("url") or "").rstrip("/").casefold()
        for d in out
        if str(d.get("url") or "").strip()
    }
    have_titles = {
        str(d.get("title") or "").casefold()
        for d in out
        if not str(d.get("url") or "").strip()
    }
    for row in rows[: _wire_limit()]:
        url = str(row.get("url") or "")
        title = str(row.get("title") or "")
        url_key = url.rstrip("/").casefold()
        if url_key and url_key in have_urls:
            continue
        if not url_key and title.casefold() in have_titles:
            continue
        bits = _digest_article_sentences(
            str(row.get("summary") or ""),
            focus=focus,
            title=title,
            n=3,
        )
        if not bits and row.get("summary"):
            bits = [str(row.get("summary"))[:360]]
        if not bits and title:
            bits = [title]
        if bits:
            out.append({"title": title, "url": url, "digest": bits})
            if url_key:
                have_urls.add(url_key)
            else:
                have_titles.add(title.casefold())
    return out[: _model_wire_limit()]


def build_wire_wigolo_dossier(
    *,
    focus: str,
    threats: list,
    search_hint: str = "",
    scope: str = "keyword",
    kind: str = "daily",
    window_hours: int = 24,
    on_progress=None,
) -> dict[str, Any]:
    """
    Wire-first dossier: Trạm tin tức corpus → digests → optional related web.

    Daily/weekly stay Wire-primary (open-web only if Wire is thin). Keyword may
    use related web, but event claims must still come from Wire when available.
    """
    from apps.integrations.web_reader.wigolo import (
        research_wigolo,
        search_wigolo,
        wigolo_configured,
        wigolo_fetch_enabled,
    )

    def _prog(msg: str, pct: int) -> None:
        if callable(on_progress):
            try:
                on_progress(msg, pct)
            except Exception:  # noqa: BLE001
                pass

    kind = (kind or "daily").strip().lower()
    window_hours = resolve_briefing_window_hours(kind, window_hours)
    window_label = _window_label(kind, window_hours)
    focus = " ".join((focus or "").split()).strip() or "Dòng tin updates"
    hint = " ".join((search_hint or focus).split()).strip()
    _prog("Đang lập truy vấn tìm kiếm…", 12)
    # Heuristics only — Groq reserved for final style review.
    queries = _multilingual_search_queries(hint, scope=scope)

    wire_rows: list[dict[str, Any]] = []
    urls: list[str] = []
    for t in threats or []:
        title_vi = " ".join((getattr(t, "title_vi", "") or "").split()).strip()
        title = " ".join((getattr(t, "title", "") or "").split()).strip()
        summary = " ".join((getattr(t, "summary", "") or "").split()).strip()[:600]
        url = _threat_url(t)
        wire_rows.append(
            {
                "title": title_vi or title,
                "title_en": title,
                "summary": summary,
                "url": url,
                "severity": str(getattr(t, "severity", "") or ""),
                "published": (
                    t.published_at.strftime("%Y-%m-%d %H:%M")
                    if getattr(t, "published_at", None)
                    else ""
                ),
            }
        )
        if url:
            urls.append(url)

    search_hits: list[dict[str, Any]] = []
    research: dict[str, Any] = {"ok": False}
    articles: list[dict[str, Any]] = []
    warnings: list[str] = []
    fetch_fail = 0
    _prog(f"Đã chọn {len(wire_rows)} tin Trạm tin tức ({window_label})", 18)

    # Daily/weekly: Wire-only when corpus is usable. Keyword / thin Wire: allow web.
    wire_only = kind in {"daily", "weekly"} and len(wire_rows) >= 3
    if kind in {"daily", "weekly"} and len(wire_rows) < 3:
        warnings.append(
            f"Trạm tin tức mỏng trong {window_label} — có thể bổ sung web đối chiếu"
        )

    if not wigolo_configured():
        warnings.append("Wigolo chưa cấu hình — chỉ dùng metadata Trạm tin tức")
        _prog("⚠ Wigolo chưa cấu hình", 30)
        wire_digests = _wire_digests_from_rows(wire_rows, focus=focus)
        raw_draft = _assemble_raw_draft_from_digests(
            focus=focus,
            scope=scope,
            wire_digests=wire_digests,
            web_digests=[],
            window_label=window_label,
        )
        research = {
            "ok": True,
            "markdown": raw_draft,
            "error": "",
            "mode": "wire_meta_only",
        }
    else:
        time_range = _search_time_range(kind, window_hours)
        if wire_only:
            _prog("Chế độ Wire-primary — bỏ qua tìm web mở", 28)
        else:
            _prog(f"Đang tìm tin web đối chiếu ({time_range})…", 28)
            try:
                search_hits = search_wigolo(
                    queries if len(queries) > 1 else (queries[0] if queries else hint),
                    limit=_search_limit(),
                    category="news",
                    time_range=time_range,
                    search_depth=str(
                        getattr(settings, "WIGOLO_BRIEFING_SEARCH_DEPTH", "deep")
                        or "deep"
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"Tìm web thất bại: {exc}"[:160]
                logger.warning("dossier search failed: %s", exc)
                warnings.append(msg)
                _prog(f"⚠ {msg}", 32)
            else:
                _prog(f"Đã tìm {len(search_hits)} kết quả web", 36)

            # Supplement with Searx (local meta-search) for better coverage / snippets.
            try:
                from apps.integrations.searx.client import search_searx, searx_configured

                if searx_configured():
                    q0 = queries[0] if queries else hint
                    searx_hits = (
                        search_searx(
                            q0,
                            limit=min(12, _search_limit()),
                            time_range=time_range,
                        )
                        or []
                    )
                    seen = {
                        str(h.get("url") or "").rstrip("/")
                        for h in search_hits
                        if h.get("url")
                    }
                    added = 0
                    for h in searx_hits:
                        u = str(h.get("url") or "").rstrip("/")
                        if not u or u in seen:
                            continue
                        seen.add(u)
                        search_hits.append(
                            {
                                "title": h.get("title") or "",
                                "url": h.get("url") or "",
                                "content": h.get("content") or h.get("snippet") or "",
                                "snippet": h.get("content") or h.get("snippet") or "",
                                "engine": h.get("engine") or "searx",
                                "published": h.get("published") or "",
                            }
                        )
                        added += 1
                    if added:
                        _prog(
                            f"Searx bổ sung +{added} nguồn (tổng {len(search_hits)})",
                            38,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug("searx supplement skipped: %s", exc)

        wire_urls = _dedupe_urls(urls)
        web_urls = (
            []
            if wire_only
            else _dedupe_urls([str(h.get("url") or "") for h in search_hits])
        )
        fetch_budget = _fetch_cap()
        # Prefer Wire bodies; leave a small slice for related web when allowed.
        wire_frac = 0.9 if wire_only else 0.75
        wire_budget = max(
            6, min(int(fetch_budget * wire_frac), len(wire_urls) or fetch_budget)
        )
        ordered_urls = wire_urls[:wire_budget]
        for u in web_urls:
            if u not in ordered_urls:
                ordered_urls.append(u)
            if len(ordered_urls) >= fetch_budget:
                break

        wire_url_set = set(wire_urls)
        hit_by_url = {
            str(h.get("url") or ""): h
            for h in search_hits
            if str(h.get("url") or "")
        }
        wire_by_url = {str(r.get("url") or ""): r for r in wire_rows if r.get("url")}

        def _snippet_fallback(url: str) -> tuple[str, str]:
            """Title + text when full fetch fails (search snippet / wire summary)."""
            row = wire_by_url.get(url) or {}
            hit = hit_by_url.get(url) or {}
            title = str(row.get("title") or hit.get("title") or "")[:300]
            snip = " ".join(
                str(
                    row.get("summary")
                    or hit.get("content")
                    or hit.get("snippet")
                    or ""
                ).split()
            )
            return title, snip

        if not wigolo_fetch_enabled():
            warnings.append("Wigolo fetch tắt — dùng snippet/tóm tắt nguồn")
            _prog("Fetch tắt — dùng snippet", 45)
            for url in ordered_urls:
                title, snip = _snippet_fallback(url)
                body = snip
                if len(body) < 40:
                    fetch_fail += 1
                    continue
                digest = _digest_article_sentences(
                    body, focus=focus, title=title, n=_digest_sentence_count()
                )
                if not digest:
                    digest = [body[:480]]
                articles.append(
                    {
                        "url": url,
                        "title": title or url,
                        "excerpt": body[: _fetch_chars()],
                        "digest": digest,
                        "from_wire": url in wire_url_set,
                        "partial": True,
                    }
                )
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            from apps.integrations.web_reader.wigolo import fetch_url_resilient

            _prog(f"Đang đọc song song tối đa {len(ordered_urls)} nguồn…", 40)
            workers = max(2, min(4, len(ordered_urls) or 1))
            results: dict[str, dict] = {}

            def _one(u: str) -> tuple[str, dict]:
                try:
                    return u, fetch_url_resilient(u, max_chars=_fetch_chars())
                except Exception as exc:  # noqa: BLE001
                    return u, {
                        "ok": False,
                        "error": str(exc)[:120],
                        "text": "",
                        "title": "",
                    }

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_one, u) for u in ordered_urls]
                done_n = 0
                for fut in as_completed(futs):
                    u, fetched = fut.result()
                    results[u] = fetched
                    done_n += 1
                    if done_n == 1 or done_n % 3 == 0 or done_n == len(ordered_urls):
                        pct = 40 + int(22 * done_n / max(len(ordered_urls), 1))
                        _prog(
                            f"Đã đọc {done_n}/{len(ordered_urls)} nguồn…",
                            min(62, pct),
                        )

            soft_fail = 0
            for url in ordered_urls:
                fetched = results.get(url) or {}
                body = " ".join(str(fetched.get("text") or "").split())
                title = str(fetched.get("title") or "")[:300]
                partial = False
                if not fetched.get("ok") or len(body) < 120:
                    # Recover via search/wire snippet instead of dropping the source.
                    fb_title, snip = _snippet_fallback(url)
                    if len(snip) >= 40:
                        title = title or fb_title
                        body = snip
                        partial = True
                        soft_fail += 1
                    else:
                        fetch_fail += 1
                        err = str(fetched.get("error") or "empty")[:80]
                        logger.info(
                            "briefing skip unreadable url=%s err=%s",
                            url[:120],
                            err,
                        )
                        if fetch_fail <= 3:
                            warnings.append(
                                f"Không đọc được (đã thử fallback): {url[:70]} ({err})"
                            )
                        continue
                digest = _digest_article_sentences(
                    body, focus=focus, title=title, n=_digest_sentence_count()
                )
                if not digest:
                    digest = [body[:480]]
                articles.append(
                    {
                        "url": url,
                        "title": title or url,
                        "excerpt": body[: _fetch_chars()],
                        "digest": digest,
                        "from_wire": url in wire_url_set,
                        "partial": partial,
                    }
                )

            if soft_fail:
                # Info only — not a hard warning (source still used via snippet).
                logger.info(
                    "briefing used snippet fallback for %s/%s urls",
                    soft_fail,
                    len(ordered_urls),
                )
            if fetch_fail and not articles:
                warnings.append(
                    f"Không đọc được {fetch_fail}/{len(ordered_urls)} nguồn"
                )
            elif fetch_fail:
                # Downgrade: short note, avoid alarming UI when most succeeded.
                logger.info(
                    "briefing hard-failed %s/%s urls (others OK)",
                    fetch_fail,
                    len(ordered_urls),
                )
            _prog(
                f"Đã có {len(articles)}/{len(ordered_urls)} nguồn có điểm chính",
                64,
            )

        wire_digests = [
            {"title": a.get("title"), "url": a.get("url"), "digest": a.get("digest") or []}
            for a in articles
            if a.get("from_wire")
        ]
        web_digests = [
            {"title": a.get("title"), "url": a.get("url"), "digest": a.get("digest") or []}
            for a in articles
            if not a.get("from_wire")
        ]
        # Always fold remaining Wire metadata so multi-country synthesis has corpus.
        before = len(wire_digests)
        wire_digests = _wire_digests_from_rows(
            wire_rows, focus=focus, existing=wire_digests
        )
        if len(wire_digests) > before and before == 0:
            warnings.append("Dùng tóm tắt Trạm tin tức (chưa đọc đủ thân bài)")
        elif len(wire_digests) > before:
            logger.info(
                "briefing filled %s wire meta digests (fetched=%s)",
                len(wire_digests) - before,
                before,
            )

        raw_draft = _assemble_raw_draft_from_digests(
            focus=focus,
            scope=scope,
            wire_digests=wire_digests,
            web_digests=web_digests,
            window_label=window_label,
        )
        # Prefer local digest draft (stable). Only call Wigolo research when draft is thin.
        min_raw = int(getattr(settings, "AI_BRIEFING_MIN_RAW_DRAFT_CHARS", 500) or 500)
        if len(raw_draft) >= min_raw:
            research = {
                "ok": True,
                "markdown": raw_draft,
                "error": "",
                "mode": "local_digest_draft",
            }
            _prog("Bản thô từ Trạm tin tức — chuyển cloud tổng hợp đa chiều", 78)
        else:
            _prog("Bản thô mỏng — Wigolo bổ sung sắp xếp…", 68)
            research = _wigolo_draft_report(
                research_wigolo=research_wigolo,
                focus=focus,
                scope=scope,
                kind=kind,
                wire_rows=wire_rows,
                articles=articles,
                search_hits=search_hits,
                raw_draft=raw_draft,
            )
            md = str(research.get("markdown") or "").strip()
            if research.get("ok") and len(md) >= 200:
                _prog("Đã có bản thô Wigolo — chuyển Groq", 78)
            else:
                # Silent recovery — local draft still goes to Groq (no scary warning).
                research = {
                    "ok": True,
                    "markdown": raw_draft or md,
                    "error": "",
                    "mode": "local_raw_after_empty_research",
                }
                _prog("Dùng bản thô digest — Groq tổng hợp", 78)

    wire_articles = [a for a in articles if a.get("from_wire")]
    other_articles = [a for a in articles if not a.get("from_wire")]
    fetched_set = {a["url"] for a in articles}
    other_hits = [
        h
        for h in search_hits
        if str(h.get("url") or "") and str(h.get("url")) not in fetched_set
    ]

    dossier = _assemble_dossier_markdown(
        focus=focus,
        scope=scope,
        wire_rows=wire_rows,
        wire_articles=wire_articles,
        other_articles=other_articles,
        other_hits=other_hits if not wire_only else [],
        research=research,
        window_label=window_label,
    )
    sources = _collect_sources(
        wire_rows=wire_rows,
        articles=articles,
        search_hits=search_hits if not wire_only else [],
    )
    return {
        "focus": focus,
        "dossier": dossier,
        "wire_count": len(wire_rows),
        "article_count": len(articles),
        "search_count": len(search_hits),
        "research_ok": bool(research.get("ok")),
        "queries": queries,
        "urls_fetched": [a["url"] for a in articles],
        "sources": sources,
        "draft_chars": len(str(research.get("markdown") or "")),
        "warnings": warnings,
        "fetch_fail": fetch_fail,
        "raw_draft": str(research.get("markdown") or ""),
        "window_hours": window_hours,
        "window_label": window_label,
        "wire_only": wire_only,
    }


def _wigolo_draft_report(
    *,
    research_wigolo,
    focus: str,
    scope: str,
    kind: str,
    wire_rows: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    search_hits: list[dict[str, Any]],
    raw_draft: str = "",
) -> dict[str, Any]:
    """Organize per-article digests into a RAW draft. Final VI style is LLM's job."""
    digest_lines: list[str] = []
    art_cap = max(_fetch_cap(), _wire_limit())
    sent_n = _digest_sentence_count()
    for idx, art in enumerate(articles[:art_cap], start=1):
        origin = "WIRE" if art.get("from_wire") else "WEB"
        digest_lines.append(
            f"{idx}. [{origin}] {art.get('title') or ''} | {art.get('url')}"
        )
        for s in (art.get("digest") or [])[:sent_n]:
            digest_lines.append(f"   • {s}")
    if not digest_lines:
        for idx, row in enumerate(wire_rows[: _wire_limit()], start=1):
            digest_lines.append(f"{idx}. [WIRE] {row.get('title')} | {row.get('url')}")
            digest_lines.append(f"   • {row.get('summary')}")

    if scope == "general_defense":
        topic_rule = (
            "PRIORITY: military, defense strategy, cyber warfare/ops, weapons, exercises. "
            "Drop unrelated noise."
        )
    else:
        topic_rule = f"Stay on topic: {focus}."

    base = (raw_draft or "").strip()
    nl = chr(10)
    if len(base) >= 200:
        question = nl.join(
            [
                "You organize OSINT digests into one RAW DRAFT (not final prose).",
                "Do NOT polish Vietnamese administrative style — final review will do that later.",
                "Keep every source URL. Merge duplicates. Keep 3–5 long substantive bullets per item.",
                "Each bullet must carry who/what/when/where/why facts grounded in article body.",
                "",
                f"FOCUS: {focus}",
                f"KIND: {kind}",
                topic_rule,
                "",
                "RAW DRAFT TO ORGANIZE (keep facts/URLs):",
                # Long-context providers (Cerebras/OpenRouter) handle large digests;
                # Groq polish path shrinks separately when needed.
                base[:56000],
            ]
        )
    else:
        evidence_block = nl.join(digest_lines)[:56000]
        question = nl.join(
            [
                "Organize these per-article key points (3–5 long sentences each) into one RAW DRAFT.",
                "Do NOT final-translate to administrative Vietnamese — final review later.",
                "Keep URLs. Structure: TIÊU ĐỀ, TỔNG QUAN, SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN,",
                "QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA, NỔI BẬT THEO MẢNG, THÔNG TIN LIÊN QUAN KHÁC,",
                "NHẬN ĐỊNH NGẮN, NGUỒN.",
                "Each item: 3–5 full fact sentences (who/what/when/where/why) from article body — no one-liners.",
                "PRIMARY facts from WIRE only; do not invent.",
                "",
                f"FOCUS: {focus}",
                f"KIND: {kind}",
                topic_rule,
                "",
                "DIGESTS:",
                evidence_block,
            ]
        )

    depth = str(
        getattr(settings, "WIGOLO_BRIEFING_RESEARCH_DEPTH", None)
        or getattr(settings, "WIGOLO_BRIEFING_FALLBACK_DEPTH", "standard")
        or "standard"
    )
    if depth not in {"quick", "standard", "comprehensive"}:
        depth = "standard"
    if depth == "comprehensive" and len(articles) > 48:
        depth = "standard"
    max_sources = _wigolo_max_sources()
    try:
        research = research_wigolo(
            question,
            depth=depth,
            max_sources=max_sources,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dossier research failed: %s", exc)
        return {"ok": False, "error": str(exc)[:160], "markdown": raw_draft or ""}
    md = str(research.get("markdown") or "").strip()
    if research.get("ok") and len(md) < 200 and len(base) >= 200:
        research = {**research, "markdown": base, "mode": "local_raw_preferred"}
    elif not research.get("ok") and base:
        return {
            "ok": False,
            "error": research.get("error") or "empty",
            "markdown": base,
        }
    return research


def _collect_sources(
    *,
    wire_rows: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    search_hits: list[dict[str, Any]],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(title: str, url: str, kind: str) -> None:
        u = (url or "").strip()
        if not u or u.casefold() in seen:
            return
        seen.add(u.casefold())
        out.append({"title": (title or u)[:300], "url": u[:2048], "kind": kind})

    for row in wire_rows:
        add(str(row.get("title") or ""), str(row.get("url") or ""), "wire")
    for art in articles:
        kind = "wire" if art.get("from_wire") else "web"
        add(str(art.get("title") or ""), str(art.get("url") or ""), kind)
    for hit in search_hits:
        add(str(hit.get("title") or ""), str(hit.get("url") or ""), "web")
    return out[: _source_list_cap()]


def _assemble_dossier_markdown(
    *,
    focus: str,
    scope: str,
    wire_rows: list[dict[str, Any]],
    wire_articles: list[dict[str, Any]],
    other_articles: list[dict[str, Any]],
    other_hits: list[dict[str, Any]],
    research: dict[str, Any],
    window_label: str = "",
) -> str:
    parts: list[str] = [
        f"FOCUS: {focus}",
        f"SCOPE: {scope}",
        f"WINDOW: {window_label or 'n/a'}",
        "",
        "=== PRIMARY: WIRE (Trạm tin tức / Dòng tin) — highest priority ===",
    ]
    if wire_rows:
        for idx, row in enumerate(wire_rows, start=1):
            parts.append(f"{idx}) {row.get('title') or '(untitled)'}")
            if row.get("published"):
                parts.append(f"   date: {row['published']}")
            if row.get("summary"):
                parts.append(f"   wire_summary: {row['summary']}")
            if row.get("url"):
                parts.append(f"   url: {row['url']}")
    else:
        parts.append("(no matching Wire items)")

    parts.append("")
    parts.append("=== PER-ARTICLE DIGESTS (3–5 long key sentences each, from article body) ===")
    digests_shown = 0
    sent_n = _digest_sentence_count()
    body_chars = _dossier_body_chars()
    for art in list(wire_articles) + list(other_articles):
        dig = art.get("digest") or []
        if not dig:
            continue
        digests_shown += 1
        origin = "WIRE" if art.get("from_wire") else "WEB"
        parts.append(f"{digests_shown}) [{origin}] {art.get('title') or art.get('url')}")
        for s in dig[:sent_n]:
            parts.append(f"   • {s}")
        if art.get("url"):
            parts.append(f"   url: {art['url']}")
    if digests_shown == 0:
        parts.append("(none)")

    parts.append("")
    parts.append("=== WIRE ARTICLE BODIES (full/partial crawl — read carefully) ===")
    if wire_articles:
        for idx, art in enumerate(wire_articles, start=1):
            parts.append(f"{idx}) {art.get('title') or art.get('url')}")
            parts.append(f"   url: {art.get('url')}")
            # Keep substantial body for long-context synthesis; digests already carry key points.
            body = str(art.get("excerpt") or "")[:body_chars]
            parts.append(f"   body: {body}")
    else:
        parts.append("(no Wire bodies fetched)")

    parts.append("")
    parts.append(
        "=== OTHER RELATED INFO (open web — đối chiếu only; do not invent primary events) ==="
    )
    if other_articles or other_hits:
        idx = 1
        for art in other_articles:
            parts.append(f"{idx}) {art.get('title') or art.get('url')}")
            parts.append(f"   url: {art.get('url')}")
            parts.append(f"   body: {str(art.get('excerpt') or '')[:body_chars]}")
            idx += 1
        for hit in other_hits[:12]:
            parts.append(f"{idx}) {hit.get('title') or '(untitled)'}")
            snip = str(hit.get("content") or hit.get("snippet") or "")[:720]
            if snip:
                parts.append(f"   excerpt: {snip}")
            if hit.get("url"):
                parts.append(f"   url: {hit['url']}")
            idx += 1
    else:
        parts.append("(none)")

    parts.append("")
    parts.append("=== WIGOLO DRAFT REPORT (pre-final; already filtered) ===")
    md = str(research.get("markdown") or "").strip()
    if research.get("ok") and md:
        parts.append(md[:18000])
    else:
        parts.append(f"(draft unavailable: {research.get('error') or 'empty'})")
    return "\n".join(parts)


def _extract_wigolo_draft(dossier: str) -> str:
    marker = "=== WIGOLO DRAFT REPORT"
    if marker not in (dossier or ""):
        return ""
    draft = (dossier.split(marker, 1)[-1] or "").strip()
    lines = draft.splitlines()
    if lines and lines[0].startswith("("):
        return ""
    if lines and "WIGOLO" in lines[0].upper():
        lines = lines[1:]
    return "\n".join(lines).strip()


def _briefing_provider_is_groq(provider: str) -> bool:
    p = (provider or "").strip().casefold()
    return p == "groq" or p.startswith("groq+") or "+groq" in p


def _briefing_provider_is_shopaikey(provider: str) -> bool:
    p = (provider or "").strip().casefold()
    return p == "shopaikey" or p.startswith("shopaikey+") or "+shopaikey" in p


def _body_min_chars() -> int:
    """Minimum body prose (excl. NGUỒN) — ~2 A4 pages Vietnamese."""
    configured = getattr(settings, "AI_BRIEFING_BODY_MIN_CHARS", None)
    if configured is None:
        # Legacy alias: quality floor used to mean "keep long"; now = min band.
        configured = getattr(settings, "AI_BRIEFING_QUALITY_FLOOR_CHARS", 4500)
    return max(2500, min(int(configured or 4500), 12000))


def _body_max_chars() -> int:
    """Hard-ish cap for body prose — ~3 A4 pages Vietnamese."""
    return max(
        _body_min_chars() + 500,
        min(int(getattr(settings, "AI_BRIEFING_BODY_MAX_CHARS", 8000) or 8000), 16000),
    )


def _body_target_chars() -> int:
    """Preferred mid-band length (~2–3 A4 pages)."""
    lo, hi = _body_min_chars(), _body_max_chars()
    configured = int(
        getattr(settings, "AI_BRIEFING_BODY_TARGET_CHARS", 6000) or 6000
    )
    return max(lo, min(configured, hi))


def _quality_floor_chars() -> int:
    """Backward-compatible alias → body minimum (2 A4 floor)."""
    return _body_min_chars()


def _body_prose_chars(text: str) -> int:
    """Character count of narrative body excluding the NGUỒN link footer."""
    return len(_strip_nguon_section(text or "").strip())


_VI_DIACRITIC_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)
_EN_FUNC_WORDS = frozenset(
    {
        "the",
        "and",
        "with",
        "from",
        "that",
        "this",
        "have",
        "has",
        "been",
        "were",
        "will",
        "would",
        "could",
        "should",
        "their",
        "there",
        "which",
        "while",
        "about",
        "after",
        "before",
        "during",
        "according",
        "reported",
        "announced",
        "launched",
        "conducted",
        "deployed",
        "forces",
        "military",
        "defense",
        "defence",
        "exercise",
        "operations",
        "between",
        "against",
        "including",
        "however",
        "also",
        "into",
        "over",
        "under",
        "said",
        "says",
        "officials",
        "ministry",
        "government",
        "region",
        "regional",
        "alliance",
        "strategic",
        "security",
    }
)
# Proper nouns / acronyms allowed in otherwise Vietnamese prose.
_ALLOWED_LATIN_TOKENS = frozenset(
    {
        "nato",
        "pla",
        "a2/ad",
        "isr",
        "us",
        "uk",
        "eu",
        "un",
        "asean",
        "indopacific",
        "indo-pacific",
        "f-35",
        "b-21",
        "ai",
        "llm",
        "url",
        "http",
        "https",
        "www",
    }
)


def _strip_urls_and_links(text: str) -> str:
    """Remove URLs and markdown links so EN-leak heuristics ignore sources."""
    body = text or ""
    body = re.sub(r"\[[^\]]*\]\(https?://[^)\s]+\)", " ", body, flags=re.I)
    body = re.sub(r"https?://\S+", " ", body, flags=re.I)
    return body


def _narrative_body_for_lang_check(text: str) -> str:
    """Body without NGUỒN footer (links are allowed to stay Latin there)."""
    body = _strip_nguon_section(text or "")
    # Drop section headers themselves (often ALL CAPS VI / mixed).
    for h in TREND_SECTION_HEADERS:
        body = re.sub(rf"(?im)^{re.escape(h)}\s*$", "", body)
    return _strip_urls_and_links(body)


def _english_prose_leak_stats(text: str) -> dict[str, Any]:
    """Score leftover English sentences/paragraphs in the narrative body."""
    body = _narrative_body_for_lang_check(text)
    if not body.strip():
        return {
            "leak_sentences": 0,
            "en_func_hits": 0,
            "latin_words": 0,
            "vi_marks": 0,
            "score": 0.0,
        }
    vi_marks = len(_VI_DIACRITIC_RE.findall(body))
    latin_words = re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", body)
    latin_n = len(latin_words)
    en_func = sum(1 for w in latin_words if w.casefold() in _EN_FUNC_WORDS)
    # Sentence-ish chunks: prefer .!? then fall back to newlines.
    chunks = re.split(r"(?<=[\.!\?。！？])\s+|\n+", body)
    leak_n = 0
    for chunk in chunks:
        s = " ".join(chunk.split()).strip()
        if len(s) < 45:
            continue
        words = re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", s)
        if len(words) < 6:
            continue
        content = [
            w
            for w in words
            if w.casefold() not in _ALLOWED_LATIN_TOKENS and not w.isupper()
        ]
        if len(content) < 5:
            continue
        func = sum(1 for w in content if w.casefold() in _EN_FUNC_WORDS)
        local_vi = len(_VI_DIACRITIC_RE.findall(s))
        # Mostly Latin prose with English glue words and little/no Vietnamese.
        if func >= 3 and local_vi <= 2:
            leak_n += 1
        elif len(content) >= 10 and local_vi == 0 and func >= 2:
            leak_n += 1
    # Composite: sentence leaks dominate; raw EN function density as backup.
    score = float(leak_n) * 3.0 + min(en_func, 40) / 10.0
    if latin_n > 120 and vi_marks < 40:
        score += 4.0
    return {
        "leak_sentences": leak_n,
        "en_func_hits": en_func,
        "latin_words": latin_n,
        "vi_marks": vi_marks,
        "score": score,
    }


def _has_english_prose_leak(text: str) -> bool:
    stats = _english_prose_leak_stats(text)
    return stats["leak_sentences"] >= 1 or stats["score"] >= 4.0


def _briefing_structure_ok(text: str) -> bool:
    body = (text or "").strip()
    required = ("TIÊU ĐỀ", "TỔNG QUAN", "NGUỒN")
    return all(re.search(_section_heading_pattern(h), body) for h in required)


_RAW_REPORT_ARTIFACT_RE = re.compile(
    r"(?im)^\s*(?:wire_summary|body|excerpt|url)\s*:|"
    r"(?i:<\/?[a-z][^>]*>|&(?:#\d+|[a-z][a-z0-9]+);)"
)


def _briefing_has_raw_artifacts(text: str) -> bool:
    """Reject crawler fields/HTML that must never be exposed as report prose."""
    return bool(_RAW_REPORT_ARTIFACT_RE.search(_strip_nguon_section(text or "")))


def _repair_report_structure(
    text: str,
    *,
    focus: str,
    sources: list[dict[str, str]],
) -> str:
    """Repair harmless heading omissions before judging an otherwise useful draft."""
    body = (text or "").strip()
    if not re.search(_section_heading_pattern("TIÊU ĐỀ"), body):
        body = f"TIÊU ĐỀ\nBáo cáo nhanh: {focus}\n\n{body}".strip()
    if not re.search(_section_heading_pattern("TỔNG QUAN"), body):
        title_match = re.search(_section_heading_pattern("TIÊU ĐỀ"), body)
        if title_match:
            heading_end = body.find("\n", title_match.end())
            if heading_end < 0:
                insert_at = len(body)
            else:
                title_end = body.find("\n", heading_end + 1)
                insert_at = len(body) if title_end < 0 else title_end
            body = (
                body[:insert_at].rstrip()
                + "\n\nTỔNG QUAN\nBáo cáo tổng hợp các thông tin đã được lựa chọn từ Trạm tin tức."
                + body[insert_at:]
            )
        else:
            body = f"TỔNG QUAN\nBáo cáo tổng hợp theo chủ đề {focus}.\n\n{body}"
    return _ensure_sources_footer(body, sources)


def _section_heading_pattern(heading: str, *, through_end: bool = False) -> str:
    """Match plain headings plus common model prefixes such as ``1)``/``I.``."""
    flags = "(?ims)" if through_end else "(?im)"
    suffix = r".*\Z" if through_end else ""
    return (
        rf"{flags}^\s*(?:(?:\d+|[IVXLCDM]+)[\.\)\-:]\s*)?"
        rf"{re.escape(heading)}\b{suffix}"
    )


def _length_band_distance(body_chars: int) -> int:
    """0 if inside [min, max]; else how far outside the 2–3 A4 band."""
    lo, hi = _body_min_chars(), _body_max_chars()
    if body_chars < lo:
        return lo - body_chars
    if body_chars > hi:
        return body_chars - hi
    return 0


def _groq_candidate_acceptable(
    *,
    primary_text: str,
    candidate_text: str,
) -> bool:
    """Accept Groq output if readable VN, structured, and nearer the 2–3 A4 band.

    Condensing an oversized primary is encouraged. Reject garbage / EN mix /
    stripped structure / tiny stubs — not merely «shorter than primary».
    """
    from apps.integrations.ai.clients import is_local_llm_unavailable_text

    cand = (candidate_text or "").strip()
    if len(cand) < 400:
        return False
    if is_local_llm_unavailable_text(cand):
        return False
    if _briefing_has_raw_artifacts(cand):
        return False
    if not _briefing_structure_ok(cand):
        return False
    if _has_english_prose_leak(cand):
        # Allow only if primary was worse and candidate improved a lot.
        pre = _english_prose_leak_stats(primary_text)
        post = _english_prose_leak_stats(cand)
        if post["score"] >= pre["score"] or post["leak_sentences"] >= 2:
            return False
    cand_body = _body_prose_chars(cand)
    pri_body = _body_prose_chars(primary_text)
    lo = _body_min_chars()
    # Soft floor: allow slightly under min if primary was also thin.
    if cand_body < max(2800, int(lo * 0.7)):
        return False
    # Prefer candidate closer to the 2–3 page band (or already inside).
    if _length_band_distance(cand_body) > _length_band_distance(pri_body) + 800:
        # Much worse length fit than primary — reject unless primary had EN leak.
        if not _has_english_prose_leak(primary_text):
            return False
    return True


def _briefing_needs_groq_assist(
    text: str,
    *,
    provider: str,
    draft_chars: int = 0,
) -> bool:
    """True when primary is thin/weak/oversized/EN-mixed — needs Groq rewrite.

    Oversized essays also trigger assist so Groq can condense to 2–3 A4 pages.
    """
    if not bool(getattr(settings, "AI_BRIEFING_GROQ_QUALITY_ASSIST", True)):
        return False
    if _briefing_provider_is_groq(provider):
        # Pure Groq primary: still assist when over max or EN leak.
        body_n = _body_prose_chars(text)
        return body_n > _body_max_chars() or _has_english_prose_leak(text)
    body = (text or "").strip()
    min_chars = int(
        getattr(settings, "AI_BRIEFING_GROQ_ASSIST_MIN_CHARS", 900) or 900
    )
    body_n = _body_prose_chars(body)
    required = ("TIÊU ĐỀ", "TỔNG QUAN", "NGUỒN")
    missing = sum(1 for h in required if not re.search(rf"(?im)^{re.escape(h)}\b", body))
    multi = (
        "SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN",
        "QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA",
        "NỔI BẬT THEO MẢNG",
    )
    multi_missing = sum(
        1 for h in multi if not re.search(rf"(?im)^{re.escape(h)}\b", body)
    )
    # Always prefer Groq when outside the readable 2–3 page band.
    if body_n > _body_max_chars() or body_n < _body_min_chars():
        return True
    if len(body) < max(400, min_chars):
        return True
    if missing >= 2 or multi_missing >= 2:
        return True
    if _has_english_prose_leak(body):
        return True
    stats = _english_prose_leak_stats(body)
    if stats["latin_words"] > 80 and stats["vi_marks"] < 20:
        return True
    if body.count("**") >= 4 or body.count("##") >= 2:
        return True
    if (
        draft_chars >= 1200
        and len(body) < max(min_chars, 2800)
        and len(body) < max(min_chars, int(draft_chars * 0.08))
    ):
        return True
    return False


def _briefing_must_run_groq_final(
    text: str,
    *,
    provider: str,
) -> bool:
    """Run a Groq repair only when the primary output actually needs it.

    Clean paid ShopAIKey output is accepted directly to reduce latency and
    avoid a second model call. Other mid-tier providers retain the old pass.
    """
    if not bool(getattr(settings, "AI_BRIEFING_GROQ_LANGUAGE_POLISH", True)):
        return False
    body = (text or "").strip()
    if len(body) < 200:
        return False
    body_n = _body_prose_chars(body)
    if _briefing_provider_is_shopaikey(provider):
        return (
            body_n > _body_max_chars()
            or body_n < _body_min_chars()
            or _has_english_prose_leak(body)
            or not _briefing_structure_ok(body)
        )
    if not _briefing_provider_is_groq(provider):
        return True
    return (
        body_n > _body_max_chars()
        or body_n < _body_min_chars()
        or _has_english_prose_leak(body)
    )


def _vn_only_structure_rules() -> str:
    lo, hi, target = _body_min_chars(), _body_max_chars(), _body_target_chars()
    return f"""
QUY TẮC NGÔN NGỮ (CỨNG):
- TOÀN BỘ phần tường thuật phải là tiếng Việt hành chính–quân sự, rõ ràng, dễ đọc.
- CẤM câu / đoạn tiếng Anh trong thân báo cáo. Không lẫn EN+VI.
- Được giữ: URL https://, tên riêng (người/tổ chức/vũ khí), viết tắt chuẩn (NATO, PLA, ASEAN…).
- Không dịch cứng máy; diễn đạt tự nhiên nhưng trang trọng.

CẤU TRÚC NỘI DUNG (gọn, bám bằng chứng Wire):
1) Từng tin tức quan trọng — chọn tối đa 8–12 sự kiện đại diện; mỗi sự kiện thành một đoạn ngắn (ai/cái gì/khi nào/ở đâu/vì sao).
2) Mối liên kết giữa các tin — chỉ nối khi có ít nhất hai căn cứ độc lập; không nối chỉ vì cùng thẻ chủ đề hoặc cùng một loại vũ khí.
3) Các quốc gia — nêu rõ quốc gia/thực thể xuất hiện trên Trạm tin tức.

QUY TẮC TRÁNH LẶP / NHIỄU:
- Một bài nguồn chỉ xuất hiện một lần trong phần sự kiện; không lặp lại cùng sự kiện ở phần quốc gia và phần mảng.
- Gộp các tin cùng một sự kiện; ưu tiên bản có thông tin đầy đủ hơn.
- Bỏ tin đời sống, sự cố dân sự hoặc nội dung không có liên hệ quân sự–quốc phòng rõ ràng.
- Không đưa URL riêng trong thân bài; chỉ liệt kê URL thật một lần ở mục NGUỒN cuối báo cáo.

ĐỘ DÀI (2–3 trang A4 tiếng Việt):
- Phần tường thuật (TRƯỚC mục NGUỒN): mục tiêu ~{target} ký tự; khoảng {lo}–{hi} ký tự.
- CẤM bài luận dài lê thê / phình quá ~3 trang. Nếu bản gốc dài: CỐT LÕI + CÔ ĐỌNG, giữ sự kiện chính.
- Mục NGUỒN (danh sách link) KHÔNG tính vào ngân sách 2–3 trang; viết sau cùng.
- Giữ đủ tiêu đề mục đúng thứ tự; chỉ có một mục NGUỒN ở cuối; không bịa URL.
""".strip()


def _groq_run_models(
    prompt: str,
    *,
    max_tokens: int,
    prefer_fast: bool,
    groq_cap: int,
    label: str,
) -> dict[str, Any] | None:
    from apps.integrations.ai.clients import AIProviderError, groq_complete

    primary = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile"
    fallback = (
        getattr(settings, "GROQ_BRIEFING_FALLBACK_MODEL", "llama-3.1-8b-instant")
        or "llama-3.1-8b-instant"
    )
    models = [fallback, primary] if prefer_fast else [primary, fallback]
    seen: set[str] = set()
    last_exc: Exception | None = None
    for model in models:
        m = str(model).strip()
        if not m or m in seen:
            continue
        seen.add(m)
        try:
            return groq_complete(
                prompt,
                max_tokens=max_tokens,
                model=m,
                prompt_limit=groq_cap,
            )
        except AIProviderError as exc:
            last_exc = exc
            logger.info("%s failed model=%s: %s", label, m, exc)
    if last_exc:
        logger.warning("%s unavailable: %s", label, last_exc)
    return None


def _groq_final_rewrite_prompt(
    *,
    focus: str,
    text: str,
    primary_provider: str,
    sources: list[dict[str, str]] | None,
    condense: bool,
) -> str:
    """Build Groq prompt: VN-only polish and/or condense to 2–3 A4 pages."""
    lo, hi, target = _body_min_chars(), _body_max_chars(), _body_target_chars()
    groq_cap = max(
        6000,
        min(int(getattr(settings, "AI_BRIEFING_GROQ_PROMPT_CHARS", 10000) or 10000), 8000),
    )
    # Fit primary into Groq window; oversized drafts are intentionally truncated —
    # the model must synthesize a compact 2–3 page report from what fits.
    body_budget = max(4200, min(groq_cap - 2200, 6000))
    body = (text or "").strip()
    clipped = body[:body_budget]
    truncated = len(body) > len(clipped)
    sections = "\n".join(TREND_SECTION_HEADERS)
    source_lines = [
        f"- [{s.get('title') or 'Nguồn'}]({s.get('url')})"
        for s in (sources or [])[:40]
        if (s.get("url") or "").startswith("http")
    ]
    source_hint = "\n".join(source_lines) if source_lines else "(giữ NGUỒN như bản gốc)"
    lang_rules = _vn_only_structure_rules()
    length_task = (
        f"CÔ ĐỌNG bản dài thành báo cáo gọn 2–3 trang A4 "
        f"(thân bài {lo}–{hi} ký tự, mục tiêu ~{target}; NGUỒN không tính)."
        if condense
        else f"Chỉnh ngôn ngữ/rõ ràng; giữ thân bài trong khoảng {lo}–{hi} ký tự "
        f"(mục tiêu ~{target})."
    )
    trunc_note = (
        "\n(Bản gốc dài hơn cửa sổ — tổng hợp CỐT LÕI từ phần đã gửi; "
        "ưu tiên tin quan trọng + liên kết + quốc gia; đủ NGUỒN từ danh sách.)\n"
        if truncated
        else ""
    )
    return f"""
Nhiệm vụ: Bước Groq CUỐI — xuất báo cáo tiếng Việt DỄ ĐỌC ({length_task}).
Nguồn chính: {primary_provider}.
- Không kết thúc báo cáo trước khi phần thân đạt tối thiểu {lo} ký tự; ưu tiên bổ sung dữ kiện có trong bản thô, không lặp ý để kéo dài.
- Dịch / viết lại mọi câu–đoạn tiếng Anh còn sót thành tiếng Việt hành chính–quân sự.
- Bám 3 trục gọn: từng tin quan trọng → mối liên kết giữa các tin → các quốc gia (Wire).
- Chọn tối đa 8–12 sự kiện đại diện; mỗi tin quan trọng: 2–4 câu đủ ý (ai/cái gì/khi nào/ở đâu/vì sao).
- Gộp tin trùng sự kiện và không lặp một nguồn ở nhiều mục. Không kéo dài bằng câu chung chung.
- Chỉ xác lập liên kết khi có ít nhất hai căn cứ độc lập; cùng một thẻ chủ đề hoặc một loại vũ khí không đủ.
- CẤM markdown đậm/nghiêng/heading (** * #). Không chèn URL riêng trong thân bài; chỉ dùng mục NGUỒN cuối.
Đủ các tiêu đề (đúng thứ tự):
{sections}
{lang_rules}
Mục NGUỒN: liệt kê mỗi link Wire thật một lần từ danh sách dưới (mỗi dòng • [tiêu đề](url)); CẤM bịa domain.
{trunc_note}
CHỦ ĐỀ: {focus}

NGUỒN WIRE THẬT:
{source_hint}

BẢN CẦN XỬ LÝ:
{clipped}
""".strip()


def _groq_quality_assist_text(
    *,
    focus: str,
    weak_text: str,
    primary_provider: str,
    max_tokens: int,
    prefer_fast: bool,
    sources: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Groq rewrite for thin/weak/oversized primary → 2–3 A4 VN report."""
    from apps.integrations.ai.groq_pool import groq_keys_configured

    if not groq_keys_configured(pool="briefing"):
        return None
    groq_cap = int(getattr(settings, "AI_BRIEFING_GROQ_PROMPT_CHARS", 10000) or 10000)
    body_n = _body_prose_chars(weak_text)
    condense = body_n > _body_max_chars()
    prompt = _groq_final_rewrite_prompt(
        focus=focus,
        text=weak_text,
        primary_provider=primary_provider,
        sources=sources,
        condense=condense or True,
    )
    # Cap tokens so Groq cannot balloon past ~3 pages.
    assist_tokens = max(
        1200,
        min(int(max_tokens or 2200), int(_body_max_chars() // 2) + 400, 3200),
    )
    return _groq_run_models(
        prompt,
        max_tokens=assist_tokens,
        prefer_fast=prefer_fast,
        groq_cap=groq_cap,
        label="groq quality-assist",
    )


def _groq_language_polish_text(
    *,
    focus: str,
    text: str,
    primary_provider: str,
    max_tokens: int,
    prefer_fast: bool = True,
    sources: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Mandatory Groq final: VN polish and/or condense to 2–3 A4 pages."""
    from apps.integrations.ai.groq_pool import groq_keys_configured

    if not groq_keys_configured(pool="briefing"):
        return None
    groq_cap = int(getattr(settings, "AI_BRIEFING_GROQ_PROMPT_CHARS", 10000) or 10000)
    body_n = _body_prose_chars(text)
    condense = body_n > _body_max_chars()
    prompt = _groq_final_rewrite_prompt(
        focus=focus,
        text=text,
        primary_provider=primary_provider,
        sources=sources,
        condense=condense,
    )
    polish_tokens = max(
        1400,
        min(int(max_tokens or 2200), int(_body_max_chars() // 2) + 500, 3200),
    )
    return _groq_run_models(
        prompt,
        max_tokens=polish_tokens,
        prefer_fast=prefer_fast,
        groq_cap=groq_cap,
        label="groq language-polish",
    )


def _apply_groq_final_pass(
    *,
    focus: str,
    text: str,
    provider: str,
    max_tokens: int,
    sources: list[dict[str, str]],
    raw_meta: dict[str, Any],
    normalize_briefing_prose,
    fast: bool,
) -> tuple[str, str]:
    """Always-on Groq final readability pass with one retry on garbage."""
    if not _briefing_must_run_groq_final(text, provider=provider):
        return text, provider

    pre = text
    pre_stats = _english_prose_leak_stats(pre)
    best_text = pre
    best_meta: dict[str, Any] | None = None
    # One bounded repair is enough. Repeating against rate-limited free keys
    # previously added minutes without improving the report.
    attempts = 1
    for attempt in range(1, attempts + 1):
        polished = _groq_language_polish_text(
            focus=focus,
            text=pre if attempt == 1 else best_text,
            primary_provider=provider or "midtier",
            max_tokens=max_tokens,
            prefer_fast=True,
            sources=sources,
        )
        if not polished or not str(polished.get("text") or "").strip():
            raw_meta.setdefault("language_polish_attempts", []).append(
                {"attempt": attempt, "ok": False, "reason": "empty"}
            )
            continue
        candidate = normalize_briefing_prose(str(polished.get("text") or ""))
        candidate = _repair_report_structure(
            candidate, focus=focus, sources=sources
        )
        ok = _groq_candidate_acceptable(primary_text=pre, candidate_text=candidate)
        post_stats = _english_prose_leak_stats(candidate)
        raw_meta.setdefault("language_polish_attempts", []).append(
            {
                "attempt": attempt,
                "ok": ok,
                "chars": len(candidate),
                "body_chars": _body_prose_chars(candidate),
                "en_leak": post_stats,
            }
        )
        if ok:
            best_text = candidate
            best_meta = {
                "from": provider,
                "to": "groq",
                "attempt": attempt,
                "primary_chars": len(pre),
                "primary_body_chars": _body_prose_chars(pre),
                "polished_chars": len(candidate),
                "polished_body_chars": _body_prose_chars(candidate),
                "en_leak_before": pre_stats,
                "en_leak_after": post_stats,
                **(polished.get("raw") or {}),
            }
            # Good enough: inside band and no EN leak — stop early.
            if (
                _length_band_distance(_body_prose_chars(candidate)) == 0
                and not _has_english_prose_leak(candidate)
            ):
                break
        elif _has_english_prose_leak(pre) and not _has_english_prose_leak(candidate):
            # Prefer any VN-clean structured draft over mixed-language primary.
            if _briefing_structure_ok(candidate) and _body_prose_chars(candidate) >= 2800:
                best_text = candidate
                best_meta = {
                    "from": provider,
                    "to": "groq",
                    "attempt": attempt,
                    "reason": "prefer_vn_over_en_primary",
                    "primary_chars": len(pre),
                    "polished_chars": len(candidate),
                    **(polished.get("raw") or {}),
                }

    if best_meta and best_text is not pre:
        raw_meta["language_polish"] = best_meta
        if not _briefing_provider_is_groq(provider):
            primary = (provider or "midtier").split("+")[0]
            provider = f"groq+{primary}"[:32]
        raw_meta["mode"] = (
            "groq_language_polish_fast" if fast else "groq_language_polish"
        )
        return best_text, provider

    raw_meta["language_polish_skipped"] = {
        "reason": "groq_unavailable_or_unacceptable",
        "primary_body_chars": _body_prose_chars(pre),
        "en_leak": pre_stats,
    }
    return text, provider


def polish_dossier_with_groq(
    *,
    focus: str,
    dossier: str,
    kind: str = "daily",
    scope: str = "keyword",
    sources: list[dict[str, str]] | None = None,
    fast: bool = False,
    window_hours: int | None = None,
) -> dict[str, Any]:
    """Final review: synthesize then ALWAYS Groq-polish to 2–3 A4 VN pages."""
    from apps.integrations.ai.briefings import normalize_briefing_prose
    from apps.integrations.ai.clients import AIProviderError, generate_briefing_text

    focus = " ".join((focus or "").split()).strip()
    kind = (kind or "daily").strip().lower()
    window_hours = resolve_briefing_window_hours(kind, window_hours)
    window_label = _window_label(kind, window_hours)
    sections = "\n".join(TREND_SECTION_HEADERS)
    dossier_trim = (dossier or "").strip()
    draft = _extract_wigolo_draft(dossier_trim)
    max_dossier = int(
        getattr(settings, "AI_BRIEFING_POLISH_DOSSIER_CHARS", 36000) or 36000
    )
    # Prefer large review body for Cerebras/OpenRouter; Groq trims inside groq_complete.
    if fast:
        body_cap = min(max_dossier, 14000)
    else:
        body_cap = min(
            max_dossier,
            int(
                getattr(settings, "AI_BRIEFING_CEREBRAS_PROMPT_CHARS", 36000) or 36000
            ),
        )
    if draft and len(draft) >= 200:
        review_body = draft[:body_cap]
        mode_hint = (
            "BẢN THÔ (digest từ Trạm tin tức) — tổng hợp GỌN 2–3 trang A4 tiếng Việt"
        )
    else:
        review_body = dossier_trim[: min(max_dossier, body_cap)]
        mode_hint = "HỒ SƠ Trạm tin tức — dựng báo cáo gọn 2–3 trang từ digest/thân bài"

    source_lines = [
        f"- [{s.get('title') or 'Nguồn'}]({s.get('url')})"
        for s in (sources or [])[: _source_list_cap()]
        if (s.get("url") or "").startswith("http")
    ]
    source_block = "\n".join(source_lines) if source_lines else "(xem url trong hồ sơ)"

    if scope == "general_defense":
        theme_rule = (
            "- Tổng hợp chung từ tin trong cửa sổ: quân sự / quốc phòng / chiến lược / "
            "tác chiến mạng / ngoại giao / điểm nóng — chỉ khi có trong PRIMARY Wire."
        )
    else:
        theme_rule = (
            f"- Bám sát chủ đề «{focus}»: chỉ nêu tin liên quan chủ đề; "
            "bỏ nhiễu ngoài phạm vi."
        )

    lo, hi, target = _body_min_chars(), _body_max_chars(), _body_target_chars()
    lang_rules = _vn_only_structure_rules()
    prompt = f"""
Nhiệm vụ: Tổng hợp BÁO CÁO XU HƯỚNG / CHỦ ĐỀ bằng tiếng Việt, văn phong hành chính–quân sự,
GỌN – RÕ – DỄ ĐỌC (khoảng 2–3 trang A4). KHÔNG viết bài luận dài lê thê.
Độ dài thân bài (trước NGUỒN): mục tiêu ~{target} ký tự; khoảng {lo}–{hi} ký tự.

CHỦ ĐỀ: {focus}
LOẠI: {kind}
CỬA SỔ: {window_label}
ĐẦU VÀO: {mode_hint}

CẤU TRÚC BẮT BUỘC (đúng thứ tự, mỗi mục là tiêu đề riêng trên một dòng):
{sections}

YÊU CẦU NỘI DUNG (3 trục gọn: từng tin quan trọng → liên kết giữa các tin → quốc gia):
1) TỔNG QUAN — 1 đoạn ngắn (4–8 câu) nêu bức tranh tổng thể và vài tin chính; không liệt kê headline suông.
2) SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN — chọn tối đa 8–12 tin/sự kiện đại diện, gắn nước/thực thể trên Trạm tin tức
   (Mỹ, Trung Quốc, và nước khác khi có). Mỗi tin: 2–4 câu (ai / cái gì / khi nào / ở đâu / vì sao); gộp tin trùng.
   Không lặp một nguồn ở nhiều mục và không chèn URL riêng trong thân bài.
3) QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA — chỉ nối các tin khi có ít nhất hai căn cứ độc lập (liên minh, diễn tập, đàm phán, cùng chủ thể và cùng hoạt động…).
4) NỔI BẬT THEO MẢNG — nhóm ngắn theo lĩnh vực có trong bằng chứng; bỏ mảng trống.
5) THÔNG TIN LIÊN QUAN KHÁC — chỉ mục phụ đã có; nếu không: ghi thiếu.
6) NHẬN ĐỊNH NGẮN — 1 đoạn (3–6 câu) chỉ nối sự kiện đã nêu; không suy đoán.
7) NGUỒN — sau thân bài: mỗi URL thật chỉ xuất hiện một lần, mỗi dòng • [tiêu đề bài](https://url-thật) từ «NGUỒN ĐÃ THU»; CẤM bịa URL.
   (Danh sách NGUỒN không tính vào ngân sách 2–3 trang.)

QUY TẮC CỨNG:
{theme_rule}
{lang_rules}
- CHỈ khẳng định sự kiện có trong PRIMARY (Trạm tin tức / Dòng tin). Không bịa số liệu, tuyên bố, quan hệ.
- Web «THÔNG TIN LIÊN QUAN» chỉ đối chiếu — không biến thành sự kiện chính nếu không có trên Wire.
- CẤM markdown đậm/nghiêng/heading: không **, không * đơn, không #. Dùng 1) 2) 3) và • khi cần.
- Nếu bằng chứng mỏng về một mục: ghi «Chưa đủ bằng chứng trong cửa sổ» rồi chuyển mục khác.

NGUỒN ĐÃ THU (dùng nguyên URL này cho mục NGUỒN — không viết lại domain):
{source_block}

BẢN THÔ / DIGEST CẦN TỔNG HỢP (chọn lọc tin quan trọng — đừng phình bài):
{review_body}
""".strip()

    if fast:
        max_tokens = int(
            getattr(settings, "AI_BRIEFING_REVIEW_FAST_MAX_TOKENS", 2200) or 2200
        )
        prefer_fast = True
    else:
        max_tokens = max(
            3200,
            int(getattr(settings, "AI_BRIEFING_REVIEW_MAX_TOKENS", 2800) or 2800),
        )
        prefer_fast = bool(
            getattr(settings, "AI_BRIEFING_REVIEW_PREFER_FAST", False)
        )
    # Hard cap primary completion so mid-tier models don't emit 15k+ essays.
    max_tokens = max(1200, min(max_tokens, 3200))
    try:
        result = generate_briefing_text(
            prompt,
            max_tokens=max_tokens,
            allow_wigolo_fallback=False,
            prefer_fast_model=prefer_fast,
            prefer_long_context=True,
            allow_local_fallback=False,
            retry_rounds=1,
        )
        provider = str(result.get("provider") or "")
        text = normalize_briefing_prose(str(result.get("text") or ""))
        text = _repair_report_structure(
            text, focus=focus, sources=sources or []
        )
        from apps.integrations.ai.clients import is_local_llm_unavailable_text

        if provider == "local" or is_local_llm_unavailable_text(text):
            raise AIProviderError("review returned local LLM-unavailable stub")
        min_chars = int(
            getattr(settings, "AI_BRIEFING_GROQ_ASSIST_MIN_CHARS", 900) or 900
        )
        # Validate narrative only. A long NGUỒN footer must never turn an empty
        # model response (for example "User Safety: safe") into a valid report.
        primary_body_chars = _body_prose_chars(text)
        invalid_reason = ""
        if primary_body_chars < max(700, min_chars):
            invalid_reason = f"review body too short ({primary_body_chars} chars)"
        elif not _briefing_structure_ok(text):
            invalid_reason = "review is missing required report structure"

        raw_meta: dict[str, Any] = {
            **(result.get("raw") or {}),
            "mode": "fast_final_review" if fast else "full_final_review",
            "window_hours": window_hours,
            "window_label": window_label,
            "body_target_chars": target,
            "body_min_chars": lo,
            "body_max_chars": hi,
            "primary_body_chars": primary_body_chars,
        }
        if invalid_reason:
            # A mid-tier provider can return useful prose with malformed or
            # missing headings. Do not discard the verified dossier: give the
            # dedicated Groq briefing pool one structured recovery pass.
            recovery = _groq_quality_assist_text(
                focus=focus,
                weak_text=review_body,
                primary_provider=provider or "midtier",
                max_tokens=max_tokens,
                prefer_fast=fast,
                sources=sources or [],
            )
            recovered_text = normalize_briefing_prose(
                str((recovery or {}).get("text") or "")
            )
            recovered_text = _repair_report_structure(
                recovered_text, focus=focus, sources=sources or []
            )
            recovered_body_chars = _body_prose_chars(recovered_text)
            if (
                not recovery
                or recovered_body_chars < max(2800, int(lo * 0.7))
                or not _briefing_structure_ok(recovered_text)
                or _has_english_prose_leak(recovered_text)
            ):
                raise AIProviderError(f"{invalid_reason}; Groq recovery unavailable or invalid")
            raw_meta["invalid_primary_recovery"] = {
                "reason": invalid_reason,
                "from": provider or "midtier",
                "to": "groq",
                "primary_body_chars": primary_body_chars,
                "recovered_body_chars": recovered_body_chars,
                **(recovery.get("raw") or {}),
            }
            raw_meta["mode"] = "groq_invalid_primary_recovery"
            text = recovered_text
            provider = "groq"
        else:
            text = _ensure_sources_footer(text, sources or [])
        if _briefing_needs_groq_assist(
            text, provider=provider, draft_chars=len(review_body)
        ):
            thin_primary = _body_prose_chars(text) < lo
            assisted = _groq_quality_assist_text(
                focus=focus,
                weak_text=review_body if thin_primary else text,
                primary_provider=provider or "midtier",
                max_tokens=3200 if not fast else max_tokens,
                prefer_fast=fast,
                sources=sources or [],
            )
            if assisted and str(assisted.get("text") or "").strip():
                assisted_text = normalize_briefing_prose(
                    str(assisted.get("text") or "")
                )
                assisted_text = _repair_report_structure(
                    assisted_text, focus=focus, sources=sources or []
                )
                if _groq_candidate_acceptable(
                    primary_text=text, candidate_text=assisted_text
                ):
                    raw_meta["quality_assist"] = {
                        "from": provider,
                        "to": "groq",
                        "primary_chars": len(text),
                        "primary_body_chars": _body_prose_chars(text),
                        "assisted_chars": len(assisted_text),
                        "assisted_body_chars": _body_prose_chars(assisted_text),
                        **(assisted.get("raw") or {}),
                    }
                    primary = (provider or "midtier").split("+")[0]
                    provider = f"groq+{primary}"[:32]
                    text = assisted_text
                    raw_meta["mode"] = (
                        "groq_quality_assist_fast" if fast else "groq_quality_assist"
                    )
                else:
                    raw_meta["quality_assist_skipped"] = {
                        "reason": "assist_unacceptable",
                        "primary_chars": len(text),
                        "assisted_chars": len(assisted_text),
                        "assisted_body_chars": _body_prose_chars(assisted_text),
                    }

        # Mandatory Groq readability pass (polish / condense / VN-normalize).
        text, provider = _apply_groq_final_pass(
            focus=focus,
            text=text,
            provider=provider,
            max_tokens=max_tokens,
            sources=sources or [],
            raw_meta=raw_meta,
            normalize_briefing_prose=normalize_briefing_prose,
            fast=fast,
        )
        # A failed repair must not publish a provider refusal or a tiny answer.
        # Fall back to the evidence-backed draft, which is already checkpointed.
        final_body_chars = _body_prose_chars(text)
        if (
            final_body_chars < max(2800, int(lo * 0.7))
            or not _briefing_structure_ok(text)
            or _has_english_prose_leak(text)
            or _briefing_has_raw_artifacts(text)
        ):
            raise AIProviderError(
                f"final report failed validation ({final_body_chars} body chars)"
            )
        raw_meta["final_body_chars"] = final_body_chars

        return {
            "ok": True,
            "text": text,
            "provider": provider or "groq",
            "raw": raw_meta,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("groq full review failed, keeping raw draft: %s", exc)
        fallback = _bounded_dossier_fallback_vi(
            focus=focus, dossier=dossier_trim, sources=sources or []
        )
        fallback = normalize_briefing_prose(fallback)
        fallback_body_chars = _body_prose_chars(fallback)
        if (
            fallback_body_chars >= max(2800, int(_body_min_chars() * 0.7))
            and _briefing_structure_ok(fallback)
            and not _has_english_prose_leak(fallback)
            and not _briefing_has_raw_artifacts(fallback)
        ):
            return {
                "ok": True,
                "text": fallback,
                "provider": "wigolo",
                "raw": {
                    "mode": "bounded_evidence_fallback",
                    "body_chars": fallback_body_chars,
                    "degraded": True,
                },
            }
        safe_fallback = _sources_only_fallback_vi(
            focus=focus, sources=sources or []
        )
        safe_fallback = normalize_briefing_prose(safe_fallback)
        return {
            "ok": True,
            "text": safe_fallback,
            "provider": "wigolo",
            "raw": {
                "mode": "vietnamese_sources_fallback",
                "body_chars": _body_prose_chars(safe_fallback),
                "degraded": True,
            },
        }


def _format_nguon_lines(sources: list[dict[str, str]], *, limit: int | None = None) -> list[str]:
    """Authoritative NGUỒN bullets: markdown links to real Wire/web URLs."""
    lines: list[str] = []
    seen: set[str] = set()
    # Prefer Wire first so readers see Trạm tin tức links.
    ordered = sorted(
        sources or [],
        key=lambda s: 0 if str(s.get("kind") or "").lower() == "wire" else 1,
    )
    cap = len(ordered) if limit is None else max(1, int(limit))
    for s in ordered:
        url = (s.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        raw_title = html.unescape(str(s.get("title") or ""))
        raw_title = re.sub(r"<[^>]+>", " ", raw_title)
        title = re.sub(r"[\r\n\[\]]+", " ", raw_title).strip()
        title = " ".join(title.split())[:200]
        latin_words = re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", title)
        has_vi = bool(_VI_DIACRITIC_RE.search(title))
        if not title or _has_english_prose_leak(title) or (len(latin_words) >= 6 and not has_vi):
            title = f"Nguồn tin {len(lines) + 1}"
        lines.append(f"• [{title}]({url})")
        if len(lines) >= cap:
            break
    return lines


def _strip_nguon_section(text: str) -> str:
    """Remove trailing NGUỒN section so we can re-append authoritative sources."""
    body = (text or "").rstrip()
    m = re.search(_section_heading_pattern("NGUỒN", through_end=True), body)
    if not m:
        return body
    return body[: m.start()].rstrip()


def _strip_all_nguon_sections(text: str) -> str:
    """Remove every model-generated NGUỒN block before appending one canonical footer."""
    lines = (text or "").replace("\r\n", "\n").splitlines()
    out: list[str] = []
    in_sources = False
    for line in lines:
        stripped = line.strip()
        normalized = re.sub(r"^(?:\d+|[IVXLCDM]+)[.)\-:]\s*", "", stripped)
        normalized = normalized.rstrip(":").strip().casefold()
        if normalized == "nguồn":
            in_sources = True
            continue
        if in_sources and normalized in {
            heading.casefold() for heading in TREND_SECTION_HEADERS if heading != "NGUỒN"
        }:
            in_sources = False
        if not in_sources:
            out.append(line)
    body = "\n".join(out).strip()
    # Body links are duplicated in the authoritative footer and make reports
    # hard to scan. Remove URL-only and markdown-link-only lines, preserving
    # normal prose that happens to mention a URL inline.
    body = re.sub(r"(?im)^\s*(?:[•\-]\s*)?https?://\S+\s*$", "", body)
    body = re.sub(
        r"(?im)^\s*(?:[•\-]\s*)?\[[^\]]+\]\(https?://[^)]+\)\s*$",
        "",
        body,
    )
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _nguon_has_real_source_urls(text: str, sources: list[dict[str, str]]) -> bool:
    """True when NGUỒN lists ≥2 https URLs that match collected Wire/web sources."""
    m = re.search(_section_heading_pattern("NGUỒN", through_end=True), text or "")
    if not m:
        return False
    section = m.group(0) or ""
    urls = re.findall(r"https?://[^\s\)\]\>\"']+", section, flags=re.I)
    if len(urls) < 2:
        return False
    known = {
        (s.get("url") or "").strip().casefold()
        for s in (sources or [])
        if (s.get("url") or "").startswith("http")
    }
    if not known:
        return len(urls) >= 2
    matched = sum(1 for u in urls if u.rstrip(".,;:!?)").casefold() in known)
    return matched >= 2


def _ensure_sources_footer(text: str, sources: list[dict[str, str]]) -> str:
    """Guarantee a NGUỒN section with real Wire article markdown links.

    Replaces empty/fake NGUỒN blocks (titles only, hallucinated domains, etc.).
    """
    body = _strip_all_nguon_sections(text)
    nguon_lines = _format_nguon_lines(sources, limit=_source_list_cap())
    if not nguon_lines:
        return "\n".join([body, "", "NGUỒN", "• (chưa có URL nguồn trong hồ sơ)"]).strip()
    return "\n".join([body, "", "NGUỒN", *nguon_lines]).strip()


def _dossier_fallback_vi(
    *,
    focus: str,
    dossier: str,
    sources: list[dict[str, str]] | None = None,
) -> str:
    """Prefer digest/draft body when Groq polish is unavailable — never the local stub."""
    draft = _extract_section_after(
        dossier,
        markers=(
            "=== WIGOLO DRAFT REPORT",
            "=== PER-ARTICLE DIGESTS",
        ),
    )

    if len(draft) >= 280:
        body = draft
        if not re.search(r"(?im)^TIÊU ĐỀ\b|^DRAFT TITLE\b|^TITLE\b", body):
            body = (
                f"TIÊU ĐỀ\nBáo cáo theo chủ đề: {focus}\n\n"
                f"TỔNG QUAN\nBản nháp đã thu thập (LLM chỉnh văn tạm chưa sẵn sàng).\n\n"
                f"{body}"
            )
        return _ensure_sources_footer(body, sources or [])

    lines = [
        "TIÊU ĐỀ",
        f"Báo cáo theo chủ đề: {focus}",
        "",
        "TỔNG QUAN",
        "LLM chỉnh văn chưa sẵn sàng. Dưới đây là nội dung đã crawl/lọc từ Dòng tin "
        "và web (~30 ngày).",
        "",
        "NỘI DUNG TỪ DÒNG TIN / THÔNG TIN KHÁC",
    ]
    bullets = 0
    for raw in (dossier or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("===") or s.startswith("FOCUS:") or s.startswith("SCOPE:"):
            continue
        if s.startswith("(") and s.endswith(")"):
            continue
        if (
            re.match(r"^\d+\)", s)
            or s.startswith("body:")
            or s.startswith("wire_summary:")
            or s.startswith("url:")
            or s.startswith("excerpt:")
            or s.startswith("•")
            or s.startswith("- ")
        ):
            lines.append(f"• {s[:500].lstrip('•').strip()}")
            bullets += 1
        if bullets >= 36:
            break
    if bullets == 0:
        lines.append("• (chưa có đủ nội dung — thử lại khi Groq/Ollama sẵn sàng)")
    lines.extend(["", "NGUỒN"])
    lines.extend(
        _format_nguon_lines(sources or [], limit=_source_list_cap())
        or ["• (chưa có URL nguồn)"]
    )
    return "\n".join(lines)


def _bounded_dossier_fallback_vi(
    *,
    focus: str,
    dossier: str,
    sources: list[dict[str, str]] | None = None,
) -> str:
    """Last-resort evidence report: detailed, bounded and free of LLM error prose."""
    fallback = _dossier_fallback_vi(focus=focus, dossier=dossier, sources=sources)
    body = _strip_nguon_section(fallback)
    clean_lines = [
        line
        for line in body.splitlines()
        if not re.search(r"(?i)\bLLM\b|tạm chưa sẵn sàng|thử lại khi Groq", line)
    ]
    body = "\n".join(clean_lines).strip()
    hi = _body_max_chars()
    if len(body) > hi:
        clipped = body[:hi]
        cut = max(clipped.rfind("\n"), clipped.rfind(". "))
        if cut >= _body_min_chars():
            clipped = clipped[: cut + 1]
        body = clipped.rstrip()
    return _ensure_sources_footer(body, sources or [])


def _sources_only_fallback_vi(
    *,
    focus: str,
    sources: list[dict[str, str]],
) -> str:
    """Safe last resort built only from Vietnamese source titles and real URLs.

    This deliberately ignores article bodies, summaries and crawler fields. It
    therefore cannot leak English prose/HTML when every paid provider is down.
    """
    titles: list[str] = []
    seen: set[str] = set()
    for source in sources:
        raw = html.unescape(str(source.get("title") or ""))
        title = re.sub(r"<[^>]+>", " ", raw)
        title = " ".join(re.sub(r"[\r\n]+", " ", title).split()).strip(" •-:")
        if not title or len(title) < 18:
            continue
        latin_words = re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", title)
        if _has_english_prose_leak(title):
            continue
        if len(latin_words) >= 6 and not _VI_DIACRITIC_RE.search(title):
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        titles.append(title[:240])
        if len(titles) >= 24:
            break

    lines = [
        "TIÊU ĐỀ",
        f"Báo cáo nhanh: {focus}",
        "",
        "TỔNG QUAN",
        (
            f"Báo cáo tổng hợp {len(titles)} nội dung tiếng Việt đã được lựa chọn từ "
            "Trạm tin tức trong cửa sổ báo cáo. Do hệ thống tạo sinh chưa trả về bản "
            "phân tích đạt chuẩn, nội dung dưới đây chỉ ghi nhận các vấn đề thể hiện "
            "trực tiếp trong tiêu đề nguồn; không sử dụng phần tóm tắt tiếng Anh, "
            "không suy diễn quan hệ và không bổ sung dữ kiện ngoài nguồn."
        ),
        "",
        "SỰ KIỆN NỔI BẬT THEO NƯỚC LỚN",
    ]
    if titles:
        for index, title in enumerate(titles[:16], 1):
            lines.append(
                f"{index}) {title}. Nội dung này được ghi nhận trong Trạm tin tức; "
                "các số liệu và diễn biến chi tiết cần được đối chiếu tại bài gốc."
            )
    else:
        lines.append("Chưa có đủ tiêu đề tiếng Việt đạt điều kiện để tổng hợp an toàn.")

    lines.extend(
        [
            "",
            "QUAN HỆ / LIÊN KẾT LIÊN QUỐC GIA",
            (
                "Chưa đủ bằng chứng trong phần tiêu đề để xác lập quan hệ nhân quả "
                "hoặc liên kết trực tiếp giữa các sự kiện. Cần đối chiếu toàn văn "
                "các nguồn trước khi đưa ra kết luận."
            ),
            "",
            "NỔI BẬT THEO MẢNG",
        ]
    )
    for index, title in enumerate(titles[16:22], 1):
        lines.append(f"{index}) {title}.")
    if len(titles) <= 16:
        lines.append("Chưa đủ bằng chứng để phân nhóm nội dung theo từng lĩnh vực.")
    lines.extend(
        [
            "",
            "THÔNG TIN LIÊN QUAN KHÁC",
        ]
    )
    for index, title in enumerate(titles[22:24], 1):
        lines.append(f"{index}) {title}.")
    if len(titles) <= 22:
        lines.append("Không ghi nhận thêm thông tin đủ điều kiện trong phạm vi đã chọn." )
    lines.extend(
        [
            "",
            "NHẬN ĐỊNH NGẮN",
            (
                "Các nội dung trên là danh mục thông tin đã được sàng lọc ở mức tiêu "
                "đề. Báo cáo chưa đưa ra nhận định mở rộng vì chưa có bản tổng hợp từ "
                "mô hình đạt đầy đủ yêu cầu về cấu trúc và ngôn ngữ. Việc đánh giá tác "
                "động cần căn cứ vào toàn văn nguồn và kết quả kiểm chứng tiếp theo."
            ),
        ]
    )
    return _ensure_sources_footer("\n".join(lines), sources)


def _extract_section_after(dossier: str, *, markers: tuple[str, ...]) -> str:
    text = dossier or ""
    for marker in markers:
        if marker not in text:
            continue
        draft = (text.split(marker, 1)[-1] or "").strip()
        lines = draft.splitlines()
        if lines and lines[0].startswith("("):
            continue
        if lines and any(m.replace("=== ", "") in lines[0].upper() for m in markers):
            lines = lines[1:]
        # Stop at next === section if present.
        out: list[str] = []
        for line in lines:
            if line.startswith("===") and out:
                break
            out.append(line)
        body = "\n".join(out).strip()
        if len(body) >= 120:
            return body
    return ""



def summarize_report_text(report: str, *, focus: str = "") -> dict[str, Any]:
    """Short executive digest of an existing detailed report (Groq only)."""
    from apps.integrations.ai.briefings import normalize_briefing_prose
    from apps.integrations.ai.clients import AIProviderError, generate_briefing_text

    report = (report or "").strip()
    if len(report) < 40:
        raise AIProviderError("Báo cáo quá ngắn để tóm tắt")
    focus = " ".join((focus or "").split()).strip() or "báo cáo hiện có"
    clipped = report[:10000]
    prompt = f"""
Tóm tắt ngắn nội dung chính của báo cáo chi tiết dưới đây bằng tiếng Việt.
Chủ đề: {focus}

QUY TẮC:
- Chỉ 1 mục TÓM TẮT NỘI DUNG CHÍNH (5–8 câu hoặc 5–8 gạch đầu dòng •).
- Mỗi ý: sự kiện cụ thể đã có trong báo cáo (ai/cái gì/khi nào/ở đâu); không suy đoán.
- CẤM câu sáo rỗng / phân tích không có trong báo cáo gốc.
- Giữ các URL quan trọng nếu có (https://...) để kiểm chứng.
- CẤM markdown (** * # và tiêu đề **1. …:**). Không viết lại toàn bộ báo cáo.

BÁO CÁO GỐC:
{clipped}
""".strip()
    result = generate_briefing_text(
        prompt,
        max_tokens=500,
        allow_wigolo_fallback=False,
        prefer_fast_model=True,
        allow_local_fallback=False,
        retry_rounds=2,
    )
    from apps.integrations.ai.clients import is_local_llm_unavailable_text

    text = normalize_briefing_prose(str(result.get("text") or ""))
    if result.get("provider") == "local" or is_local_llm_unavailable_text(text):
        raise AIProviderError("LLM tạm không khả dụng — không tạo stub local")
    if len(text) < 40:
        raise AIProviderError("Tóm tắt rỗng")
    return {
        "ok": True,
        "text": text,
        "provider": str(result.get("provider") or "groq"),
        "raw": result.get("raw") or {},
    }


def produce_quality_briefing(
    *,
    kind: str,
    keyword: str = "",
    window_hours: int = 24,
    threats: list | None = None,
    on_progress=None,
    briefing_id: int | None = None,
) -> dict[str, Any]:
    """End-to-end: focus → crawl/read → digest draft → Groq full review."""
    def _prog(msg: str, pct: int) -> None:
        if callable(on_progress):
            try:
                on_progress(msg, pct)
            except Exception:  # noqa: BLE001
                pass

    kind = (kind or "daily").strip().lower()
    window_hours = resolve_briefing_window_hours(kind, window_hours)
    window_label = _window_label(kind, window_hours)
    kw = " ".join((keyword or "").split()).strip()
    intent: dict[str, Any] | None = None
    if kind == "keyword" and kw:
        _prog("Đang làm rõ chủ đề (Groq)…", 9)
        intent = refine_keyword_intent(kw)
        focus_kw = str(intent.get("topic") or kw).strip() or kw
    else:
        focus_kw = kw

    defense_bias = not bool(focus_kw)
    _prog(f"Đang chọn tin Trạm tin tức ({window_label})…", 10)
    if threats is None:
        if intent:
            threats = select_wire_threats(
                keyword=focus_kw,
                window_hours=window_hours,
                limit=_wire_limit(),
                defense_bias=False,
                match_phrases=list(intent.get("match_phrases") or []),
                must_tokens=list(intent.get("must_tokens") or []),
                exclude_tokens=list(intent.get("exclude_tokens") or []),
                strict=True,
            )
        else:
            threats = select_wire_threats(
                keyword=focus_kw,
                window_hours=window_hours,
                limit=_wire_limit(),
                defense_bias=defense_bias,
                strict=bool(focus_kw),
            )
    titles = [
        str(getattr(t, "title_vi", "") or getattr(t, "title", "") or "").strip()
        for t in threats
    ]
    titles = [t for t in titles if t]
    focus_meta = resolve_focus(kind=kind, keyword=focus_kw, threat_titles=titles)
    if intent and intent.get("search_hint"):
        focus_meta = {
            **focus_meta,
            "search_hint": str(intent["search_hint"]),
            "focus": str(intent.get("topic") or focus_meta["focus"]),
        }
    dossier_pack = build_wire_wigolo_dossier(
        focus=focus_meta["focus"],
        threats=threats,
        search_hint=focus_meta["search_hint"],
        scope=focus_meta.get("scope") or ("keyword" if focus_kw else "general_defense"),
        kind=kind,
        window_hours=window_hours,
        on_progress=on_progress,
    )
    # Checkpoint before Groq so SoftTimeLimit can still export a report.
    if briefing_id:
        try:
            from apps.integrations.ai.briefings import checkpoint_briefing_draft

            checkpoint_briefing_draft(
                briefing_id,
                focus=focus_meta["focus"],
                raw_draft=str(dossier_pack.get("raw_draft") or ""),
                sources=dossier_pack.get("sources") or [],
                warnings=list(dossier_pack.get("warnings") or []),
                pct=80,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("checkpoint skipped: %s", exc)

    _prog("Đang tổng hợp báo cáo đa chiều (LLM)…", 88)
    polished = polish_dossier_with_groq(
        focus=focus_meta["focus"],
        dossier=dossier_pack["dossier"],
        kind=kind,
        scope=focus_meta.get("scope") or "keyword",
        sources=dossier_pack.get("sources") or [],
        window_hours=window_hours,
    )
    warnings = list(dossier_pack.get("warnings") or [])
    if not polished.get("ok"):
        err = str(
            polished.get("error")
            or (polished.get("raw") or {}).get("polish_error")
            or ""
        )
        # SoftTimeLimit during Groq: still exported draft — keep a short note only.
        if "SoftTimeLimit" in err or "time limit" in err.lower():
            warnings.append(
                "Hết thời gian khi Groq rà soát — đã xuất từ bản thô đã kiểm chứng nguồn"
            )
            _prog("⚠ Timeout Groq — đã xuất bản thô có nguồn", 92)
        elif err:
            warnings.append(f"Groq rà soát lỗi — dùng bản thô: {err[:120]}")
            _prog(f"⚠ Groq lỗi: {err[:100]}", 92)
        else:
            _prog("⚠ Groq hạn chế — xuất bản thô", 92)
    else:
        if str((polished.get("raw") or {}).get("mode") or "") == "bounded_evidence_fallback":
            _prog("Đã hoàn thiện báo cáo từ hồ sơ nguồn đã kiểm chứng", 96)
        else:
            _prog("Groq đã rà soát xong — chuẩn bị xuất báo cáo", 96)
    if warnings:
        _prog(f"Hoàn tất (có {len(warnings)} cảnh báo)", 98)
    provider = polished["provider"]
    if (
        dossier_pack["article_count"]
        or dossier_pack["research_ok"]
        or dossier_pack["search_count"]
    ):
        if "wigolo" not in provider:
            provider = f"{provider}+wigolo"
    meta: dict[str, Any] = {
        "focus": focus_meta["focus"],
        "scope": focus_meta.get("scope"),
        "window_hours": window_hours,
        "window_label": window_label,
        "wire_only": bool(dossier_pack.get("wire_only")),
        "wire_count": dossier_pack["wire_count"],
        "article_count": dossier_pack["article_count"],
        "search_count": dossier_pack["search_count"],
        "research_ok": dossier_pack["research_ok"],
        "draft_chars": dossier_pack.get("draft_chars") or 0,
        "fetch_fail": dossier_pack.get("fetch_fail") or 0,
        "queries": dossier_pack["queries"],
        "urls_fetched": dossier_pack["urls_fetched"][: _fetch_cap()],
        "sources": dossier_pack.get("sources") or [],
        "polish": polished.get("raw") or {},
        "polish_ok": polished.get("ok"),
        "warnings": warnings[:20],
        "report_kind": "wire_multidim_trend",
    }
    if intent:
        meta["keyword_intent"] = {
            "topic": intent.get("topic"),
            "search_hint": intent.get("search_hint"),
            "match_phrases": intent.get("match_phrases"),
            "must_tokens": intent.get("must_tokens"),
            "exclude_tokens": intent.get("exclude_tokens"),
            "source": intent.get("source"),
            "raw_prompt": intent.get("raw_prompt"),
        }
    return {
        "title": focus_meta["title"],
        "focus": focus_meta["focus"],
        "content": polished["text"],
        "provider": provider[:32],
        "threat_count": len(threats),
        "warnings": warnings[:20],
        "meta": meta,
    }
