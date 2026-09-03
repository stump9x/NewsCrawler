"""Multilingual topic expansion for last30days research.

Lexicon (VI/EN/ZH) + optional Groq semantic expansion. Strict phrase matching
avoids Vietnamese tone collisions (Biển Đông ↔ biến động).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from apps.integrations.web_reader.phrase import (
    contains_phrase,
    fold_diacritics,
    normalize_phrase,
)

_TOPIC_GROUPS: tuple[tuple[str, ...], ...] = (
    (
        "biển đông",
        "south china sea",
        "west philippine sea",
        "南中国海",
        "西菲律宾海",
    ),
    (
        "bãi cạn second thomas",
        "bãi cạn second thomas shoal",
        "second thomas shoal",
        "second thomas",
        "ayungin shoal",
        "ayungin",
        "bãi cạn ayungin",
        "仁爱礁",
        "仁愛礁",
    ),
    (
        "bãi cạn scarlet",
        "scarlet shoal",
        "bãi đá vành khăn",
        "mischief reef",
        "美济礁",
    ),
    (
        "đá xuất phát",
        "fiery cross reef",
        "永暑礁",
    ),
    (
        "đá chữ thập",
        "cuarteron reef",
        "华阳礁",
    ),
    (
        "bãi tư chính",
        "vanguard bank",
    ),
    (
        "eo biển đài loan",
        "taiwan strait",
        "taiwan straits",
        "台湾海峡",
        "台灣海峽",
    ),
    (
        "biển hoa đông",
        "east china sea",
        "东海",
    ),
    (
        "đài loan",
        "taiwan",
        "台湾",
        "台灣",
    ),
    (
        "quân giải phóng nhân dân",
        "people's liberation army",
        "解放军",
    ),
    (
        "hải quân pla",
        "pla navy",
        "people's liberation army navy",
        "中国海军",
    ),
    (
        "cảnh sát biển trung quốc",
        "china coast guard",
        "中国海警",
    ),
    (
        "dân quân biển",
        "maritime militia",
        "海上民兵",
    ),
    (
        "nine dash line",
        "nine-dash line",
        "đường lưỡi bò",
        "đường chín đoạn",
        "九段线",
    ),
    (
        "west philippine sea",
        "biển đông philippines",
    ),
)

_MATCH_MIN_CHARS = 4
DEFAULT_TREND_TOPIC = "__newscrawler_scope__"

# The automatic Xu hướng board uses a bounded strategic scope rather than a
# free-text query. A country/entity must appear with a strategic signal; this
# prevents generic celebrity, sport and consumer stories from leaking in.
_TREND_SCOPE_COUNTRIES = (
    "trung quốc", "china", "中国", "philippines", "đài loan", "taiwan", "台湾",
    "nhật bản", "japan", "malaysia", "indonesia", "campuchia", "cambodia",
    "lào", "laos", "thái lan", "thailand", "singapore", "brunei", "myanmar",
    "australia", "úc", "united states", "usa", "mỹ", "north korea", "triều tiên",
    "south korea", "hàn quốc", "việt nam", "vietnam",
)
_TREND_SCOPE_SIGNALS = (
    "quốc phòng", "quân sự", "hải quân", "không quân", "diễn tập", "huấn luyện",
    "an ninh", "biên giới", "cửa khẩu", "chủ quyền", "biển đông", "south china sea",
    "taiwan strait", "maritime", "navy", "military", "defense", "defence", "exercise",
    "drill", "border", "sovereignty", "radar", "ra-đa", "tên lửa", "missile",
    "phòng không", "air defense", "điều khiển", "command and control", "tàu không người lái",
    "unmanned", "6g", "trí tuệ nhân tạo", "artificial intelligence", "hạt nhân", "nuclear",
    "kiểm soát xuất khẩu", "export control", "trừng phạt", "sanctions", "đối phó",
    "học thuyết", "doctrine", "sách trắng", "white paper", "hợp tác quốc phòng",
    "defense cooperation", "an ninh mạng", "cybersecurity", "cyber attack",
)
_TREND_SCOPE_REGION_SIGNALS = (
    "biển đông", "south china sea", "west philippine sea", "quần đảo trường sa",
    "spratly", "hoàng sa", "paracel", "bãi cạn", "shoal", "taiwan strait", "eo biển đài loan",
)
_TREND_SCOPE_SEARCH_ALIASES = (
    "South China Sea military", "Taiwan Strait defense", "China military", "maritime security",
    "military exercise", "defense cooperation", "border security", "cybersecurity",
    "export controls", "unmanned vessel", "6G artificial intelligence",
)
_EN_STOP = frozenset(
    {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "at"}
)


def _canon(text: str) -> str:
    return normalize_phrase(text)


def english_phrase_tokens(phrase: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[a-z0-9]+", (phrase or "").casefold())
        if len(t) > 2 and t not in _EN_STOP
    ]


def contains_english_tokens(text: str, phrase: str) -> bool:
    tokens = english_phrase_tokens(phrase)
    if len(tokens) < 2:
        return contains_phrase(text, phrase)
    hay = fold_diacritics(text)
    return all(tok in hay for tok in tokens)


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, tuple[str, ...]]:
    index: dict[str, tuple[str, ...]] = {}
    for group in _TOPIC_GROUPS:
        members = tuple(x.strip() for x in group if x and x.strip())
        keys = {_canon(x) for x in members}
        keys |= {fold_diacritics(x) for x in members}
        for key in keys:
            prev = index.get(key)
            if prev is None:
                index[key] = members
            else:
                index[key] = tuple(dict.fromkeys([*prev, *members]))
    return index


def expand_topic_aliases(topic: str, *, limit: int = 12) -> list[str]:
    """Lexicon-only alias list (original first)."""
    raw = " ".join((topic or "").split()).strip()
    if not raw:
        return []
    if normalize_phrase(raw) == DEFAULT_TREND_TOPIC:
        return list(_TREND_SCOPE_SEARCH_ALIASES)[: max(1, limit)]

    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = " ".join((value or "").split()).strip()
        if not text:
            return
        key = _canon(text)
        if key in seen:
            return
        seen.add(key)
        out.append(text)

    add(raw)
    key = _canon(raw)
    folded = fold_diacritics(raw)
    index = _alias_index()
    group = index.get(key) or index.get(folded)
    if group:
        english = [
            a
            for a in group
            if re.search(r"[A-Za-z]", a) and not re.search(r"[À-ỹ一-鿿]", a)
        ]
        cjk = [a for a in group if re.search(r"[一-鿿]", a)]
        other = [a for a in group if a not in english and a not in cjk]
        for alias in english + cjk + other:
            add(alias)

    if re.search(r"[A-Za-z].*[À-ỹđĐ一-鿿]|[À-ỹđĐ一-鿿].*[A-Za-z]", raw):
        latin_parts = re.findall(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})*", raw)
        for part in latin_parts:
            if len(part) < 4:
                continue
            add(part)
            g2 = index.get(_canon(part)) or index.get(fold_diacritics(part))
            if g2:
                for alias in g2:
                    add(alias)

    return out[: max(1, limit)]


def preferred_english_query(topic: str) -> str:
    aliases = expand_topic_aliases(topic, limit=12)
    for alias in aliases:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-/.']*", alias):
            if " " in alias or len(alias) >= 6:
                return alias
    for alias in aliases:
        if re.search(r"[A-Za-z]", alias) and not re.search(r"[À-ỹ一-鿿]", alias):
            return alias
    return aliases[0] if aliases else topic


@dataclass(frozen=True)
class TopicPlan:
    topic: str
    aliases: tuple[str, ...]
    english: str
    must_tokens: tuple[str, ...]
    groq_used: bool = False

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "aliases": list(self.aliases),
            "english": self.english,
            "must_tokens": list(self.must_tokens),
            "groq_used": self.groq_used,
        }


def build_topic_plan(topic: str, *, use_groq: bool = True) -> TopicPlan:
    """Lexicon + optional Groq semantic expansion (one call per research run)."""
    raw = " ".join((topic or "").split()).strip()
    if normalize_phrase(raw) == DEFAULT_TREND_TOPIC:
        return TopicPlan(
            topic=DEFAULT_TREND_TOPIC,
            aliases=tuple(_TREND_SCOPE_SEARCH_ALIASES),
            english="South China Sea military and Asia-Pacific strategic security",
            must_tokens=(),
            groq_used=False,
        )
    aliases = list(expand_topic_aliases(raw, limit=12))
    english = preferred_english_query(raw)
    must = tuple(english_phrase_tokens(english))
    groq_used = False

    if use_groq:
        from .topic_groq import groq_expand_topic

        hint = groq_expand_topic(raw)
        if hint:
            groq_used = True
            canon = str(hint.get("canonical_english") or "").strip()
            if canon:
                english = canon
            seen = {_canon(a) for a in aliases}
            for alias in hint.get("aliases") or []:
                text = " ".join(str(alias or "").split()).strip()
                if not text:
                    continue
                key = _canon(text)
                if key in seen:
                    continue
                seen.add(key)
                aliases.append(text)
            tokens = hint.get("must_tokens") or []
            if isinstance(tokens, list) and len(tokens) >= 2:
                cleaned = tuple(
                    t.casefold()
                    for t in (str(x).strip() for x in tokens)
                    if len(t) > 2 and t.isascii()
                )[:8]
                if len(cleaned) >= 2:
                    must = cleaned

    if english and _canon(english) not in {_canon(a) for a in aliases}:
        aliases.insert(1, english)

    return TopicPlan(
        topic=raw,
        aliases=tuple(aliases[:14]),
        english=english or raw,
        must_tokens=must,
        groq_used=groq_used,
    )


def _match_aliases(topic: str) -> list[str]:
    out: list[str] = []
    for alias in expand_topic_aliases(topic, limit=12):
        compact = re.sub(r"\s+", "", alias)
        if re.search(r"[一-鿿]", alias):
            if len(alias) < 3:
                continue
        elif len(compact) < _MATCH_MIN_CHARS:
            continue
        out.append(alias)
    return out or expand_topic_aliases(topic, limit=3)


def _match_aliases_from_plan(plan: TopicPlan) -> list[str]:
    out: list[str] = []
    for alias in plan.aliases:
        compact = re.sub(r"\s+", "", alias)
        if re.search(r"[一-鿿]", alias):
            if len(alias) < 3:
                continue
        elif len(compact) < _MATCH_MIN_CHARS:
            continue
        out.append(alias)
    return out or list(plan.aliases[:3])


def item_matches_topic(
    text: str, topic: str, plan: TopicPlan | None = None
) -> bool:
    blob = text or ""
    aliases = (
        _match_aliases_from_plan(plan) if plan is not None else _match_aliases(topic)
    )
    for alias in aliases:
        if contains_phrase(blob, alias):
            return True
    if plan is not None and len(plan.must_tokens) >= 2:
        hay = fold_diacritics(blob)
        if all(tok in hay for tok in plan.must_tokens):
            return True
    return False


def filter_items_for_topic(
    items: list[dict],
    topic: str,
    *,
    trust_english_query: str | None = None,
    plan: TopicPlan | None = None,
) -> list[dict]:
    kept: list[dict] = []
    if normalize_phrase(topic) == DEFAULT_TREND_TOPIC:
        for item in items:
            blob = "\n".join(
                str(item.get(k) or "")
                for k in ("title", "snippet", "body", "content", "url", "cluster_title")
            )
            has_region = any(contains_phrase(blob, phrase) for phrase in _TREND_SCOPE_REGION_SIGNALS)
            has_country = any(contains_phrase(blob, phrase) for phrase in _TREND_SCOPE_COUNTRIES)
            has_signal = any(contains_phrase(blob, phrase) for phrase in _TREND_SCOPE_SIGNALS)
            if has_region or (has_country and has_signal):
                kept.append(item)
        return kept
    eng = (trust_english_query or (plan.english if plan else "") or "").strip()
    for item in items:
        blob = "\n".join(
            str(item.get(k) or "")
            for k in ("title", "snippet", "body", "content", "url", "cluster_title")
        )
        if item_matches_topic(blob, topic, plan=plan):
            kept.append(item)
            continue
        if eng and contains_english_tokens(blob, eng):
            kept.append(item)
            continue
        if plan is not None and len(plan.must_tokens) >= 2:
            hay = fold_diacritics(blob)
            if all(tok in hay for tok in plan.must_tokens):
                kept.append(item)
    return kept
