"""Title translation: Google Translate first, optional AI refine."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

import httpx
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.core.text import prefer_my_for_united_states
from apps.intel.models import ScannedDocument, Threat

logger = logging.getLogger(__name__)

_GOOGLE_CIRCUIT_CACHE_KEY = "title_translate:google_circuit_open"
_GROQ_CIRCUIT_CACHE_KEY = "title_translate:groq_circuit_open"
_GROQ_FAIL_COUNT_CACHE_KEY = "title_translate:groq_fail_count"
_GOOGLE_RPM_CACHE_KEY = "title_translate:google_rpm"
_GOOGLE_LAST_CALL_CACHE_KEY = "title_translate:google_last_call"


def wait_for_google_budget(*, block: bool = True) -> bool:
    """Shared Google Translate RPM / spacing — unofficial endpoint is fragile."""
    min_interval = max(
        0.0, float(getattr(settings, "GOOGLE_TRANSLATE_MIN_INTERVAL_SEC", 3.0) or 0.0)
    )
    max_rpm = max(
        1, int(getattr(settings, "GOOGLE_TRANSLATE_MAX_REQUESTS_PER_MIN", 6) or 6)
    )
    try:
        from django.core.cache import cache
    except Exception:  # noqa: BLE001
        if min_interval:
            time.sleep(min_interval)
        return True

    while True:
        try:
            count = cache.get(_GOOGLE_RPM_CACHE_KEY)
            if count is None:
                cache.set(_GOOGLE_RPM_CACHE_KEY, 0, timeout=60)
                count = 0
            if int(count) >= max_rpm:
                if not block:
                    return False
                time.sleep(3.0)
                continue
            last = cache.get(_GOOGLE_LAST_CALL_CACHE_KEY)
            now = time.time()
            if last is not None and min_interval:
                wait = min_interval - (now - float(last))
                if wait > 0:
                    if not block:
                        return False
                    time.sleep(min(wait, 5.0))
                    continue
            cache.set(_GOOGLE_LAST_CALL_CACHE_KEY, now, timeout=120)
            try:
                cache.incr(_GOOGLE_RPM_CACHE_KEY)
            except ValueError:
                cache.set(_GOOGLE_RPM_CACHE_KEY, 1, timeout=60)
            return True
        except Exception:  # noqa: BLE001
            if min_interval:
                time.sleep(min_interval)
            return True


def is_google_circuit_open() -> bool:
    """True while Google Translate is temporarily blocked for this host."""
    try:
        from django.core.cache import cache

        return bool(cache.get(_GOOGLE_CIRCUIT_CACHE_KEY))
    except Exception:  # noqa: BLE001
        return False


def trip_google_circuit(*, reason: str = "") -> None:
    """Skip Google for a cool-down window; Ollama fallback continues translating."""
    ttl = max(
        60,
        int(getattr(settings, "GOOGLE_TRANSLATE_CIRCUIT_TTL_SEC", 900) or 900),
    )
    try:
        from django.core.cache import cache

        cache.set(_GOOGLE_CIRCUIT_CACHE_KEY, True, timeout=ttl)
    except Exception:  # noqa: BLE001
        logger.debug("google circuit cache unavailable", exc_info=True)
    logger.warning(
        "google translate circuit open for %ss%s",
        ttl,
        f" ({reason})" if reason else "",
    )


def clear_google_circuit() -> None:
    try:
        from django.core.cache import cache

        cache.delete(_GOOGLE_CIRCUIT_CACHE_KEY)
    except Exception:  # noqa: BLE001
        return


def is_groq_circuit_open() -> bool:
    """Legacy helper — global Groq circuit is disabled (per-item 15m stuck instead)."""
    return False


def trip_groq_circuit(*, reason: str = "") -> None:
    """No-op: do not divert the whole pipeline off Groq after brief failures."""
    if reason:
        logger.info("groq soft-fail (no global circuit): %s", reason)


def clear_groq_circuit() -> None:
    try:
        from django.core.cache import cache

        cache.delete(_GROQ_CIRCUIT_CACHE_KEY)
        cache.delete(_GROQ_FAIL_COUNT_CACHE_KEY)
    except Exception:  # noqa: BLE001
        return


def note_groq_success() -> None:
    """Groq succeeded — clear any leftover fail counters."""
    clear_groq_circuit()


def note_groq_failure(*, reason: str = "") -> None:
    """Log Groq failure only — keep retrying Groq until per-item stuck (15m)."""
    logger.info("groq translate miss%s", f": {reason}" if reason else "")


def _is_google_block_error(exc: BaseException | str) -> bool:
    text = str(exc or "").casefold()
    return any(
        marker in text
        for marker in (
            "blocked",
            "sorry",
            "http 302",
            "http 429",
            "non-json",
            "rate",
        )
    )


# Only exact structured ransomware ingest titles use a local rule.
_RANSOMWARE_TITLE_RE = re.compile(
    r"^Ransomware:\s*(?P<victim>.+?)\s*\((?P<group>[^)]+)\)\s*$",
    re.IGNORECASE,
)

_VIET_CHAR_RE = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    r"ùúụủũưừứựửữỳýỵỷỹđ"
    r"ÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠ"
    r"ÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]"
)

_VIETNAMESE_SIGNAL_WORDS = {
    "các",
    "cho",
    "của",
    "đã",
    "đang",
    "được",
    "hải",
    "khi",
    "một",
    "phòng",
    "quân",
    "quốc",
    "sau",
    "sự",
    "tại",
    "tập",
    "trận",
    "trên",
    "trong",
    "trung",
    "từ",
    "và",
    "về",
    "với",
}

# Latin words (3+ letters) — proper nouns/CVE kept but counted for English remnants.
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{3,}\b")

# Obvious garble: "Cơ quotate:" style prefix glued to an English headline.
_CO_ENGLISH_GARBLE_RE = re.compile(
    r"\b[Cc]ơ\s+(?:quot\w+|confirm\w*|agency\w*|registry\w*)\b"
    r"|\b[Cc]ơ\s+[a-z]{4,}\s*:\s*[A-Z]",
    re.IGNORECASE,
)

# Long contiguous English phrase inside a "Vietnamese" title.
# Require Title Case English words so unaccented VI ("trong khi") is not flagged.
_LONG_ENGLISH_RUN_RE = re.compile(
    r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){2,}\b"
)

# Residual English headline fragments (Title Case chains), e.g. "National Land Registry".
_ENGLISH_HEADLINE_RE = re.compile(
    r"[A-Z][a-z]+(?:[''][sS])?(?:\s+[A-Z][a-z]+){2,}"
)

# Shared military/defence/security translation doctrine for Wire titles.
# Adapted from full-document military translation instructions; scoped to titles.
_MILITARY_TRANSLATION_DOCTRINE = """
## Role
You are an expert military, defence, and security translator specializing in
translating English and other languages into Vietnamese.

## Task
Translate the given news/OSINT TITLE into Vietnamese. Apply the same fidelity
rules you would use for headings in a military, defence, or intelligence document.

## Translation Requirements
1. Faithfully preserve the meaning of the original. Highest accuracy for military,
   defence, and security content.
2. Do NOT paraphrase, summarize, omit, infer, embellish, or add commentary,
   explanations, or information that is not explicitly present in the source.
3. Preserve uncertainty exactly (assess / suggest / indicate / appear / likely /
   may / might / could / estimate / believe / judge / reportedly, etc.). Never
   strengthen or weaken the author's assessment.
4. Do NOT invent countries, places, actors, or destinations. Example: do NOT add
   Việt Nam / Vietnam unless the source mentions it. "Visit" = "chuyến thăm" —
   do NOT invent a destination country.
5. Translate every meaningful word. Do not leave source-language content words
   untranslated (e.g. visit, revive, project, opportunity, strategic).
   Translate the complete headline clause: retain auxiliaries and planning verbs
   such as "plans to", "prepares to", "aims to" and their objects. Never return
   a fragment such as "Cách Nhật Bản dự" or stop at a dangling preposition.
6. Exceptions that MAY remain in the original form:
   - abbreviations, identifiers, code names, serial numbers, technical designators
   - Latin proper nouns when no confident official Vietnamese form exists
     (persons, ships, bases, systems): keep original (Modi, Sabang, PLA, NATO, CMMC…)
7. For military units, agencies, bases, ports, vessels, aircraft, missiles, radars,
   weapons, operations, doctrines, treaties, and geographic names: use the official
   Vietnamese name when known with certainty. If not confident, keep the original
   name — NEVER guess or invent.
8. Preferred terminology when applicable:
   - South China Sea / 南海 → Biển Đông
   - East China Sea → Biển Hoa Đông
   - Taiwan Strait / 台海 → Eo biển Đài Loan
   - Japan Self-Defense Forces / 自卫队 / 自衛隊 → Lực lượng Phòng vệ Nhật Bản
   - coast guard → cảnh sát biển
   - military exercise / drill → tập trận quân sự
   - defense procurement → mua sắm quốc phòng
   - force posture → bố trí lực lượng
   - cyber warfare / 网络战 → tác chiến mạng
   - opportunity → cơ hội (never "cơ hợp")
9. Use formal Vietnamese suitable for military, defence, security, intelligence,
   and government publications (văn phong hành chính). Keep legal / diplomatic /
   technical / analytical tone.
10. If the source is Chinese / Japanese / Korean: translate fully into Vietnamese;
    do not leave Han/Kanji characters in the output.

## Output Requirements
- Return ONLY the completed Vietnamese title.
- No introductions, summaries, conclusions, translator notes, quotes, or prefixes.
- One clean title line, ready to display.
""".strip()

DEFAULT_REFINE_PROMPT = f"""{_MILITARY_TRANSLATION_DOCTRINE}

## Current job
Revise the Google Translate draft of this title so it fully complies with the
doctrine above. Prefer the source meaning over a bad draft. If the draft invents
places or leaves English content words, correct them.

Source title:
{{title}}

Google Translate draft (to revise):
{{draft}}
"""

DEFAULT_FALLBACK_PROMPT = f"""{_MILITARY_TRANSLATION_DOCTRINE}

## Current job
Translate the following title into Vietnamese, strictly following the doctrine.

Source title:
{{title}}
"""

# Short prompt for small local models (3B) — long doctrine causes hallucinations.
_SHORT_EN_FALLBACK_PROMPT = """\
You translate military/defence news titles into formal Vietnamese.
Rules:
- Keep the exact meaning. Do not summarize or add facts.
- Translate the whole headline, including verbs and objects; never output a
  shortened fragment or end with an unfinished word such as "dự" or "của".
- Do not invent countries, people, or places.
- Keep proper nouns/acronyms as-is when unsure (NATO, PLA, F-35, Patriot, GAO…).
- Output ONLY one Vietnamese title line. No quotes or notes.

Title:
{title}
"""

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")

# Common small-model typos / nonsense fragments.
_VI_GARBLED_PHRASE_RE = re.compile(
    r"\bcơ\s+h[oọ]p\b"  # should be "cơ hội"
    r"|\bcơ\s+h[oọ]p\s+chi[eế]n\b",
    re.IGNORECASE,
)

# English headline content that must not remain Latin in a Vietnamese title.
_MUST_TRANSLATE_EN = frozenset(
    {
        "visit",
        "visits",
        "visited",
        "revive",
        "revives",
        "revived",
        "strategic",
        "opportunity",
        "opportunities",
        "project",
        "projects",
        "port",
        "ports",
        "matters",
        "launch",
        "launches",
        "launched",
        "military",
        "defense",
        "defence",
        "exercise",
        "exercises",
        "drill",
        "drills",
        "agreement",
        "summit",
        "talks",
        "deploy",
        "deploys",
        "deployment",
        "weapon",
        "weapons",
        "missile",
        "missiles",
        "navy",
        "army",
        "force",
        "forces",
        "war",
        "conflict",
        "plan",
        "plans",
        "planning",
        "security",
        "alliance",
        "policy",
        "report",
        "reports",
        "claim",
        "claims",
        "attack",
        "attacks",
        "cyber",
        "warfare",
        "strengthen",
        "strengthens",
        "strengthened",
        "announce",
        "announces",
        "announced",
        "develop",
        "develops",
        "development",
        "digital",
        "center",
        "centre",
        "through",
        "system",
        "systems",
    }
)

# Place groups: if any "hit" appears in the translation, original must mention a "source" alias.
_PLACE_FIDELITY_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("vietnam", "viet nam", "việt nam"),
        (
            "vietnam",
            "viet nam",
            "việt nam",
            "hanoi",
            "hà nội",
            "saigon",
            "sài gòn",
            "ho chi minh",
            "hồ chí minh",
        ),
    ),
    (
        ("trung quốc", "trung hoa"),
        ("china", "chinese", "prc", "pla", "beijing", "中国", "中國", "北京", "解放军"),
    ),
    (
        ("đài loan",),
        ("taiwan", "taiwanese", "taipei", "台灣", "台湾", "台北", "đài loan"),
    ),
    (
        ("nhật bản",),
        ("japan", "japanese", "tokyo", "jsdf", "日本", "东京", "東京", "nhật bản"),
    ),
    (
        ("philippines", "manila"),
        ("philippines", "philippine", "manila", "filipino", "菲律宾"),
    ),
    (
        ("indonesia", "jakarta"),
        ("indonesia", "indonesian", "jakarta", "印尼", "印度尼西亚"),
    ),
    (
        ("ấn độ",),
        (
            "india",
            "indian",
            "delhi",
            "modi",
            "印度",
            "印太",
            "印度太平洋",
            "印度洋",
            "indo-pacific",
            "ấn độ",
        ),
    ),
    (
        ("mỹ", "hoa kỳ"),
        (
            "united states",
            "u.s.",
            "u.s",
            "usa",
            "us",  # word-boundary matched (not substring of australia/status)
            "american",
            "pentagon",
            "washington",
            "美国",
            "美军",
            "美太空军",
            "美空军",
            "美海军",
            "美陆军",
            "美",  # Chinese abbreviated United States (美军/美方)
            "mỹ",
            "hoa kỳ",
        ),
    ),
    (
        ("úc", "australia"),
        ("australia", "australian", "canberra", "úc"),
    ),
    (
        ("ukraina", "ukraine"),
        ("ukraine", "ukrainian", "kyiv", "kiev", "ukraina"),
    ),
    (
        ("anh quốc", "quốc gia anh", "nước anh"),
        (
            "united kingdom",
            "u.k.",
            "uk",
            "britain",
            "british",
            "england",
            "london",
            "anh quốc",
        ),
    ),
)

# VI phrases that 3B models invent when they misread English (reject unless source supports).
_HALLUCINATION_PHRASE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "đoàn kết",
        ("unity", "solidarity", "cohesion", "unite", "unified", "đoàn kết"),
    ),
    (
        "đoàn thanh niên",
        ("youth", "union", "thanh niên", "communist youth"),
    ),
    (
        "đoàn thanh viên",
        ("youth", "union", "member", "members", "committee"),
    ),
    (
        "mặt trời",
        ("sun", "solar", "sunrise", "mặt trời"),
    ),
    (
        "thanh viên cung cấp",
        ("member", "members", "congress", "committee", "support"),
    ),
    (
        "đánh trạng tự",
        ("status", "state", "autonomy"),
    ),
    (
        "phương pháp đánh trạng",
        ("method", "status", "state"),
    ),
)

# Significant EN content → expected VI cues (at least one should appear if word is in source).
_CORE_TERM_HINTS: dict[str, tuple[str, ...]] = {
    "intelligence": ("tình báo", "trinh sát", "intel"),
    "support": ("hỗ trợ", "chi viện", "ủng hộ"),
    "targeting": ("mục tiêu", "nhắm"),
    "horizon": ("tầm", "horizon", "xa"),
    "contested": ("tranh chấp", "đối địch", "tranh giành", "bất ổn"),
    "environment": ("môi trường", "chiến trường"),
    "environments": ("môi trường", "chiến trường"),
    "dogfight": ("không chiến", "dogfight", "chiến đấu trên không"),
    "ukraine": ("ukraina", "ukraine"),
    "ukrainian": ("ukraina", "ukraine", "ukrainian"),
    "australia": ("australia", "úc", "australian", "canberra"),
    "philippines": ("philippines", "philippine"),
    "china": ("trung quốc", "trung", "china", "chinese"),
    "pentagon": ("pentagon", "lầu năm góc"),
    "missile": ("tên lửa", "missile"),
    "drone": ("máy bay không người lái", "uav", "drone"),
    "navy": ("hải quân", "navy"),
    "army": ("quân đội", "lục quân", "army"),
    "cyber": ("mạng", "cyber", "không gian mạng"),
    "warfare": ("tác chiến", "chiến tranh", "warfare"),
    "patrol": ("tuần tra", "patrol"),
    "general": ("tướng", "general"),
    "confirms": ("xác nhận", "confirm"),
    "historic": ("lịch sử", "historic"),
    "planning": ("lập kế hoạch", "kế hoạch", "planning"),
    "stands": ("đứng", "ủng hộ", "stand"),
}

# Missing any of these is enough to reject (countries / key military nouns).
_CRITICAL_CORE_TERMS = frozenset(
    {
        "australia",
        "ukraine",
        "ukrainian",
        "china",
        "philippines",
        "pentagon",
        "dogfight",
        "intelligence",
        "missile",
    }
)


def translation_has_hallucinated_phrases(original: str, translated: str) -> bool:
    """Reject known small-model invention patterns unsupported by the source."""
    src = _fold_for_place_match(original)
    dst = _fold_for_place_match(translated)
    if not src or not dst:
        return False
    for phrase, allowed_src in _HALLUCINATION_PHRASE_GROUPS:
        if phrase not in dst:
            continue
        if not any(alias in src for alias in allowed_src):
            return True
    return False


def translation_misses_core_terms(original: str, translated: str) -> bool:
    """
    True when several mapped English content words have no Vietnamese cue.

    Catches free-association 3B output that still 'looks Vietnamese'.
    """
    if is_cjk_title(original):
        return False
    src_words = {
        w.casefold()
        for w in re.findall(r"[A-Za-z]{4,}", original or "")
    }
    dst = _fold_for_place_match(translated)
    if not src_words or not dst:
        return False
    checked = 0
    missed = 0
    critical_missed = 0
    for word, hints in _CORE_TERM_HINTS.items():
        if word not in src_words:
            continue
        checked += 1
        if word in dst:
            continue
        if any(hint in dst for hint in hints):
            continue
        missed += 1
        if word in _CRITICAL_CORE_TERMS:
            critical_missed += 1
    if critical_missed:
        return True
    if checked < 2:
        return False
    # Fail when majority of mapped terms are missing.
    return missed >= max(2, (checked + 1) // 2)


def translation_length_implausible(original: str, translated: str) -> bool:
    """Reject wildly longer/shorter Ollama drafts vs the source title."""
    src = re.sub(r"\s+", " ", (original or "").strip())
    dst = re.sub(r"\s+", " ", (translated or "").strip())
    if not src or not dst:
        return False
    ratio = len(dst) / max(1, len(src))
    src_words = re.findall(r"[A-Za-zÀ-ỹĐđ]{2,}", src)
    dst_words = re.findall(r"[A-Za-zÀ-ỹĐđ]{2,}", dst)
    # A long source reduced to three or four words is usually a truncated
    # machine draft, even when it contains Vietnamese diacritics.
    if len(src_words) >= 6 and len(dst_words) < max(5, int(len(src_words) * 0.45)):
        return True
    if re.search(
        r"\b(?:dự|của|về|với|cho|từ|tại|trong|để|nhằm|theo|đang|sẽ)$",
        dst.casefold(),
    ) and len(dst_words) < 7:
        return True
    # CJK titles pack meaning into fewer glyphs; Vietnamese expansion of 2–5× is normal.
    if is_cjk_title(original):
        return ratio > 5.5 or ratio < 0.45
    # Character length ratio (VI often ~0.8–1.6× EN for titles).
    if ratio > 2.4 or ratio < 0.35:
        return True
    return False


class TitleTranslateError(Exception):
    pass


def title_hash(title: str) -> str:
    normalized = " ".join((title or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def cjk_char_ratio(text: str) -> float:
    chars = [ch for ch in (text or "") if not ch.isspace()]
    if not chars:
        return 0.0
    return sum(1 for ch in chars if _CJK_RE.match(ch)) / len(chars)


def is_cjk_title(text: str) -> bool:
    """True when the source title is primarily Chinese / Japanese / Korean script."""
    return cjk_char_ratio(text) >= 0.25


def translation_still_cjk(original: str, translated: str) -> bool:
    """True when CJK remains where it should not (failed CN/JP translate, or invented glyphs)."""
    text = translated or ""
    if not text:
        return False
    # English/Latin sources must not gain Chinese/Japanese characters.
    if not is_cjk_title(original):
        return bool(_CJK_RE.search(text))
    if _CJK_RE.search(text):
        return True
    return cjk_char_ratio(text) >= 0.08


def looks_vietnamese(text: str) -> bool:
    if not text or not text.strip():
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    accented = sum(1 for ch in letters if _VIET_CHAR_RE.match(ch))
    if not accented:
        return False
    ratio = accented / len(letters)
    words = {
        word.casefold()
        for word in re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    }
    return ratio >= 0.20 or bool(words & _VIETNAMESE_SIGNAL_WORDS)


def vietnamese_ratio(text: str) -> float:
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return 0.0
    viet = sum(1 for ch in letters if _VIET_CHAR_RE.match(ch))
    return viet / len(letters)


def english_word_count(text: str) -> int:
    return len(_LATIN_WORD_RE.findall(text or ""))


def _fold_for_place_match(text: str) -> str:
    return " ".join((text or "").casefold().split())


def _place_alias_in_text(alias: str, text: str) -> bool:
    """
    Match place aliases in folded text.

    Short / dotted tokens (us, uk, u.s.) use word boundaries so they do not
    match inside australia/ukraine/status/etc.
    """
    alias = (alias or "").casefold().strip()
    text = text or ""
    if not alias or not text:
        return False
    if len(alias) <= 3 or alias in {"u.s.", "u.k.", "u.s", "u.k"}:
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
                text,
            )
        )
    return alias in text


def translation_invents_places(original: str, translated: str) -> bool:
    """True when the Vietnamese title invents countries/places absent from the source."""
    src = _fold_for_place_match(original)
    dst = _fold_for_place_match(translated)
    if not src or not dst:
        return False
    for hits, sources in _PLACE_FIDELITY_GROUPS:
        # Use the same alias matcher for hits — short tokens like "úc" must not
        # match inside "được" after diacritic folding.
        if any(_place_alias_in_text(hit, dst) for hit in hits) and not any(
            _place_alias_in_text(src_alias, src) for src_alias in sources
        ):
            return True
    return False


def has_untranslated_english_content(original: str, translated: str) -> bool:
    """True when key English content words from the source remain Latin in the VI title."""
    if is_cjk_title(original):
        return False
    src = _fold_for_place_match(original)
    dst = translated or ""
    for word in _MUST_TRANSLATE_EN:
        if word not in src:
            continue
        if re.search(rf"\b{re.escape(word)}\b", dst, flags=re.IGNORECASE):
            return True
    return False


def has_obvious_garble(text: str) -> bool:
    if not text:
        return False
    if _CO_ENGLISH_GARBLE_RE.search(text):
        return True
    if _LONG_ENGLISH_RUN_RE.search(text):
        return True
    if _VI_GARBLED_PHRASE_RE.search(text):
        return True
    return False


def is_mangled_title_vi(
    title_vi: str, *, provider: str = "", original: str = ""
) -> bool:
    """Detect broken translations (mixed EN/VI garble, invented places, leftovers)."""
    text = (title_vi or "").strip()
    if not text:
        return False
    if has_obvious_garble(text):
        return True
    if original and translation_still_cjk(original, text):
        return True
    if original and translation_invents_places(original, text):
        return True
    if original and translation_length_implausible(original, text):
        return True
    # Stricter fidelity checks for local-model output (3B invents fluent nonsense).
    provider_l = str(provider or "").casefold()
    ollama_like = (
        not provider_l
        or provider_l.startswith("ollama")
        or "ollama" in provider_l
        or provider_l.startswith("awaiting_google:ollama")
        or provider_l.startswith("groq")
    )
    if ollama_like and original:
        if translation_has_hallucinated_phrases(original, text):
            return True
        if translation_misses_core_terms(original, text):
            return True
        if translation_length_implausible(original, text):
            return True
    if original and has_untranslated_english_content(original, text):
        return True
    if cjk_char_ratio(text) >= 0.35 and not looks_vietnamese(text):
        return True
    if not looks_vietnamese(text) and english_word_count(text) >= 3:
        return True
    if _ENGLISH_HEADLINE_RE.search(text):
        return True
    if str(provider).startswith("google+ollama") and _LONG_ENGLISH_RUN_RE.search(text):
        return True
    return False


def accept_ollama_translation(original: str, translated: str) -> bool:
    """Validate Ollama output; reject place hallucinations and CJK leftovers."""
    text = (translated or "").strip()
    if not text:
        return False
    if translation_still_cjk(original, text):
        return False
    if translation_invents_places(original, text):
        return False
    if translation_has_hallucinated_phrases(original, text):
        return False
    if translation_misses_core_terms(original, text):
        return False
    if translation_length_implausible(original, text):
        return False
    if has_untranslated_english_content(original, text):
        return False
    if is_mangled_title_vi(text, provider="ollama-fallback", original=original):
        return False
    if looks_vietnamese(text):
        return True
    if is_cjk_title(original) and vietnamese_ratio(text) >= 0.08:
        words = {
            word.casefold()
            for word in re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
        }
        return bool(words & _VIETNAMESE_SIGNAL_WORDS)
    return False


def accept_groq_translation(original: str, translated: str) -> bool:
    """
    Validate Groq output. Slightly softer than Ollama: 70B rarely free-associates,
    so we skip strict core-term majority checks that over-reject good drafts.
    """
    text = (translated or "").strip()
    if not text:
        return False
    if translation_still_cjk(original, text):
        return False
    if translation_invents_places(original, text):
        return False
    if translation_has_hallucinated_phrases(original, text):
        return False
    if translation_length_implausible(original, text):
        return False
    if has_obvious_garble(text):
        return False
    if has_untranslated_english_content(original, text):
        return False
    if _ENGLISH_HEADLINE_RE.search(text) and english_word_count(text) >= 4:
        return False
    if looks_vietnamese(text):
        return True
    if is_cjk_title(original) and vietnamese_ratio(text) >= 0.08:
        words = {
            word.casefold()
            for word in re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
        }
        return bool(words & _VIETNAMESE_SIGNAL_WORDS)
    return False


def accept_google_translation(original: str, translated: str) -> bool:
    """Validate Google output before it becomes the title shown to users."""
    text = (translated or "").strip()
    if not text:
        return False
    if translation_still_cjk(original, text):
        return False
    if translation_invents_places(original, text):
        return False
    if translation_length_implausible(original, text):
        return False
    if has_obvious_garble(text) or has_untranslated_english_content(original, text):
        return False
    if _ENGLISH_HEADLINE_RE.search(text) and english_word_count(text) >= 4:
        return False
    return looks_vietnamese(text) or (is_cjk_title(original) and vietnamese_ratio(text) >= 0.08)


def cjk_prefer_ollama() -> bool:
    return bool(getattr(settings, "TITLE_TRANSLATE_CJK_PREFER_OLLAMA", True))


def accept_refine_result(original: str, google_draft: str, refined: str) -> bool:
    """Keep Google draft unless Ollama output is clearly better Vietnamese."""
    refined = (refined or "").strip()
    google_draft = (google_draft or "").strip()
    if not refined or not looks_vietnamese(refined):
        return False
    if has_obvious_garble(refined):
        return False
    if translation_invents_places(original, refined):
        return False
    if translation_has_hallucinated_phrases(original, refined):
        return False
    if translation_misses_core_terms(original, refined):
        return False

    refined_ratio = vietnamese_ratio(refined)
    draft_ratio = vietnamese_ratio(google_draft)
    if refined_ratio < max(0.12, draft_ratio - 0.08):
        return False

    refined_eng = english_word_count(refined)
    draft_eng = english_word_count(google_draft)
    if _ENGLISH_HEADLINE_RE.search(refined) and not _ENGLISH_HEADLINE_RE.search(
        google_draft
    ):
        return False
    if _LONG_ENGLISH_RUN_RE.search(refined) and not _LONG_ENGLISH_RUN_RE.search(
        google_draft
    ):
        return False
    if refined_eng > draft_eng + 2 and refined_eng >= 5:
        return False

    return True


def prepare_title_for_translate(title: str) -> str:
    """Strip URLs / noise so Google is cheaper and more accurate; keep meaning."""
    text = re.sub(r"https?://\S+", " ", title or "", flags=re.I)
    text = re.sub(r"\bt\.co/\S+", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return (text or (title or "").strip())[:512]


def normalize_military_translation(original: str, translated: str) -> str:
    """Normalize recurring military geography/terminology without adding facts."""
    text = re.sub(r"\s+", " ", translated or "").strip()
    if re.search(r"\bSouth China Sea\b", original or "", flags=re.IGNORECASE):
        text = re.sub(
            r"\bBiển (?:Nam Trung Quốc|Nam Trung Hoa)\b",
            "Biển Đông",
            text,
            flags=re.IGNORECASE,
        )
    if re.search(r"\bTaiwan Strait\b", original or "", flags=re.IGNORECASE):
        text = re.sub(
            r"\beo biển Đài Loan\b",
            "Eo biển Đài Loan",
            text,
            flags=re.IGNORECASE,
        )
    if re.search(r"\bEast China Sea\b", original or "", flags=re.IGNORECASE):
        text = re.sub(
            r"\bBiển (?:Đông Trung Quốc|Hoa Đông)\b",
            "Biển Hoa Đông",
            text,
            flags=re.IGNORECASE,
        )
    if re.search(
        r"\bJapan(?:ese)? Self[- ]Defense Forces?\b",
        original or "",
        flags=re.IGNORECASE,
    ):
        text = re.sub(
            r"\bLực lượng (?:Tự vệ|Phòng vệ) Nhật Bản\b",
            "Lực lượng Phòng vệ Nhật Bản",
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(
        r"\bQuân đội Giải phóng Nhân dân Trung Quốc\b",
        "Quân Giải phóng Nhân dân Trung Quốc",
        text,
        flags=re.IGNORECASE,
    )
    return prefer_my_for_united_states(text)[:512]


def rule_translate_title(title: str) -> str | None:
    """Deterministic translation only for exact ransomware templates."""
    raw = (title or "").strip()
    if not raw:
        return None
    if looks_vietnamese(raw):
        return raw
    match = _RANSOMWARE_TITLE_RE.match(raw)
    if not match:
        return None
    victim = match.group("victim").strip()
    group = match.group("group").strip()
    return f"Mã độc tống tiền: {victim} ({group})"[:512]


def is_structured_ransomware_title(title: str) -> bool:
    return bool(_RANSOMWARE_TITLE_RE.match((title or "").strip()))


def build_google_translate_client() -> httpx.Client:
    timeout_sec = float(
        getattr(settings, "GOOGLE_TRANSLATE_TIMEOUT_SEC", 20) or 20
    )
    timeout = httpx.Timeout(timeout_sec, connect=min(5.0, timeout_sec))
    return httpx.Client(
        timeout=timeout,
        # Consent / sorry pages redirect; following once lets us classify 429 HTML.
        follow_redirects=True,
        headers={
            "Accept": "application/json",
            "Accept-Language": "vi,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (compatible; NewsCrawler/1.0; +title-translation)"
            ),
        },
    )


def _google_blocked_error(response: httpx.Response) -> TitleTranslateError | None:
    """Detect Google temporary blocks (sorry / rate-limit HTML)."""
    final = str(response.url or "").casefold()
    content_type = (response.headers.get("content-type") or "").casefold()
    if "google.com/sorry" in final or response.status_code == 429:
        return TitleTranslateError(
            f"Google Translate blocked: HTTP {response.status_code}"
        )
    if response.status_code >= 400:
        return None
    if "json" not in content_type and "html" in content_type:
        return TitleTranslateError(
            f"Google Translate blocked: non-JSON HTTP {response.status_code}"
        )
    return None


def _parse_google_translation(data: Any) -> str:
    try:
        translated = "".join(
            str(segment[0] or "")
            for segment in (data[0] or [])
            if isinstance(segment, list) and segment
        )
    except (IndexError, TypeError) as exc:
        raise TitleTranslateError(
            "Google Translate returned an invalid response"
        ) from exc
    cleaned = translated.strip()
    if not cleaned:
        raise TitleTranslateError("Google Translate returned empty text")
    return cleaned


def google_translate_title(
    title: str, *, client: httpx.Client | None = None
) -> str:
    """Translate an auto-detected source language to Vietnamese."""
    text = prepare_title_for_translate(title)
    if not text:
        raise TitleTranslateError("empty title")

    if client is None:
        with build_google_translate_client() as owned_client:
            return google_translate_title(title, client=owned_client)

    if not wait_for_google_budget(block=True):
        raise TitleTranslateError("Google Translate rate budget exhausted")

    source_language = (
        getattr(settings, "GOOGLE_TRANSLATE_SOURCE_LANGUAGE", "auto") or "auto"
    ).strip()
    # Never hammer retries on 429 — unofficial endpoint bans quickly.
    max_retries = max(
        0, int(getattr(settings, "GOOGLE_TRANSLATE_MAX_RETRIES", 0) or 0)
    )
    backoff = max(
        0.0,
        float(
            getattr(settings, "GOOGLE_TRANSLATE_RETRY_BACKOFF_SEC", 1.0)
            or 0.0
        ),
    )
    last_error: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": source_language,
                    "tl": "vi",
                    "dt": "t",
                    "q": text[:512],
                },
            )
            blocked = _google_blocked_error(response)
            if blocked is not None:
                # Do not retry blocked/429 responses — trip circuit via caller.
                raise blocked
            if response.status_code == 429:
                raise TitleTranslateError(
                    f"Google Translate blocked: HTTP {response.status_code}"
                )
            if response.status_code >= 500:
                last_error = TitleTranslateError(
                    f"Google Translate HTTP {response.status_code}"
                )
                if attempt < max_retries:
                    time.sleep(backoff * (2**attempt))
                    continue
                raise last_error
            response.raise_for_status()
            data = response.json()
            translated = _parse_google_translation(data)
            return normalize_military_translation(title, translated)
        except httpx.HTTPStatusError as exc:
            raise TitleTranslateError(
                f"Google Translate failed: HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.RequestError, ValueError, TitleTranslateError) as exc:
            last_error = exc
            # Never retry rate-limit / block errors.
            msg = str(exc).casefold()
            if "429" in msg or "blocked" in msg or "sorry" in msg:
                raise
            if attempt < max_retries:
                time.sleep(backoff * (2**attempt))
                continue
    raise TitleTranslateError(f"Google Translate failed: {last_error}") from last_error


def needs_ai_refine(
    original: str,
    draft: str,
    *,
    wire_priority: int = 0,
) -> bool:
    """
    Optional Ollama polish — only for high-priority Wire titles when enabled.

    Default TITLE_TRANSLATE_AI_REFINE=false (Google-only = cheapest).
    When refine is on, skip low-priority noise to save local LLM tokens.
    """
    if not getattr(settings, "TITLE_TRANSLATE_AI_REFINE", False):
        return False
    min_pri = int(getattr(settings, "TITLE_TRANSLATE_AI_MIN_PRIORITY", 50) or 50)
    if int(wire_priority or 0) < min_pri:
        return False
    return True


def build_refine_prompt(title: str, draft: str) -> str:
    template = getattr(settings, "TITLE_TRANSLATE_REFINE_PROMPT", "") or DEFAULT_REFINE_PROMPT
    return template.replace("{title}", title.strip()).replace("{draft}", draft.strip())


def ollama_available() -> bool:
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return False
    if not getattr(settings, "OLLAMA_ENABLED", False):
        return False
    if not getattr(settings, "TITLE_TRANSLATE_AI_REFINE", False):
        return False
    base = (getattr(settings, "OLLAMA_BASE_URL", "") or "").strip()
    return bool(base)


def ollama_fallback_available() -> bool:
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return False
    if not getattr(settings, "TITLE_TRANSLATE_OLLAMA_FALLBACK", True):
        return False
    if not getattr(settings, "OLLAMA_ENABLED", False):
        return False
    return bool((getattr(settings, "OLLAMA_BASE_URL", "") or "").strip())


def groq_translate_available() -> bool:
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return False
    if not getattr(settings, "TITLE_TRANSLATE_GROQ", True):
        return False
    from apps.integrations.ai.groq_pool import groq_keys_configured

    return groq_keys_configured(pool="translate")


def groq_ready_now() -> bool:
    """True when Groq keys exist and at least one is not cooling down."""
    if not groq_translate_available():
        return False
    from apps.integrations.ai.groq_pool import groq_keys_ready

    return groq_keys_ready(pool="translate")


def prefer_groq_translate() -> bool:
    """True when Groq keys exist and preference is on (sticky — no global circuit)."""
    if not groq_translate_available():
        return False
    return bool(getattr(settings, "TITLE_TRANSLATE_PREFER_GROQ", True))


def title_translate_inline_groq_enabled() -> bool:
    """Inline Groq on ingest (off by default — Celery paces free-tier RPM)."""
    return bool(getattr(settings, "TITLE_TRANSLATE_INLINE_GROQ", False))


def title_translate_inline_enabled() -> bool:
    """Translate during ingest so Wire shows Vietnamese immediately."""
    if getattr(settings, "TITLE_TRANSLATE_INLINE", True):
        return True
    return bool(getattr(settings, "TITLE_TRANSLATE_INLINE_GOOGLE", False))


def translation_is_stuck(threat: Threat) -> bool:
    """
    Pending/failed longer than TITLE_TRANSLATE_STUCK_SEC (default 5m).

    Only then may Google → Ollama run for *this* item. Fresh failures stay on Groq.
    Uses updated_at when provider is awaiting_* (time since last attempt).
    """
    status = threat.title_vi_status
    if status not in {
        Threat.TitleViStatus.PENDING,
        Threat.TitleViStatus.FAILED,
        "",
        None,
    } and (threat.title_vi or "").strip():
        return False
    stuck_sec = max(
        60,
        int(getattr(settings, "TITLE_TRANSLATE_STUCK_SEC", 300) or 300),
    )
    provider = str(threat.title_vi_provider or "")
    if provider.startswith("awaiting_") or provider.startswith("stuck_"):
        ref = threat.updated_at or threat.created_at or timezone.now()
    else:
        ref = threat.updated_at or threat.created_at or timezone.now()
    age = (timezone.now() - ref).total_seconds()
    return age >= stuck_sec


def _defer_awaiting_groq(threat: Threat) -> dict[str, Any]:
    """
    Keep item on Groq queue without resetting the stuck clock.

    Re-saving updated_at on every Celery pass prevented TITLE_TRANSLATE_STUCK_SEC
    failover from ever firing for high-priority wire items.
    """
    provider_now = str(threat.title_vi_provider or "")
    fields = ["title_vi_status"]
    threat.title_vi_status = Threat.TitleViStatus.PENDING
    if not provider_now.startswith("awaiting_groq"):
        threat.title_vi_provider = "awaiting_groq"
        fields.append("title_vi_provider")
        # First defer starts the stuck timer.
        fields.append("updated_at")
    threat.save(update_fields=fields)
    return {
        "id": threat.id,
        "status": "pending",
        "provider": "awaiting_groq",
        "google_skipped": True,
        "groq_deferred": True,
    }


def unfinished_news_translation_qs():
    """NEWS rows still waiting on a usable title_vi (empty / null)."""
    return (
        Threat.objects.filter(source=Threat.Source.NEWS)
        .filter(Q(title_vi="") | Q(title_vi__isnull=True))
        .exclude(title_vi_status=Threat.TitleViStatus.SKIPPED)
    )


def trim_pending_translation_backlog(
    *, max_pending: int | None = None
) -> dict[str, int]:
    """
    Delete unfinished NEWS translations beyond TITLE_TRANSLATE_MAX_PENDING.

    Keeps the highest wire_relevant / wire_priority / newest rows so Dòng tin
    never accumulates a large awaiting_groq pile.
    """
    if max_pending is None:
        max_pending = int(
            getattr(settings, "TITLE_TRANSLATE_MAX_PENDING", 10) or 10
        )
    max_pending = max(0, int(max_pending))
    qs = unfinished_news_translation_qs()
    total = qs.count()
    if max_pending == 0:
        deleted, _ = qs.delete()
        return {"deleted": int(deleted), "kept": 0, "before": total}
    if total <= max_pending:
        return {"deleted": 0, "kept": total, "before": total}
    keep_ids = list(
        qs.order_by(
            "-wire_relevant",
            "-wire_priority",
            "-published_at",
            "-id",
        ).values_list("id", flat=True)[:max_pending]
    )
    deleted, _ = qs.exclude(id__in=keep_ids).delete()
    logger.info(
        "trim_pending_translation_backlog before=%s kept=%s deleted=%s max=%s",
        total,
        len(keep_ids),
        deleted,
        max_pending,
    )
    return {"deleted": int(deleted), "kept": len(keep_ids), "before": total}


def clear_hopeless_stuck_titles(*, limit: int = 40) -> dict[str, int]:
    """
    After 2× STUCK_SEC still pending: clear from translate queue so new
    high-priority wire items are not blocked. Prefer Google (then Ollama for
    CJK); only passthrough Latin as last resort so Dòng tin is not blocked.
    """
    stuck_sec = max(
        60, int(getattr(settings, "TITLE_TRANSLATE_STUCK_SEC", 300) or 300)
    )
    cutoff = timezone.now() - timezone.timedelta(seconds=stuck_sec * 2)
    qs = (
        Threat.objects.filter(
            Q(title_vi_status=Threat.TitleViStatus.PENDING)
            | Q(title_vi_status=Threat.TitleViStatus.FAILED)
            | Q(title_vi="")
        )
        .filter(
            Q(title_vi_provider__startswith="awaiting_")
            | Q(title_vi_provider__startswith="stuck_")
            | Q(title_vi_provider="")
        )
        .filter(updated_at__lt=cutoff)
        .order_by("-wire_relevant", "-wire_priority", "updated_at")[: max(1, limit)]
    )
    cleared = 0
    failed = 0
    google_ok = 0
    if is_google_circuit_open():
        google_client = None
    else:
        google_client = build_google_translate_client()
    try:
        for threat in qs:
            title = (threat.title or "").strip()
            if not title:
                threat.title_vi_status = Threat.TitleViStatus.FAILED
                threat.title_vi_provider = "stuck_cleared:empty"
                threat.save(
                    update_fields=["title_vi_status", "title_vi_provider", "updated_at"]
                )
                failed += 1
                continue
            # Prefer a real VI translation over English passthrough.
            if google_client is not None and not is_google_circuit_open():
                try:
                    draft = google_translate_title(title, client=google_client)
                    clear_google_circuit()
                    if not (is_cjk_title(title) and translation_still_cjk(title, draft)):
                        _persist_translation(
                            threat,
                            title_vi=draft,
                            status=Threat.TitleViStatus.OK,
                            provider="google:stuck_cleared",
                        )
                        cleared += 1
                        google_ok += 1
                        time.sleep(
                            min(
                                1.5,
                                float(
                                    getattr(settings, "TITLE_TRANSLATE_BATCH_PAUSE_SEC", 3)
                                    or 1.5
                                ),
                            )
                        )
                        continue
                except TitleTranslateError as exc:
                    if _is_google_block_error(exc):
                        trip_google_circuit(reason=str(exc)[:120])
                        google_client = None
            if is_cjk_title(title) and ollama_fallback_available():
                provider = _try_ollama_fallback(threat, title)
                if provider:
                    cleared += 1
                    continue
            if not is_cjk_title(title):
                # Latin/EN last resort: show original so Dòng tin is not blocked.
                _persist_translation(
                    threat,
                    title_vi=title[:512],
                    status=Threat.TitleViStatus.SKIPPED,
                    provider="stuck_cleared:passthrough",
                )
                cleared += 1
                continue
            threat.title_vi_status = Threat.TitleViStatus.FAILED
            threat.title_vi_provider = "stuck_cleared:timeout"
            threat.save(
                update_fields=["title_vi_status", "title_vi_provider", "updated_at"]
            )
            failed += 1
    finally:
        if google_client is not None:
            try:
                google_client.close()
            except Exception:  # noqa: BLE001
                pass
    return {"cleared": cleared, "failed": failed, "google": google_ok}


def llm_fallback_available() -> bool:
    """True when Groq and/or Ollama can translate titles."""
    return groq_translate_available() or ollama_fallback_available()


def _groq_translate_prompt(title: str) -> str:
    prepared = prepare_title_for_translate(title)
    if is_cjk_title(title):
        return (
            f"{_MILITARY_TRANSLATION_DOCTRINE}\n\n"
            "## Current job\n"
            "Translate this Chinese/Japanese/Korean defence title into formal Vietnamese. "
            "No Han/Kanji left. Output ONLY one Vietnamese title line.\n\n"
            f"Source title:\n{prepared}"
        )
    return _SHORT_EN_FALLBACK_PROMPT.replace("{title}", prepared)


def groq_translate_title(title: str) -> str:
    """Translate a title via Groq (multi-key pool; preferred over Google/Ollama)."""
    from apps.integrations.ai.groq_pool import groq_chat_completion

    model = (
        getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
        or "openai/gpt-oss-120b"
    )
    timeout = float(getattr(settings, "GROQ_TIMEOUT_SEC", 45) or 45)
    try:
        result = groq_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert military/defence translator. "
                        "Translate news titles into formal Vietnamese. "
                        "Preserve meaning exactly; do not invent countries, people, or facts. "
                        "Keep acronyms (NATO, PLA, F-35, OT) when standard. "
                        "Use natural Vietnamese military register "
                        "(e.g. cybersecurity→an ninh mạng, operational technology→công nghệ vận hành). "
                        "Reply with ONLY the Vietnamese title — no quotes, notes, or explanation."
                    ),
                },
                {"role": "user", "content": _groq_translate_prompt(title)},
            ],
            max_tokens=160 if is_cjk_title(title) else 120,
            temperature=0.1,
            model=model,
            timeout=timeout,
            # Celery may block briefly; ingest fails fast if budget exhausted.
            block_for_budget=True,
            pool="translate",
        )
    except RuntimeError as exc:
        raise TitleTranslateError(str(exc)) from exc
    text = normalize_military_translation(
        title, _clean_model_output(str(result.get("text") or ""))
    )[:512]
    if not text:
        raise TitleTranslateError("Groq returned empty text")
    if not accept_groq_translation(title, text):
        raise TitleTranslateError(f"unaccepted draft: {text[:80]}")
    return text


def ollama_translate_title(title: str) -> str:
    """Translate a title via local Ollama (last-resort fallback)."""
    base = (getattr(settings, "OLLAMA_BASE_URL", "") or "").rstrip("/")
    model = getattr(settings, "OLLAMA_TRANSLATE_MODEL", "qwen2.5:3b")
    timeout = float(getattr(settings, "OLLAMA_TIMEOUT_SEC", 90) or 90)
    # Chinese/Japanese titles need more tokens than English.
    default_predict = 128 if is_cjk_title(title) else 96
    num_predict = int(
        getattr(settings, "OLLAMA_NUM_PREDICT", default_predict) or default_predict
    )
    # EN titles: keep context small so long doctrine does not dominate 3B models.
    default_ctx = 2048 if is_cjk_title(title) else 768
    num_ctx = int(getattr(settings, "OLLAMA_NUM_CTX", default_ctx) or default_ctx)
    keep_alive = getattr(settings, "OLLAMA_KEEP_ALIVE", "10m")
    custom = (getattr(settings, "TITLE_TRANSLATE_FALLBACK_PROMPT", "") or "").strip()
    prepared = prepare_title_for_translate(title)
    if is_cjk_title(title):
        template = custom or DEFAULT_FALLBACK_PROMPT
        prompts = [template.replace("{title}", prepared)]
        # Second pass: short reminder when the first draft fails validation.
        prompts.append(
            f"{_MILITARY_TRANSLATION_DOCTRINE}\n\n"
            "## Current job\n"
            "Translate fully into Vietnamese. No Han/Kanji left. "
            "Output ONLY one Vietnamese title.\n\n"
            f"Source title:\n{prepared}"
        )
    else:
        # Prefer short EN prompt — full doctrine overwhelms qwen2.5:3b.
        template = custom or _SHORT_EN_FALLBACK_PROMPT
        prompts = [template.replace("{title}", prepared)]
        prompts.append(
            "Translate this defence news title into formal Vietnamese.\n"
            "Keep meaning; do not invent places or facts.\n"
            "Output ONLY the Vietnamese title.\n\n"
            f"{prepared}"
        )

    last_error: BaseException | None = None
    for prompt in prompts:
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": 0.05,
                "num_predict": max(48, num_predict),
                "num_ctx": max(512, num_ctx),
                "top_p": 0.9,
            },
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(f"{base}/api/generate", json=body)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            continue
        text = _clean_model_output(str(data.get("response") or ""))
        if not text:
            continue
        text = normalize_military_translation(title, text)[:512]
        if accept_ollama_translation(title, text):
            return text
        last_error = TitleTranslateError(f"unaccepted draft: {text[:80]}")
    if last_error:
        raise TitleTranslateError(f"Ollama fallback failed: {last_error}") from last_error
    raise TitleTranslateError("Ollama fallback returned empty text")


def ollama_refine_title(title: str, draft: str) -> str:
    base = (getattr(settings, "OLLAMA_BASE_URL", "") or "").rstrip("/")
    model = getattr(settings, "OLLAMA_TRANSLATE_MODEL", "qwen2.5:3b")
    timeout = float(getattr(settings, "OLLAMA_TIMEOUT_SEC", 120) or 120)
    num_predict = int(getattr(settings, "OLLAMA_NUM_PREDICT", 128) or 128)
    num_ctx = int(getattr(settings, "OLLAMA_NUM_CTX", 1024) or 1024)
    keep_alive = getattr(settings, "OLLAMA_KEEP_ALIVE", "15m")
    url = f"{base}/api/generate"
    body = {
        "model": model,
        "prompt": build_refine_prompt(title, draft),
        "stream": False,
        "keep_alive": keep_alive,
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
            "num_ctx": max(512, num_ctx),
        },
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise TitleTranslateError(f"Ollama refine failed: {exc}") from exc

    data = response.json() if response.content else {}
    if response.status_code >= 400:
        raise TitleTranslateError(
            f"Ollama HTTP {response.status_code}: {data.get('error') or data}"
        )
    text = _clean_model_output(str(data.get("response") or ""))
    if not text:
        raise TitleTranslateError("Ollama refine returned empty text")
    return text[:512]


def _clean_model_output(text: str) -> str:
    cleaned = (text or "").strip()
    if "\n" in cleaned:
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
        cleaned = lines[-1] if lines else cleaned
    cleaned = cleaned.strip().strip('"').strip("'").strip("`")
    prefixes = (
        "vietnamese:",
        "translation:",
        "title:",
        "bản dịch:",
        "tiêu đề:",
    )
    low = cleaned.casefold()
    for prefix in prefixes:
        if low.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return cleaned


def cached_translation(title: str) -> Threat | None:
    digest = title_hash(title)
    return (
        Threat.objects.filter(title_hash=digest, title_vi_status__in=["ok", "rule"])
        .exclude(title_vi="")
        .exclude(title_vi_provider="rule")  # avoid reusing old phrase-rule hybrids
        .filter(
            Q(title_vi_provider__startswith="google")
            | Q(title_vi_provider__startswith="cache:")
            | Q(title_vi_provider__startswith="ollama")
            | Q(title_vi_provider="skip_vi")
        )
        .order_by("-id")
        .only("id", "title_vi", "title_vi_status", "title_vi_provider")
        .first()
    )


def _persist_translation(
    threat: Threat,
    *,
    title_vi: str,
    status: str,
    provider: str,
) -> None:
    threat.title_vi = prefer_my_for_united_states(title_vi)[:512]
    threat.title_vi_status = status
    threat.title_vi_provider = provider[:64]
    threat.title_vi_translated_at = timezone.now()
    threat.title_hash = title_hash(threat.title or "")
    threat.save(
        update_fields=[
            "title_vi",
            "title_vi_status",
            "title_vi_provider",
            "title_vi_translated_at",
            "title_hash",
            "updated_at",
        ]
    )
    # Retag countries from original + Vietnamese title (JP/CJK feeds often
    # only become taggable after translation, e.g. "Nhật - Pháp").
    if status in {
        Threat.TitleViStatus.OK,
        Threat.TitleViStatus.RULE,
        Threat.TitleViStatus.SKIPPED,
    }:
        try:
            from apps.workers.geography import attach_threat_geography_tags

            attach_threat_geography_tags(threat)
        except Exception:
            logger.exception(
                "geography retag after translation failed threat=%s", threat.pk
            )


def apply_inline_rule_translation(
    threat: Threat,
    *,
    google_client: httpx.Client | None = None,
    skip_google: bool = False,
) -> bool:
    """Instant path: Vietnamese skip, cache, structured rule, or inline Google."""
    title = threat.title or ""
    threat.title_hash = title_hash(title)

    if looks_vietnamese(title):
        _persist_translation(
            threat,
            title_vi=title,
            status=Threat.TitleViStatus.SKIPPED,
            provider="skip_vi",
        )
        return True

    hit = cached_translation(title)
    if hit:
        _persist_translation(
            threat,
            title_vi=hit.title_vi,
            status=hit.title_vi_status,
            provider=f"cache:{hit.title_vi_provider}"[:64],
        )
        return True

    ruled = rule_translate_title(title)
    if ruled and is_structured_ransomware_title(title):
        _persist_translation(
            threat,
            title_vi=ruled,
            status=Threat.TitleViStatus.RULE,
            provider="rule",
        )
        return True

    # Inline on ingest: optional Groq only when budget is free. Default is Celery-only
    # Groq so RSS bursts do not stampede free-tier keys into a 429 storm.
    allow_inline = bool(title_translate_inline_enabled() or skip_google)
    if (
        allow_inline
        and title_translate_inline_groq_enabled()
        and prefer_groq_translate()
        and not translation_is_stuck(threat)
        and groq_ready_now()
    ):
        from apps.integrations.ai.groq_pool import groq_budget_peek

        if groq_budget_peek(pool="translate") and _try_groq_fallback(threat, title):
            return True

    groq_ready = groq_ready_now() and prefer_groq_translate()
    allow_non_groq_inline = allow_inline and (
        (not groq_ready) or translation_is_stuck(threat)
    )

    if allow_non_groq_inline:
        if (
            not skip_google
            and not is_google_circuit_open()
            and bool(
                getattr(settings, "TITLE_TRANSLATE_INLINE", True)
                or getattr(settings, "TITLE_TRANSLATE_INLINE_GOOGLE", False)
            )
        ):
            try:
                draft = google_translate_title(title, client=google_client)
                if not accept_google_translation(title, draft):
                    raise TitleTranslateError("Google returned an incomplete or mixed-language title")
                clear_google_circuit()
                if translation_still_cjk(title, draft) and _try_ollama_fallback(
                    threat, title
                ):
                    return True
                _persist_translation(
                    threat,
                    title_vi=draft,
                    status=Threat.TitleViStatus.OK,
                    provider="google",
                )
                return True
            except TitleTranslateError as exc:
                logger.info("inline google skipped threat=%s: %s", threat.id, exc)
                if _is_google_block_error(exc):
                    trip_google_circuit(reason=str(exc)[:120])
                if _try_ollama_fallback(threat, title):
                    return True
        elif _try_ollama_fallback(threat, title):
            return True

    threat.title_vi_status = Threat.TitleViStatus.PENDING
    fields = ["title_hash", "title_vi_status"]
    if groq_ready and not (threat.title_vi_provider or "").startswith("awaiting_"):
        threat.title_vi_provider = "awaiting_groq"
        fields.append("title_vi_provider")
        fields.append("updated_at")
    elif not (threat.title_vi_provider or "").startswith("awaiting_"):
        # Non-Groq pending — start/refresh timer.
        fields.append("updated_at")
    threat.save(update_fields=fields)
    return False


def _should_force_retranslate(threat: Threat) -> bool:
    """Re-run Google on old phrase-rule hybrids or mangled Ollama refines."""
    title = threat.title or ""
    provider = str(threat.title_vi_provider or "")
    if is_mangled_title_vi(
        threat.title_vi or "",
        provider=provider,
        original=title,
    ):
        return True
    # Soft/hard Ollama rejects: retry when Google circuit cools (EN + CJK).
    if provider.startswith("ollama-rejected") or provider.startswith(
        "awaiting_google"
    ):
        return True
    # Accepted-but-bad Ollama fallbacks from earlier weak validation.
    if provider.startswith("ollama-fallback") and is_mangled_title_vi(
        threat.title_vi or "", provider=provider, original=title
    ):
        return True
    if is_cjk_title(title) and translation_still_cjk(title, threat.title_vi or ""):
        return True
    if threat.title_vi_status != Threat.TitleViStatus.RULE:
        return False
    if is_structured_ransomware_title(title):
        return False
    return True


def _try_ollama_refine(
    threat: Threat,
    title: str,
    google_draft: str,
    *,
    google_only: bool = False,
) -> str | None:
    """Return provider string if refine accepted; None to keep Google draft."""
    if google_only:
        return None
    if (
        not needs_ai_refine(
            title,
            google_draft,
            wire_priority=int(threat.wire_priority or 0),
        )
        or not ollama_available()
    ):
        return None
    try:
        refined = ollama_refine_title(title, google_draft)
    except TitleTranslateError as exc:
        logger.info("ai refine skipped threat=%s: %s", threat.id, exc)
        return None
    if not accept_refine_result(title, google_draft, refined):
        logger.info(
            "ai refine rejected threat=%s: output worse than google draft",
            threat.id,
        )
        return None
    provider = f"google+ollama:{getattr(settings, 'OLLAMA_TRANSLATE_MODEL', 'qwen2.5:3b')}"
    _persist_translation(
        threat,
        title_vi=refined,
        status=Threat.TitleViStatus.OK,
        provider=provider[:64],
    )
    return provider


def _try_groq_fallback(threat: Threat, title: str) -> str | None:
    """Persist a validated Groq translation. Does not soft-reject (caller may retry)."""
    if not groq_ready_now():
        return None
    try:
        translated = groq_translate_title(title)
    except TitleTranslateError as exc:
        logger.warning("groq translate failed threat=%s: %s", threat.id, exc)
        note_groq_failure(reason=str(exc)[:120])
        return None
    note_groq_success()
    model = getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
    provider = f"groq:{model}"[:64]
    _persist_translation(
        threat,
        title_vi=translated,
        status=Threat.TitleViStatus.OK,
        provider=provider,
    )
    return provider


def _try_ai_fallback(threat: Threat, title: str) -> str | None:
    """
    Sticky Groq, then Ollama only when this item is stuck (15m+) or Groq unavailable.
    """
    if prefer_groq_translate() and not translation_is_stuck(threat):
        hit = _try_groq_fallback(threat, title)
        if hit:
            return hit
        # Not stuck yet — do not bounce to Ollama.
        return None
    # Stuck: caller already made the last Groq attempt — go straight to Ollama.
    if translation_is_stuck(threat):
        return _try_ollama_fallback(threat, title)
    if prefer_groq_translate():
        hit = _try_groq_fallback(threat, title)
        if hit:
            return hit
    return _try_ollama_fallback(threat, title)


def _try_ollama_fallback(threat: Threat, title: str) -> str | None:
    """Persist a validated Ollama translation (CJK preferred path or Google fallback)."""
    if not ollama_fallback_available():
        return None
    try:
        translated = ollama_translate_title(title)
    except TitleTranslateError as exc:
        logger.warning("ollama fallback failed threat=%s: %s", threat.id, exc)
        # Soft for EN too — hard failed + ollama-rejected used to skip forever.
        _mark_ollama_rejected(threat, reason="error", soft=True)
        return None
    if not accept_ollama_translation(title, translated):
        reason = "still_cjk" if translation_still_cjk(title, translated) else "not_vi"
        if translation_has_hallucinated_phrases(title, translated):
            reason = "hallucinated"
        elif translation_misses_core_terms(title, translated):
            reason = "core_miss"
        elif is_mangled_title_vi(
            translated, provider="ollama-fallback", original=title
        ):
            reason = "mangled"
        logger.warning(
            "ollama fallback rejected threat=%s: %s", threat.id, reason
        )
        _mark_ollama_rejected(threat, reason=reason, soft=True)
        return None
    provider = (
        f"ollama-fallback:{getattr(settings, 'OLLAMA_TRANSLATE_MODEL', 'qwen2.5:3b')}"
    )
    _persist_translation(
        threat,
        title_vi=translated,
        status=Threat.TitleViStatus.OK,
        provider=provider[:64],
    )
    return provider


def _mark_ollama_rejected(
    threat: Threat, *, reason: str, soft: bool = True
) -> None:
    """
    Mark a failed Ollama attempt.

    soft=True (default): keep pending so Google can retry when the circuit cools.
    soft=False: hard-fail (legacy); still requeued via translate_threats.
    """
    if soft:
        threat.title_vi_status = Threat.TitleViStatus.PENDING
        threat.title_vi_provider = f"awaiting_google:ollama-{reason}"[:64]
    else:
        threat.title_vi_status = Threat.TitleViStatus.FAILED
        threat.title_vi_provider = f"ollama-rejected:{reason}"[:64]
    threat.save(update_fields=["title_vi_status", "title_vi_provider", "updated_at"])


def translate_threat(
    threat: Threat,
    *,
    force: bool = False,
    google_client: httpx.Client | None = None,
    skip_google: bool = False,
    skip_groq: bool = False,
) -> dict[str, Any]:
    """Translate one threat: CJK→Ollama preferred; else Google → Ollama fallback."""
    title = threat.title or ""
    if not title.strip():
        threat.title_vi_status = Threat.TitleViStatus.SKIPPED
        threat.title_vi_provider = "empty"
        threat.save(update_fields=["title_vi_status", "title_vi_provider", "updated_at"])
        return {"id": threat.id, "status": "skipped", "provider": "empty"}

    force = force or _should_force_retranslate(threat)
    google_only = is_mangled_title_vi(
        threat.title_vi or "",
        provider=str(threat.title_vi_provider or ""),
        original=title,
    )
    # Already have Google draft — only run Ollama polish when refine is enabled.
    if (
        not force
        and threat.title_vi
        and threat.title_vi_status == Threat.TitleViStatus.OK
        and str(threat.title_vi_provider or "").startswith("google")
        and not str(threat.title_vi_provider or "").startswith("google+ollama")
    ):
        if translation_still_cjk(title, threat.title_vi):
            fallback_provider = _try_ai_fallback(threat, title)
            if fallback_provider:
                return {"id": threat.id, "status": "ok", "provider": fallback_provider}
        provider = _try_ollama_refine(
            threat, title, threat.title_vi, google_only=google_only
        )
        if provider:
            return {"id": threat.id, "status": "ok", "provider": provider}
        return {
            "id": threat.id,
            "status": threat.title_vi_status,
            "provider": threat.title_vi_provider,
            "cached": True,
        }

    if (
        not force
        and threat.title_vi
        and threat.title_vi_status
        in {
            Threat.TitleViStatus.OK,
            Threat.TitleViStatus.RULE,
            Threat.TitleViStatus.SKIPPED,
        }
    ):
        return {
            "id": threat.id,
            "status": threat.title_vi_status,
            "provider": threat.title_vi_provider,
            "cached": True,
        }

    # Sticky Groq when the pool has a ready key. If the pool is cooling, do not
    # burn more 429s — fall through to CJK Ollama / stuck Google instead.
    stuck = translation_is_stuck(threat)
    groq_preferred = prefer_groq_translate()
    groq_up = groq_preferred and not skip_groq and groq_ready_now()
    if groq_up and not stuck:
        groq_provider = _try_groq_fallback(threat, title)
        if groq_provider:
            return {
                "id": threat.id,
                "status": "ok",
                "provider": groq_provider,
                "google_skipped": True,
            }
        if not groq_ready_now():
            # Pool just exhausted — let batch skip Groq for remaining titles.
            skip_groq = True
        else:
            # Transient miss — keep pending for Celery; avoid Google stampede.
            return _defer_awaiting_groq(threat)

    groq_cooling = groq_preferred and (skip_groq or not groq_ready_now())

    # Groq preferred but cooling: translate CJK via Ollama immediately (no 15m wait).
    if groq_cooling and is_cjk_title(title) and ollama_fallback_available():
        ollama_provider = _try_ollama_fallback(threat, title)
        if ollama_provider:
            return {
                "id": threat.id,
                "status": "ok",
                "provider": ollama_provider,
                "google_skipped": True,
                "groq_skipped": True,
            }

    # EN titles while Groq is preferred-but-cooling: stay pending (no Google storm).
    if groq_cooling and not stuck and not is_cjk_title(title):
        out = _defer_awaiting_groq(threat)
        out["groq_skipped"] = True
        return out

    # Stuck (or Groq not preferred): Google → Ollama for this item only.
    # Batch may pass skip_google after a Groq deferral — that must NOT block
    # stuck-item Google failover (was stranding Dòng tin on awaiting_groq).
    google_blocked = bool(skip_google and not stuck) or is_google_circuit_open()
    prefer_cjk_ollama = (
        cjk_prefer_ollama() and is_cjk_title(title) and ollama_fallback_available()
    )
    prefer_ollama = google_blocked or prefer_cjk_ollama
    cjk_ollama_tried = False

    if groq_preferred and stuck and not skip_groq and groq_ready_now():
        # One last Groq attempt even when stuck, then fall through.
        groq_provider = _try_groq_fallback(threat, title)
        if groq_provider:
            return {
                "id": threat.id,
                "status": "ok",
                "provider": groq_provider,
                "google_skipped": True,
            }

    if prefer_cjk_ollama:
        cjk_ollama_tried = True
        fallback_provider = _try_ollama_fallback(threat, title)
        if fallback_provider:
            return {
                "id": threat.id,
                "status": "ok",
                "provider": fallback_provider,
                "google_skipped": bool(google_blocked),
            }

    if not force:
        apply_inline_rule_translation(
            threat,
            google_client=google_client,
            # When stuck, keep Google available (Google → Ollama). Do not skip it.
            skip_google=bool(google_blocked)
            or bool(prefer_ollama and not prefer_cjk_ollama and not stuck),
        )
        threat.refresh_from_db(
            fields=["title_vi", "title_vi_status", "title_vi_provider", "title_hash"]
        )
        if threat.title_vi and threat.title_vi_status != Threat.TitleViStatus.PENDING:
            if translation_still_cjk(title, threat.title_vi):
                fallback_provider = _try_ai_fallback(threat, title)
                if fallback_provider:
                    return {
                        "id": threat.id,
                        "status": "ok",
                        "provider": fallback_provider,
                    }
            if (
                str(threat.title_vi_provider or "").startswith("google")
                and not str(threat.title_vi_provider or "").startswith("google+ollama")
            ):
                provider = _try_ollama_refine(
                    threat, title, threat.title_vi, google_only=google_only
                )
                if provider:
                    return {"id": threat.id, "status": "ok", "provider": provider}
            result = {
                "id": threat.id,
                "status": threat.title_vi_status,
                "provider": threat.title_vi_provider,
            }
            if prefer_ollama and str(threat.title_vi_provider or "").startswith(
                "ollama-fallback"
            ):
                result["google_skipped"] = True
            return result

    if prefer_ollama or cjk_ollama_tried:
        if not cjk_ollama_tried:
            already = str(threat.title_vi_provider or "")
            # Skip only when apply_inline soft-rejected in this same call (not force).
            if force or not already.startswith("awaiting_google:ollama"):
                fallback_provider = _try_ai_fallback(threat, title)
                if fallback_provider:
                    return {
                        "id": threat.id,
                        "status": "ok",
                        "provider": fallback_provider,
                        "google_skipped": True,
                    }
        threat.refresh_from_db(
            fields=["title_vi_status", "title_vi_provider"]
        )
        if str(threat.title_vi_provider or "").startswith("ollama-rejected"):
            return {
                "id": threat.id,
                "status": "failed",
                "provider": threat.title_vi_provider,
                "google_skipped": True,
            }
        # Keep pending so Google can retry after the circuit cools down.
        # Preserve soft-reject reason when Ollama already marked awaiting_google:*.
        provider_now = str(threat.title_vi_provider or "")
        if not provider_now.startswith("awaiting_google"):
            threat.title_vi_provider = "awaiting_google"
            provider_now = "awaiting_google"
        threat.title_vi_status = Threat.TitleViStatus.PENDING
        threat.save(
            update_fields=["title_vi_status", "title_vi_provider", "updated_at"]
        )
        return {
            "id": threat.id,
            "status": "pending",
            "provider": provider_now,
            "google_skipped": True,
        }

    # Stuck EN path: always allow Google here even if batch passed skip_google
    # after a Groq deferral (google_blocked already accounts for stuck).
    if google_blocked:
        fallback_provider = _try_ai_fallback(threat, title)
        if fallback_provider:
            return {
                "id": threat.id,
                "status": "ok",
                "provider": fallback_provider,
                "google_skipped": True,
            }
        threat.title_vi_status = Threat.TitleViStatus.PENDING
        if not str(threat.title_vi_provider or "").startswith("awaiting_"):
            threat.title_vi_provider = "awaiting_google"
        threat.save(
            update_fields=["title_vi_status", "title_vi_provider", "updated_at"]
        )
        return {
            "id": threat.id,
            "status": "pending",
            "provider": threat.title_vi_provider,
            "google_skipped": True,
        }

    try:
        draft = google_translate_title(title, client=google_client)
        clear_google_circuit()
    except TitleTranslateError as exc:
        logger.warning("google translate failed threat=%s: %s", threat.id, exc)
        if _is_google_block_error(exc):
            trip_google_circuit(reason=str(exc)[:120])
        fallback_provider = _try_ai_fallback(threat, title)
        if fallback_provider:
            return {
                "id": threat.id,
                "status": "ok",
                "provider": fallback_provider,
            }
        threat.refresh_from_db(fields=["title_vi_status", "title_vi_provider"])
        if str(threat.title_vi_provider or "").startswith("ollama-rejected"):
            return {
                "id": threat.id,
                "status": "failed",
                "provider": threat.title_vi_provider,
            }
        provider_now = str(threat.title_vi_provider or "")
        if not provider_now.startswith("awaiting_google"):
            threat.title_vi_provider = "awaiting_google"
            provider_now = "awaiting_google"
        threat.title_vi_status = Threat.TitleViStatus.PENDING
        threat.save(
            update_fields=["title_vi_status", "title_vi_provider", "updated_at"]
        )
        return {"id": threat.id, "status": "pending", "provider": provider_now}

    # Google left Chinese/Japanese mostly untranslated → Ollama.
    if translation_still_cjk(title, draft):
        fallback_provider = _try_ai_fallback(threat, title)
        if fallback_provider:
            return {"id": threat.id, "status": "ok", "provider": fallback_provider}

    # Persist Google immediately so UI can show Vietnamese without waiting for Ollama.
    _persist_translation(
        threat,
        title_vi=draft,
        status=Threat.TitleViStatus.OK,
        provider="google",
    )

    provider = "google"
    refined_provider = _try_ollama_refine(
        threat, title, draft, google_only=google_only
    )
    if refined_provider:
        provider = refined_provider

    return {"id": threat.id, "status": "ok", "provider": provider}


def translate_threats(
    threat_ids: list[int] | None = None,
    *,
    limit: int = 40,
    force: bool = False,
) -> dict[str, Any]:
    """Process pending / bad-rule titles via Groq (paced) + stuck failover."""
    from django.db.models import Case, IntegerField, Value, When

    stats: dict[str, Any] = {
        "processed": 0,
        "ok": 0,
        "rule": 0,
        "failed": 0,
        "skipped": 0,
        "cached": 0,
        "pending": 0,
    }
    if not threat_ids and not force:
        # Safety valve only — stuck Google failover should drain most backlog.
        # Do NOT auto-delete unfinished Threat rows here (that belongs to the
        # explicit cleanup_untranslated_wire management command).
        cleared = clear_hopeless_stuck_titles(limit=min(8, max(3, limit // 2)))
        if cleared.get("cleared") or cleared.get("failed") or cleared.get("google"):
            stats["hopeless_cleared"] = cleared.get("cleared", 0)
            stats["hopeless_failed"] = cleared.get("failed", 0)
            stats["hopeless_google"] = cleared.get("google", 0)

    qs = Threat.objects.all().order_by("-wire_priority", "-published_at", "-id")
    if threat_ids:
        qs = qs.filter(id__in=threat_ids)
    elif not force:
        # Unfinished work only. Do NOT pull successful groq:/google OK rows into the
        # candidate window — they used to crowd out the real pending backlog.
        qs = qs.filter(
            Q(title_vi_status=Threat.TitleViStatus.PENDING)
            | Q(title_vi_status=Threat.TitleViStatus.FAILED)
            | Q(title_vi="")
            | Q(title_vi_provider__startswith="awaiting_google")
            | Q(title_vi_provider__startswith="awaiting_groq")
            | Q(title_vi_provider__startswith="ollama-fallback")
            | Q(title_vi_provider__startswith="ollama-rejected")
            | Q(title_vi_provider__startswith="google+ollama")
            | (
                Q(title_vi_status=Threat.TitleViStatus.RULE)
                & ~Q(title__istartswith="Ransomware:")
            )
        ).exclude(title_vi_status=Threat.TitleViStatus.SKIPPED)
        # Pending/empty first, then failed/rule — keeps Dòng tin filling.
        qs = qs.annotate(
            _translate_rank=Case(
                When(title_vi_status=Threat.TitleViStatus.PENDING, then=Value(0)),
                When(title_vi="", then=Value(0)),
                When(title_vi_status=Threat.TitleViStatus.FAILED, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by(
            "_translate_rank",
            "-wire_relevant",
            "-wire_priority",
            "-published_at",
            "-id",
        )
    # Pull a wider candidate window then re-rank: wire_relevant + SecRSS/CJK first.
    rows = list(qs[: max(1, limit * 6)])
    pool_cooling = not groq_ready_now()
    # Prefer SecRSS / CJK backlog so Chinese analysis is not stuck behind EN titles.
    # When Groq is cooling, promote stuck items so Google failover can drain the queue.
    rows.sort(
        key=lambda threat: (
            0 if (pool_cooling and translation_is_stuck(threat)) else 1,
            0 if bool(getattr(threat, "wire_relevant", False)) else 1,
            0 if "secrss.com" in str(threat.source_url or "").casefold() else 1,
            0 if is_cjk_title(threat.title or "") else 1,
            -int(threat.wire_priority or 0),
            -(threat.published_at.timestamp() if threat.published_at else 0),
            -int(threat.id or 0),
        )
    )
    selected: list[Threat] = []
    for threat in rows:
        provider = str(threat.title_vi_provider or "")
        if threat_ids:
            selected.append(threat)
        elif force:
            selected.append(threat)
        elif _should_force_retranslate(threat):
            selected.append(threat)
        elif is_mangled_title_vi(
            threat.title_vi or "",
            provider=provider,
            original=threat.title or "",
        ):
            selected.append(threat)
        elif threat.title_vi_status in {
            Threat.TitleViStatus.PENDING,
            Threat.TitleViStatus.FAILED,
        } or not (threat.title_vi or "").strip():
            selected.append(threat)
        # Skip already-good Ollama/Google OK rows pulled in by provider prefix.
        if len(selected) >= max(1, limit):
            break

    if not selected:
        return stats
    pause = max(
        0.0,
        float(
            getattr(settings, "TITLE_TRANSLATE_BATCH_PAUSE_SEC", 0)
            or getattr(settings, "GOOGLE_TRANSLATE_BATCH_PAUSE_SEC", 1.5)
            or 0.0
        ),
    )
    groq_pause = max(
        0.0, float(getattr(settings, "GROQ_BATCH_PAUSE_SEC", 2.0) or 0.0)
    )
    with build_google_translate_client() as google_client:
        consecutive_google_blocks = 0
        skip_google = is_google_circuit_open()
        skip_groq = not groq_ready_now()
        for index, threat in enumerate(selected):
            if not skip_groq and not groq_ready_now():
                skip_groq = True
            result = translate_threat(
                threat,
                force=force or _should_force_retranslate(threat),
                google_client=google_client,
                skip_google=skip_google,
                skip_groq=skip_groq,
            )
            stats["processed"] += 1
            status = result.get("status") or ""
            if result.get("cached"):
                stats["cached"] += 1
            if status in stats:
                stats[status] += 1
            provider = str(result.get("provider") or "")
            if result.get("groq_skipped") or (
                status == "pending"
                and provider == "awaiting_groq"
                and not groq_ready_now()
            ):
                skip_groq = True
                stats["groq_pool_cooling"] = True
            # Groq deferrals set google_skipped=True but must NOT trip Google for the
            # rest of the batch — stuck items need Google → Ollama failover.
            if result.get("groq_deferred") or provider == "awaiting_groq":
                pass
            elif result.get("google_skipped") and provider.startswith("ollama"):
                skip_google = True
                stats["circuit_open"] = True
            elif result.get("google_skipped") and provider.startswith("groq:"):
                pass
            elif result.get("google_skipped"):
                skip_google = True
                stats["circuit_open"] = True
            elif status == "pending" and provider == "awaiting_google":
                consecutive_google_blocks += 1
                if consecutive_google_blocks >= 2:
                    # Prefer Ollama for the rest of this batch instead of aborting.
                    skip_google = True
                    trip_google_circuit(reason="batch consecutive awaiting_google")
                    stats["circuit_open"] = True
            elif status == "ok" and provider.startswith("google"):
                consecutive_google_blocks = 0
                skip_google = False
            elif status == "ok" and provider.startswith("ollama-fallback"):
                # Google just failed for this item; keep preferring Ollama in-batch.
                skip_google = True
                stats.setdefault("ollama_fallback", 0)
                stats["ollama_fallback"] += 1
                if is_google_circuit_open():
                    stats["circuit_open"] = True
            elif status == "ok" and provider.startswith("groq:"):
                stats.setdefault("groq", 0)
                stats["groq"] += 1
            # Always pace between titles to stay under shared RPM budgets.
            if index < len(selected) - 1:
                if provider.startswith("groq:") and groq_pause:
                    time.sleep(groq_pause)
                elif provider.startswith("google") and pause and not skip_google:
                    time.sleep(pause)
                elif provider.startswith("ollama") and pause:
                    time.sleep(min(pause, 1.5))
                elif status == "pending" and provider == "awaiting_groq":
                    # Brief pause even on defer — avoids tight retry loops.
                    time.sleep(min(groq_pause or pause or 1.0, 2.0))
                elif pause:
                    time.sleep(min(pause, 2.0))
    return stats


def enqueue_title_translations(threat_ids: list[int]) -> None:
    """Fire-and-forget Celery enqueue; never raise into ingest path."""
    ids = [int(i) for i in threat_ids if i]
    if not ids:
        return
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return
    try:
        from apps.integrations.tasks import translate_threat_titles_task

        translate_threat_titles_task.delay(ids)
    except Exception:  # noqa: BLE001 — ingest must not fail on broker blips
        logger.exception("enqueue_title_translations failed ids=%s", ids[:5])


def _persist_document_translation(
    doc: ScannedDocument,
    *,
    title_vi: str,
    status: str,
    provider: str,
) -> None:
    doc.title_vi = prefer_my_for_united_states(title_vi)[:512]
    doc.title_vi_status = status
    doc.title_vi_provider = provider[:64]
    doc.title_vi_translated_at = timezone.now()
    doc.title_hash = title_hash(doc.title or "")
    doc.save(
        update_fields=[
            "title_vi",
            "title_vi_status",
            "title_vi_provider",
            "title_vi_translated_at",
            "title_hash",
            "updated_at",
        ]
    )


def cached_document_or_threat_translation(title: str) -> tuple[str, str, str] | None:
    """Reuse an existing Vietnamese title from Wire or Documents cache."""
    digest = title_hash(title)
    threat_hit = cached_translation(title)
    if threat_hit and (threat_hit.title_vi or "").strip():
        return (
            threat_hit.title_vi,
            threat_hit.title_vi_status,
            f"cache:{threat_hit.title_vi_provider}"[:64],
        )
    doc_hit = (
        ScannedDocument.objects.filter(
            title_hash=digest, title_vi_status__in=["ok", "rule"]
        )
        .exclude(title_vi="")
        .filter(
            Q(title_vi_provider__startswith="google")
            | Q(title_vi_provider__startswith="cache:")
            | Q(title_vi_provider__startswith="ollama")
        )
        .order_by("-id")
        .only("title_vi", "title_vi_status", "title_vi_provider")
        .first()
    )
    if doc_hit and (doc_hit.title_vi or "").strip():
        return (
            doc_hit.title_vi,
            doc_hit.title_vi_status,
            f"cache:{doc_hit.title_vi_provider}"[:64],
        )
    return None


def translate_scanned_document(
    doc: ScannedDocument,
    *,
    force: bool = False,
    google_client: httpx.Client | None = None,
    skip_google: bool = False,
) -> dict[str, Any]:
    """Translate one document title with the same Google/Ollama doctrine as Wire."""
    title = (doc.title or "").strip()
    if not title:
        doc.title_vi_status = Threat.TitleViStatus.SKIPPED
        doc.title_vi_provider = "empty"
        doc.save(update_fields=["title_vi_status", "title_vi_provider", "updated_at"])
        return {"id": doc.id, "status": "skipped", "provider": "empty"}

    doc.title_hash = title_hash(title)
    if (
        not force
        and (doc.title_vi or "").strip()
        and doc.title_vi_status
        in {
            Threat.TitleViStatus.OK,
            Threat.TitleViStatus.RULE,
            Threat.TitleViStatus.SKIPPED,
        }
    ):
        return {
            "id": doc.id,
            "status": doc.title_vi_status,
            "provider": doc.title_vi_provider,
            "cached": True,
        }

    cached = cached_document_or_threat_translation(title)
    if cached and not force:
        vi, status, provider = cached
        _persist_document_translation(
            doc, title_vi=vi, status=status, provider=provider
        )
        return {"id": doc.id, "status": status, "provider": provider, "cached": True}

    prefer_cjk_ollama = (
        cjk_prefer_ollama() and is_cjk_title(title) and llm_fallback_available()
    )
    prefer_ollama = skip_google or is_google_circuit_open() or prefer_cjk_ollama

    # Groq-first for document titles; sticky until per-doc age allows fallback.
    if prefer_groq_translate():
        try:
            draft = groq_translate_title(title)
            note_groq_success()
            provider = (
                f"groq:{getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-120b')}"
            )
            _persist_document_translation(
                doc,
                title_vi=draft,
                status=Threat.TitleViStatus.OK,
                provider=provider[:64],
            )
            return {
                "id": doc.id,
                "status": "ok",
                "provider": provider[:64],
                "google_skipped": True,
            }
        except TitleTranslateError as exc:
            logger.info("document groq failed id=%s: %s", doc.id, exc)
            note_groq_failure(reason=str(exc)[:120])
            # Not stuck yet → stay pending for Celery Groq retry.
            age = (
                timezone.now() - (doc.created_at or timezone.now())
            ).total_seconds()
            stuck_sec = max(
                60, int(getattr(settings, "TITLE_TRANSLATE_STUCK_SEC", 900) or 900)
            )
            if age < stuck_sec and not skip_google:
                doc.title_vi_status = Threat.TitleViStatus.PENDING
                doc.title_vi_provider = "awaiting_groq"
                doc.save(
                    update_fields=[
                        "title_hash",
                        "title_vi_status",
                        "title_vi_provider",
                        "updated_at",
                    ]
                )
                return {
                    "id": doc.id,
                    "status": "pending",
                    "provider": "awaiting_groq",
                    "google_skipped": True,
                }

    if prefer_cjk_ollama or prefer_ollama:
        try:
            if groq_translate_available():
                draft = groq_translate_title(title)
                provider = f"groq:{getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-120b')}"
            else:
                draft = ollama_translate_title(title)
                provider = "ollama-fallback"
            if draft and not is_mangled_title_vi(
                draft, provider=provider, original=title
            ):
                if not (is_cjk_title(title) and translation_still_cjk(title, draft)):
                    _persist_document_translation(
                        doc,
                        title_vi=draft,
                        status=Threat.TitleViStatus.OK,
                        provider=provider[:64],
                    )
                    return {
                        "id": doc.id,
                        "status": "ok",
                        "provider": provider[:64],
                        "google_skipped": True,
                    }
        except TitleTranslateError as exc:
            logger.info("document llm failed id=%s: %s", doc.id, exc)
            if groq_translate_available() and ollama_fallback_available():
                try:
                    draft = ollama_translate_title(title)
                    if draft and not is_mangled_title_vi(
                        draft, provider="ollama-fallback", original=title
                    ):
                        _persist_document_translation(
                            doc,
                            title_vi=draft,
                            status=Threat.TitleViStatus.OK,
                            provider="ollama-fallback",
                        )
                        return {
                            "id": doc.id,
                            "status": "ok",
                            "provider": "ollama-fallback",
                            "google_skipped": True,
                        }
                except TitleTranslateError as exc2:
                    logger.info("document ollama failed id=%s: %s", doc.id, exc2)

    if not prefer_ollama or prefer_cjk_ollama:
        # Still try Google when CJK Ollama failed or Google is available.
        pass

    if not skip_google and not is_google_circuit_open():
        try:
            draft = google_translate_title(title, client=google_client)
            clear_google_circuit()
            if is_cjk_title(title) and translation_still_cjk(title, draft):
                try:
                    if groq_translate_available():
                        llm_draft = groq_translate_title(title)
                        llm_provider = f"groq:{getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-120b')}"
                    else:
                        llm_draft = ollama_translate_title(title)
                        llm_provider = "ollama-fallback"
                    if llm_draft and not translation_still_cjk(title, llm_draft):
                        _persist_document_translation(
                            doc,
                            title_vi=llm_draft,
                            status=Threat.TitleViStatus.OK,
                            provider=llm_provider[:64],
                        )
                        return {
                            "id": doc.id,
                            "status": "ok",
                            "provider": llm_provider[:64],
                        }
                except TitleTranslateError:
                    pass
            _persist_document_translation(
                doc,
                title_vi=draft,
                status=Threat.TitleViStatus.OK,
                provider="google",
            )
            return {"id": doc.id, "status": "ok", "provider": "google"}
        except TitleTranslateError as exc:
            logger.info("document google failed id=%s: %s", doc.id, exc)
            if _is_google_block_error(exc):
                trip_google_circuit(reason=str(exc)[:120])
            try:
                if groq_translate_available():
                    draft = groq_translate_title(title)
                    provider = f"groq:{getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-120b')}"
                else:
                    draft = ollama_translate_title(title)
                    provider = "ollama-fallback"
                if draft and not is_mangled_title_vi(
                    draft, provider=provider, original=title
                ):
                    _persist_document_translation(
                        doc,
                        title_vi=draft,
                        status=Threat.TitleViStatus.OK,
                        provider=provider[:64],
                    )
                    return {
                        "id": doc.id,
                        "status": "ok",
                        "provider": provider[:64],
                        "google_skipped": True,
                    }
            except TitleTranslateError:
                pass
            doc.title_vi_status = Threat.TitleViStatus.PENDING
            doc.title_vi_provider = "awaiting_google"
            doc.save(
                update_fields=[
                    "title_hash",
                    "title_vi_status",
                    "title_vi_provider",
                    "updated_at",
                ]
            )
            return {
                "id": doc.id,
                "status": "pending",
                "provider": "awaiting_google",
                "google_skipped": True,
            }

    # Circuit open and Ollama already tried / unavailable.
    doc.title_vi_status = Threat.TitleViStatus.PENDING
    doc.title_vi_provider = "awaiting_google"
    doc.save(
        update_fields=[
            "title_hash",
            "title_vi_status",
            "title_vi_provider",
            "updated_at",
        ]
    )
    return {
        "id": doc.id,
        "status": "pending",
        "provider": "awaiting_google",
        "google_skipped": True,
    }


def translate_scanned_documents(
    document_ids: list[int] | None = None,
    *,
    limit: int = 20,
    force: bool = False,
) -> dict[str, Any]:
    """Batch-translate pending ScannedDocument titles."""
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return {"skipped": True, "reason": "disabled"}

    qs = ScannedDocument.objects.all().order_by("-discovered_at", "-id")
    if document_ids:
        qs = qs.filter(id__in=document_ids)
    else:
        qs = qs.filter(
            Q(title_vi_status=Threat.TitleViStatus.PENDING)
            | Q(title_vi_status=Threat.TitleViStatus.FAILED)
            | Q(title_vi="")
        ).exclude(title_vi_status=Threat.TitleViStatus.SKIPPED)

    selected = list(qs[: max(1, min(int(limit or 20), 40))])
    stats: dict[str, Any] = {
        "processed": 0,
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "cached": 0,
        "pending": 0,
    }
    if not selected:
        return stats

    pause = max(
        0.0,
        float(getattr(settings, "GOOGLE_TRANSLATE_BATCH_PAUSE_SEC", 0.2) or 0.0),
    )
    with build_google_translate_client() as google_client:
        skip_google = is_google_circuit_open()
        consecutive_google_blocks = 0
        for index, doc in enumerate(selected):
            result = translate_scanned_document(
                doc,
                force=force,
                google_client=google_client,
                skip_google=skip_google,
            )
            stats["processed"] += 1
            status = result.get("status") or ""
            if result.get("cached"):
                stats["cached"] += 1
            if status in stats:
                stats[status] += 1
            provider = str(result.get("provider") or "")
            if result.get("google_skipped"):
                skip_google = True
                stats["circuit_open"] = True
            elif status == "pending" and provider == "awaiting_google":
                consecutive_google_blocks += 1
                if consecutive_google_blocks >= 2:
                    skip_google = True
                    trip_google_circuit(reason="document batch awaiting_google")
                    stats["circuit_open"] = True
            elif status == "ok" and provider.startswith("google"):
                consecutive_google_blocks = 0
            if (
                pause
                and index < len(selected) - 1
                and provider.startswith("google")
                and not skip_google
            ):
                time.sleep(pause)
    return stats


def enqueue_document_title_translations(document_ids: list[int]) -> None:
    """Queue document title translation without blocking document ingest."""
    ids = [int(i) for i in document_ids if i]
    if not ids:
        return
    if not getattr(settings, "DOCUMENT_SCAN_ENABLED", False):
        return
    if not getattr(settings, "TITLE_TRANSLATE_ENABLED", True):
        return
    try:
        from apps.integrations.tasks import translate_document_titles_task

        translate_document_titles_task.delay(ids)
    except Exception:  # noqa: BLE001
        logger.exception("enqueue_document_title_translations failed ids=%s", ids[:5])
