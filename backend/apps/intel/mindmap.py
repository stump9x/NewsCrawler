"""Fast, evidence-first relationship graph for Trạm tin tức.

The base graph is deterministic and cheap. Paid AI is an explicit, cached
second pass for the currently focused article; it never blocks opening the map.
"""

from __future__ import annotations

import hashlib
import json
import html
import math
import re
import unicodedata
from collections import Counter
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q, Subquery
from django.utils import timezone
from django.utils.html import strip_tags

from .models import Threat


_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "over", "after", "before",
    "cua", "cho", "voi", "trong", "tren", "sau", "truoc", "nhung", "mot",
    "cac", "dang", "duoc", "tai", "ve", "khi", "nay", "that", "this",
    "news", "new", "says", "said", "will", "its", "their", "about",
    # Generic grammar/news vocabulary is not evidence that two events relate.
    "chien", "cong", "tan", "cuoc", "quan", "su", "quoc", "phong",
    "hoat", "dong", "luc", "luong", "manh", "suc", "thu", "nghiem",
    "phat", "trien", "moi", "he", "thong", "dung", "nham", "muc", "tieu",
    "has", "had", "have", "power", "test", "tests", "released", "showcases",
    "aboard", "highlighting", "com", "www", "html", "async", "alt", "src",
    "width", "height", "px", "ship", "forces", "force", "military",
}

_EVENT_CONCEPTS = {
    "hai quan": "hoạt động hải quân và trên biển",
    "naval": "hoạt động hải quân và trên biển",
    "tau chien": "hoạt động hải quân và trên biển",
    "tau tan cong": "hoạt động hải quân và trên biển",
    "tau khu truc": "hoạt động hải quân và trên biển",
    "tau san bay": "hoạt động hải quân và trên biển",
    "tau ngam": "hoạt động hải quân và trên biển",
    "amphibious assault": "hoạt động hải quân và trên biển",
    "aircraft carrier": "hoạt động hải quân và trên biển",
    "submarine": "hoạt động hải quân và trên biển",
    "luc quan": "hoạt động lục quân",
    "khong quan": "hoạt động không quân",
    "bien dong": "Biển Đông",
    "dai loan": "eo biển Đài Loan",
    "ban dan that": "bắn đạn thật",
    "dien tap": "huấn luyện, diễn tập",
    "tap tran": "huấn luyện, diễn tập",
    "thu nghiem": "hoạt động thử nghiệm",
    "tan cong mang": "tấn công mạng",
    "tac chien mang": "tác chiến mạng",
    "tan cong sau": "tấn công tầm sâu",
    "tan cong": "hoạt động tác chiến",
    "tac chien": "hoạt động tác chiến",
    "chien dau": "hoạt động tác chiến",
    "combat": "hoạt động tác chiến",
    "phong thu": "hoạt động phòng thủ",
    "chi huy va kiem soat": "chỉ huy và kiểm soát",
    "khong nguoi lai": "phương tiện không người lái",
    "mua sam": "mua sắm quốc phòng",
    "hop tac quoc phong": "hợp tác quốc phòng",
    "xung dot": "xung đột quân sự",
}

_COUNTRIES = {
    "viet nam": "Việt Nam", "vietnam": "Việt Nam",
    "hoa ky": "Mỹ", "my": "Mỹ", "united states": "Mỹ", "u s": "Mỹ",
    "trung quoc": "Trung Quốc", "china": "Trung Quốc", "pla": "Trung Quốc",
    "nga": "Nga", "russia": "Nga", "ukraine": "Ukraine",
    "nhat ban": "Nhật Bản", "japan": "Nhật Bản",
    "han quoc": "Hàn Quốc", "south korea": "Hàn Quốc",
    "trieu tien": "Triều Tiên", "north korea": "Triều Tiên",
    "philippines": "Philippines", "dai loan": "Đài Loan", "taiwan": "Đài Loan",
    "an do": "Ấn Độ", "india": "Ấn Độ", "uc": "Australia", "australia": "Australia",
    "anh": "Anh", "united kingdom": "Anh", "phap": "Pháp", "france": "Pháp",
    "duc": "Đức", "germany": "Đức", "israel": "Israel", "iran": "Iran",
    "nato": "NATO", "asean": "ASEAN", "eu": "Liên minh châu Âu",
}

_CAPABILITY_TERMS = {
    "ten lua", "missile", "hypersonic", "sieu thanh", "tau ngam", "submarine",
    "tau san bay", "aircraft carrier", "drone", "uav", "ai", "tri tue nhan tao",
    "cyber", "mang", "radar", "satellite", "ve tinh", "nuclear", "hat nhan",
    "fighter", "tiem kich", "bomb", "oanh tac", "air defense", "phong khong",
}

# Aliases for the same weapon/capability must not be counted as separate
# evidence. For example, "missile" and "tên lửa" in one feed are one type,
# not two independent types.
_CAPABILITY_GROUPS = {
    "ten lua": "tên lửa", "missile": "tên lửa",
    "hypersonic": "siêu thanh", "sieu thanh": "siêu thanh",
    "tau ngam": "tàu ngầm", "submarine": "tàu ngầm",
    "tau san bay": "tàu sân bay", "aircraft carrier": "tàu sân bay",
    "drone": "UAV", "uav": "UAV",
    "ai": "trí tuệ nhân tạo", "tri tue nhan tao": "trí tuệ nhân tạo",
    "cyber": "tác chiến mạng", "mang": "tác chiến mạng",
    "radar": "radar", "satellite": "vệ tinh", "ve tinh": "vệ tinh",
    "nuclear": "hạt nhân", "hat nhan": "hạt nhân",
    "fighter": "máy bay tiêm kích", "tiem kich": "máy bay tiêm kích",
    "bomb": "oanh tạc", "oanh tac": "oanh tạc",
    "air defense": "phòng không", "phong khong": "phòng không",
}

_RELATION_LABELS = {
    "same_event": "Cùng sự kiện",
    "same_country": "Cùng quốc gia/thực thể",
    "same_capability": "Cùng năng lực/vũ khí",
    "same_topic": "Cùng chủ đề",
    "related_event": "Liên quan về nội dung",
    "cause_effect": "Nguyên nhân – hệ quả",
    "response": "Phản ứng/điều chỉnh",
    "cooperation": "Hợp tác/liên minh",
    "competition": "Cạnh tranh/đối trọng",
    "ai_related": "AI xác định liên quan",
}

_TERM_LABELS = {
    "aircraft carrier": "tàu sân bay", "tau san bay": "tàu sân bay",
    "missile": "tên lửa", "ten lua": "tên lửa", "hypersonic": "vũ khí siêu thanh",
    "sieu thanh": "vũ khí siêu thanh", "submarine": "tàu ngầm", "tau ngam": "tàu ngầm",
    "ai": "trí tuệ nhân tạo", "tri tue nhan tao": "trí tuệ nhân tạo",
    "cyber": "tác chiến mạng", "mang": "tác chiến mạng", "nuclear": "hạt nhân",
    "hat nhan": "hạt nhân", "fighter": "máy bay tiêm kích", "tiem kich": "máy bay tiêm kích",
    "air defense": "phòng không", "phong khong": "phòng không",
}

_TAG_LABELS = {
    # Geography and analyst-facing topic labels are rendered in Vietnamese;
    # the source slug remains unchanged for filtering and traceability.
    "geo-united-states": "Mỹ", "geo-china": "Trung Quốc", "geo-russia": "Nga",
    "geo-vietnam": "Việt Nam", "procurement": "mua sắm quốc phòng",
    "combat-trends": "xu hướng tác chiến", "exercises": "huấn luyện, diễn tập",
    "cyber": "tác chiến mạng", "strategy": "chiến lược", "weapons": "vũ khí",
    "force-posture": "bố trí lực lượng", "national-strategy": "chiến lược quốc gia",
    "cyber-operations": "tác chiến mạng", "defense-policy": "chính sách quốc phòng",
    "security-cooperation": "hợp tác an ninh", "maritime": "hàng hải",
    "analysis": "phân tích", "data-breach": "sự cố lộ lọt dữ liệu",
}

_GEO_TAG_LABELS = {
    "geo-africa": "Châu Phi", "geo-asia-pacific": "Châu Á – Thái Bình Dương",
    "geo-australia": "Australia", "geo-austria": "Áo", "geo-belgium": "Bỉ",
    "geo-brazil": "Brazil", "geo-cambodia": "Campuchia", "geo-canada": "Canada",
    "geo-colombia": "Colombia", "geo-czech-republic": "Cộng hòa Séc",
    "geo-denmark": "Đan Mạch", "geo-egypt": "Ai Cập", "geo-europe": "Châu Âu",
    "geo-finland": "Phần Lan", "geo-france": "Pháp", "geo-germany": "Đức",
    "geo-greece": "Hy Lạp", "geo-india": "Ấn Độ", "geo-indonesia": "Indonesia",
    "geo-iran": "Iran", "geo-iraq": "Iraq", "geo-israel": "Israel",
    "geo-italy": "Italy", "geo-japan": "Nhật Bản", "geo-laos": "Lào",
    "geo-lithuania": "Litva", "geo-malaysia": "Malaysia", "geo-mexico": "Mexico",
    "geo-middle-east": "Trung Đông", "geo-myanmar": "Myanmar",
    "geo-netherlands": "Hà Lan", "geo-new-zealand": "New Zealand",
    "geo-north-america": "Bắc Mỹ", "geo-north-korea": "Triều Tiên",
    "geo-norway": "Na Uy", "geo-pakistan": "Pakistan", "geo-philippines": "Philippines",
    "geo-poland": "Ba Lan", "geo-qatar": "Qatar", "geo-saudi-arabia": "Saudi Arabia",
    "geo-singapore": "Singapore", "geo-south-korea": "Hàn Quốc",
    "geo-southeast-asia": "Đông Nam Á", "geo-spain": "Tây Ban Nha",
    "geo-sweden": "Thụy Điển", "geo-taiwan": "Đài Loan", "geo-thailand": "Thái Lan",
    "geo-turkey": "Thổ Nhĩ Kỳ", "geo-ukraine": "Ukraine",
    "geo-united-arab-emirates": "Các Tiểu vương quốc Ả Rập Thống nhất",
    "geo-united-kingdom": "Vương quốc Anh", "geo-united-states": "Mỹ",
}


def _tag_label(slug: str) -> str:
    """Return an analyst-facing Vietnamese label without changing tag slugs."""
    key = str(slug or "").strip().casefold()
    if key in _TAG_LABELS:
        return _TAG_LABELS[key]
    if key in _GEO_TAG_LABELS:
        return _GEO_TAG_LABELS[key]
    if key.startswith("geo-"):
        return key[4:].replace("-", " ")
    return key.replace("-", " ")

_TECHNICAL_TAG_PREFIXES = ("site-", "source-", "feed-", "lang-")
_GEOGRAPHY_TAG_SLUGS = {
    "vietnam", "united-states", "usa", "china", "russia", "ukraine",
    "japan", "south-korea", "north-korea", "india", "taiwan",
    "australia", "philippines", "singapore", "indonesia", "thailand",
}


def _is_context_tag(slug: str) -> bool:
    """Keep analyst-facing topics; exclude ingestion/source bookkeeping."""
    return bool(slug) and not slug.startswith(_TECHNICAL_TAG_PREFIXES)


def _is_topic_tag(slug: str) -> bool:
    """Geography is useful context but is never topical evidence by itself."""
    return (
        _is_context_tag(slug)
        and not slug.startswith("geo-")
        and slug not in _GEOGRAPHY_TAG_SLUGS
    )


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn").casefold()


def _clean_item_text(item: Threat, *, title_only: bool = False) -> str:
    """Decode and remove feed markup before entity/token extraction."""
    title = f"{item.title_vi or ''} {item.title or ''}"
    if title_only:
        return " ".join(title.split())
    summary = html.unescape(item.summary or "")
    summary = re.sub(r"<[^>]*>", " ", summary)
    summary = strip_tags(summary)
    summary = re.sub(r"https?://\S+|www\.\S+", " ", summary, flags=re.I)
    return " ".join(f"{title} {summary}".split())


_GEOGRAPHY_TOKENS = {
    token
    for value in (*_COUNTRIES.keys(), *_COUNTRIES.values())
    for token in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", _plain(value))
}

# Generic platform/capability and grammar words are useful for topic facets,
# but they are not event identifiers. Keeping them out of the same-event rule
# prevents unrelated articles about nuclear ships, missiles, or exercises from
# being promoted to the same event merely because their titles use the same
# military vocabulary.
_EVENT_GENERIC_TITLE_TERMS = {
    "a", "and", "armed", "aircraft", "carrier", "chiec", "could", "does",
    "four", "bon", "hai", "hanh", "hat", "hien", "horsepower", "just",
    "khong", "most", "nao", "nam", "nang", "nhan", "nhat", "not", "one",
    "operate", "planet", "range", "reactor", "reactors", "single", "ship",
    "ships", "submarine", "sua", "tau", "the", "took", "two", "ung", "van",
    "warship", "warships", "years", "nuclear", "navy", "cruiser", "unlimited",
    "lo", "lua", "ten", "phao", "phong", "thu", "quan", "luyen", "tap",
    "xung", "dot", "attack", "attacks", "defense", "defence", "military",
    "force", "forces", "class", "lop", "new", "old", "first", "last",
    "mua", "sam", "quoc", "hop", "dong", "ke", "hoach", "contract",
    "contracts", "procurement", "purchase", "purchases", "national", "ngan",
    "sach", "budget", "program", "programme", "plan", "plans",
}


def _event_identity_tokens(item: Threat) -> set[str]:
    """Extract title terms that can identify a concrete event or asset."""
    raw_title = item.title or item.title_vi or ""
    words = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", _plain(raw_title))
    return {
        word
        for word in words
        if word not in _EVENT_GENERIC_TITLE_TERMS
        and word not in _STOPWORDS
        and not word.isdigit()
    }


def _tokens(item: Threat) -> set[str]:
    text = _plain(_clean_item_text(item))
    words = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text)
    return {word for word in words if word not in _STOPWORDS and not word.isdigit()}


def _title_tokens(item: Threat) -> set[str]:
    text = _plain(_clean_item_text(item, title_only=True))
    return {
        word for word in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text)
        if word not in _STOPWORDS and not word.isdigit()
    }


def _countries(item: Threat) -> set[str]:
    text = f" {_plain(_clean_item_text(item))} "
    found = set()
    for needle, label in _COUNTRIES.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text):
            found.add(label)
    return found


def _capabilities(item: Threat) -> set[str]:
    text = _plain(_clean_item_text(item))
    return {
        term
        for term in _CAPABILITY_TERMS
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
    }


def _capability_groups(item: Threat) -> set[str]:
    """Return distinct capability types, collapsing language/feed aliases."""
    return {_CAPABILITY_GROUPS.get(term, term) for term in _capabilities(item)}


def _event_concepts(item: Threat) -> set[str]:
    text = _plain(_clean_item_text(item))
    return {
        label
        for phrase, label in _EVENT_CONCEPTS.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text)
    }


def _shared_term_labels(source: Threat, target: Threat, terms: set[str]) -> list[str]:
    """Render normalized common title terms with their Vietnamese diacritics."""
    if not terms:
        return []
    target_terms = set(terms)
    labels: dict[str, str] = {}
    for item in (source, target):
        text = item.title_vi or item.title or ""
        for raw in re.findall(r"[^\W\d_][\w-]{2,}", text, flags=re.UNICODE):
            normalized = _plain(raw).strip("-")
            if normalized in target_terms and normalized not in labels:
                labels[normalized] = raw.casefold()
    return [labels[term] for term in sorted(target_terms) if term in labels]


def _node(item: Threat, *, focus: bool = False, wire_rank: int | None = None) -> dict[str, Any]:
    # Decode escaped feed markup first, then remove tags. Reversing this order
    # can turn ``&lt;img ...&gt;`` back into visible HTML in the analyst panel.
    decoded_summary = html.unescape(item.summary or "")
    # Some feeds store a truncated <img ...> fragment with no closing `>`.
    # Django's strip_tags intentionally leaves malformed markup untouched.
    if decoded_summary.lstrip().casefold().startswith("<img"):
        close = decoded_summary.find(">")
        decoded_summary = decoded_summary[close + 1 :] if close >= 0 else ""
    clean_summary = " ".join(strip_tags(decoded_summary).split())
    tag_contexts = [
        _tag_label(tag.slug)
        for tag in item.tags.all()
        if _is_context_tag(tag.slug)
    ]
    topic_labels = [
        _tag_label(tag.slug)
        for tag in item.tags.all()
        if _is_topic_tag(tag.slug)
    ]
    contexts = list(
        dict.fromkeys(
            [*sorted(_countries(item)), *(_TERM_LABELS.get(term, term) for term in sorted(_capabilities(item))), *tag_contexts]
        )
    )[:10]
    return {
        "id": item.id,
        "wire_rank": wire_rank,
        "title": (item.title_vi or item.title or "(không tiêu đề)").strip(),
        "title_original": (item.title or "").strip(),
        "summary": clean_summary[:700],
        "source_url": item.source_url or "",
        "source": item.source,
        "severity": item.severity,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "countries": sorted(_countries(item)),
        # Keep canonical capability groups available for the AI post-filter;
        # aliases such as missile/tên lửa must count as one type.
        "capability_groups": sorted(_capability_groups(item)),
        "event_concepts": sorted(_event_concepts(item)),
        "topic_labels": sorted(set(topic_labels)),
        "contexts": contexts,
        "event_cluster_id": None,
        "event_cluster_size": 1,
        "bridge_score": 0.0,
        "is_focus": focus,
    }


def _decorate_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """Add event clusters, bridge scores and multi-context facets."""
    # The decorator runs again after AI relationships are merged. Reset all
    # derived values so a changed edge cannot leave stale metadata behind.
    for node in nodes:
        node["event_cluster_id"] = None
        node["event_cluster_size"] = 1
        node["bridge_score"] = 0.0

    node_by_id = {node["id"]: node for node in nodes}
    parent = {node_id: node_id for node_id in node_by_id}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    degree: Counter[int] = Counter()
    relation_types: dict[int, set[str]] = {node_id: set() for node_id in node_by_id}
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if source not in node_by_id or target not in node_by_id:
            continue
        degree[source] += 1
        degree[target] += 1
        relation_types[source].add(edge["type"])
        relation_types[target].add(edge["type"])
        if edge["type"] == "same_event" and edge["score"] >= 0.62:
            union(source, target)

    groups: dict[int, list[int]] = {}
    for node_id in node_by_id:
        groups.setdefault(find(node_id), []).append(node_id)
    event_clusters = []
    for root, member_ids in groups.items():
        if len(member_ids) < 2:
            continue
        cluster_id = f"event-{root}"
        for node_id in member_ids:
            node_by_id[node_id]["event_cluster_id"] = cluster_id
            node_by_id[node_id]["event_cluster_size"] = len(member_ids)
        event_clusters.append(
            {
                "id": cluster_id,
                "node_ids": sorted(member_ids),
                "size": len(member_ids),
                "title": node_by_id[min(member_ids)]["title"],
            }
        )

    max_degree = max(degree.values(), default=1)
    bridge_nodes = []
    for node_id, node in node_by_id.items():
        diversity = len(relation_types[node_id])
        bridge_score = min(1.0, (degree[node_id] / max_degree) * 0.72 + min(diversity, 4) * 0.07)
        node["bridge_score"] = round(bridge_score, 3)
        if bridge_score >= 0.45:
            bridge_nodes.append(
                {
                    "id": node_id,
                    "score": round(bridge_score, 3),
                    "degree": degree[node_id],
                    "relation_types": sorted(relation_types[node_id]),
                }
            )
    bridge_nodes.sort(key=lambda item: (-item["score"], -item["degree"], item["id"]))
    context_counts = Counter(context for node in nodes for context in node.get("contexts") or [])
    contexts = [
        {"name": name, "count": count}
        for name, count in context_counts.most_common(18)
        if count >= 2
    ]
    return {
        "event_clusters": event_clusters,
        "bridge_nodes": bridge_nodes[:8],
        "contexts": contexts,
    }


def _pair(source: Threat, target: Threat) -> dict[str, Any] | None:
    a_tokens, b_tokens = _tokens(source), _tokens(target)
    shared = a_tokens & b_tokens
    content_shared = shared - _GEOGRAPHY_TOKENS
    union = a_tokens | b_tokens
    token_score = len(content_shared) / max(1, min(len(union), 24))
    countries = _countries(source) & _countries(target)
    has_country_relation = len(countries) >= 2
    capabilities = _capabilities(source) & _capabilities(target)
    capability_groups = _capability_groups(source) & _capability_groups(target)
    concepts = _event_concepts(source) & _event_concepts(target)
    tags_a = {tag.slug for tag in source.tags.all() if _is_topic_tag(tag.slug)}
    tags_b = {tag.slug for tag in target.tags.all() if _is_topic_tag(tag.slug)}
    shared_tags = tags_a & tags_b
    topic_labels = {
        _tag_label(tag)
        for tag in shared_tags
    }
    title_shared = (_title_tokens(source) & _title_tokens(target)) - _GEOGRAPHY_TOKENS
    title_union = _title_tokens(source) | _title_tokens(target)
    title_overlap = len(title_shared) / max(1, min(len(title_union), 18))
    event_identity_shared = _event_identity_tokens(source) & _event_identity_tokens(target)
    event_identity_union = _event_identity_tokens(source) | _event_identity_tokens(target)
    event_identity_overlap = len(event_identity_shared) / max(1, min(len(event_identity_union), 18))
    event_identity_labels = _shared_term_labels(source, target, event_identity_shared)
    independent_concepts = concepts - topic_labels
    shared_procurement = (
        "procurement" in shared_tags
        or "mua sắm quốc phòng" in concepts
    )
    shared_missile_capability = "tên lửa" in capability_groups
    hours = abs((source.published_at - target.published_at).total_seconds()) / 3600
    time_score = max(0.0, 1.0 - hours / (24 * 14))
    score = min(
        0.99,
        token_score * 1.75
        + min(len(content_shared), 4) * 0.035
        + (min(len(countries) - 1, 2) * 0.16 if has_country_relation else 0)
        + min(len(capability_groups), 2) * 0.16
        + min(len(concepts), 2) * 0.12
        + min(len(shared_tags), 2) * 0.18
        + time_score * 0.07,
    )
    if score < 0.20 or (
        len(content_shared) < 2
        and not has_country_relation
        and not capabilities
        and not concepts
        and not shared_tags
    ):
        return None

    # Same-event requires a concrete identifier in the original titles. A
    # generic overlap such as "tàu", "hạt nhân", or "nuclear" is never enough.
    same_event_evidence = (
        bool(event_identity_shared) and event_identity_overlap >= 0.15
    ) or (
        len(event_identity_shared) >= 2 and event_identity_overlap >= 0.25
    )
    # Procurement + missile is still too generic when the pair shares only
    # one country (or no country). Require at least two shared countries, in
    # line with the global same-country rule; a concrete same-event identifier
    # remains sufficient because it is stronger evidence than a topic facet.
    if shared_procurement and shared_missile_capability and not has_country_relation and not same_event_evidence:
        return None

    if same_event_evidence:
        relation_type = "same_event"
        reason = (
            "Hai bản tin cùng có dấu hiệu định danh cụ thể trong tiêu đề, "
            "nhiều khả năng mô tả một sự kiện hoặc tài sản cụ thể từ các nguồn khác nhau."
        )
    elif len(capability_groups) >= 2:
        relation_type = "same_capability"
        detail = ", ".join(sorted(capability_groups)[:4])
        reason = (
            f"Cùng đề cập từ hai loại năng lực hoặc phương tiện trở lên: {detail}."
        )
    elif shared_tags and (
        bool(countries)
        or bool(capability_groups)
        or bool(independent_concepts)
        or bool(event_identity_shared)
    ):
        relation_type = "same_topic"
        detail = ", ".join(
            _tag_label(tag)
            for tag in sorted(shared_tags)[:3]
        )
        evidence = []
        if countries:
            evidence.append("cùng chủ thể/quốc gia: " + ", ".join(sorted(countries)[:3]))
        if capability_groups:
            evidence.append("cùng năng lực: " + ", ".join(sorted(capability_groups)[:2]))
        if independent_concepts:
            evidence.append("cùng hoạt động: " + ", ".join(sorted(independent_concepts)[:2]))
        if event_identity_shared:
            evidence.append("có định danh nội dung chung")
        reason = f"Cùng nhóm chủ đề {detail}, đồng thời có " + "; ".join(evidence) + "."
    elif has_country_relation and concepts:
        relation_type = "same_country"
        country_detail = ", ".join(sorted(countries)[:3])
        concept_detail = ", ".join(sorted(concepts)[:2])
        reason = (
            f"Cùng đề cập {country_detail}; đồng thời cùng liên quan đến {concept_detail}. "
            "Liên kết được xác lập từ hai nhóm bằng chứng độc lập."
        )
    elif (
        has_country_relation
        and len(event_identity_labels) >= 2
        and title_overlap >= 0.18
    ):
        relation_type = "same_country"
        country_detail = ", ".join(sorted(countries)[:3])
        title_detail = ", ".join(event_identity_labels[:3])
        reason = (
            f"Cùng đề cập {country_detail}; hai tiêu đề còn cùng nhắc đến {title_detail}. "
            "Liên kết được xác lập từ quốc gia và nội dung tiêu đề, không chỉ từ tên nước."
        )
    else:
        # Weak token overlap is not a relationship. Require one shared country
        # plus a concrete military concept, two concrete concepts, or a strong
        # overlap of distinctive terms in both titles.
        strong_title_overlap = (
            len(event_identity_labels) >= 3 and event_identity_overlap >= 0.24
        )
        if countries and concepts:
            country_detail = ", ".join(sorted(countries))
            concept_detail = ", ".join(sorted(concepts)[:2])
            reason = (
                f"Hai tin cùng đề cập {country_detail} trong bối cảnh {concept_detail}. "
                "Đây là điểm liên quan về chủ thể và hoạt động; chưa đủ căn cứ xác định là cùng một sự kiện."
            )
        elif len(concepts) >= 2:
            concept_detail = ", ".join(sorted(concepts)[:3])
            reason = (
                f"Hai tin cùng đề cập {concept_detail}. Đây là điểm liên quan về nội dung; "
                "chưa đủ căn cứ xác định là cùng một sự kiện."
            )
        elif strong_title_overlap:
            detail = ", ".join(event_identity_labels[:4])
            reason = (
                f"Hai tiêu đề cùng nhắc đến {detail}. Đây là điểm chung cụ thể về nội dung; "
                "chưa đủ căn cứ xác định là cùng một sự kiện."
            )
        else:
            return None
        relation_type = "related_event"
    if hours <= 72:
        reason += f" Hai tin xuất hiện cách nhau khoảng {max(1, round(hours))} giờ."
    return {
        "source": source.id,
        "target": target.id,
        "type": relation_type,
        "label": _RELATION_LABELS[relation_type],
        "score": round(score, 3),
        "reason": reason,
        "provider": "rules",
        "ai_verified": False,
    }


def build_mindmap(
    *,
    focus_id: int | None = None,
    focus_rank: int | None = None,
    limit: int = 48,
    days: int = 14,
    search: str = "",
    user=None,
) -> dict[str, Any]:
    limit = max(25, min(int(limit or 100), 150))
    allowed_days = (1, 7, 14, 30)
    requested_days = int(days or 14)
    days = min(allowed_days, key=lambda value: abs(value - requested_days))
    now = timezone.now()
    cutoff = now - timedelta(days=days)

    # Mirror ThreatFilter.filter_wire_feed exactly. Mindmap must never include
    # rows that are absent from Trạm tin tức merely because their database IDs
    # still exist after pruning/filtering.
    general_days = int(getattr(settings, "WIRE_MAX_AGE_DAYS", 30) or 30)
    vietnam_days = int(
        getattr(settings, "WIRE_VIETNAM_MAX_AGE_DAYS", general_days)
        or general_days
    )
    eligible = (
        Threat.objects.filter(wire_relevant=True)
        .filter(
            Q(tags__slug="vietnam", published_at__gte=now - timedelta(days=vietnam_days))
            | Q(published_at__gte=now - timedelta(days=general_days))
        )
        .filter(
            Q(title_vi_status__in=["ok", "rule", "skipped"])
            & ~Q(title_vi="")
        )
        .distinct()
    )
    max_items = max(1, int(getattr(settings, "WIRE_MAX_ITEMS", 5000) or 5000))
    top_ids = eligible.order_by("-published_at", "-id").values("pk")[:max_items]
    base = (
        eligible.filter(pk__in=Subquery(top_ids))
        .prefetch_related("tags")
        .order_by("-published_at", "-id")
    )
    if user is not None:
        from apps.core.wire_filter_policy import apply_user_wire_policy

        # Mindmap keeps the same newest-first display timeline as Trạm tin tức;
        # rank assignment below is oldest-first for stable numbering.
        base = apply_user_wire_policy(base, user, prioritize=False)
    # Keep graph display independent from numbering: nodes use the same
    # oldest-to-newest rank map as Trạm tin tức and Yêu thích.
    ordered_wire_ids = list(
        base.order_by("published_at", "id").values_list("id", flat=True)
    )
    wire_total = len(ordered_wire_ids)
    wire_ranks = {
        item_id: index + 1
        for index, item_id in enumerate(ordered_wire_ids)
    }
    if focus_rank is not None:
        rank = int(focus_rank)
        if rank < 1 or rank > wire_total:
            return {
                "focus_id": None,
                "nodes": [],
                "edges": [],
                "meta": {
                    "empty": True,
                    "focus_rank_not_found": rank,
                    "wire_total": wire_total,
                },
            }
        # Ranks are oldest-first, so rank N maps directly to index N-1.
        # Using wire_total - rank would turn #1823 into the fourth story.
        focus_id = ordered_wire_ids[rank - 1]
    recent = base.filter(published_at__gte=cutoff)
    if search:
        recent = recent.filter(
            Q(title__icontains=search)
            | Q(title_vi__icontains=search)
            | Q(summary__icontains=search)
        )
    focus = base.filter(pk=focus_id).first() if focus_id else recent.first()
    if focus is None:
        return {"focus_id": None, "nodes": [], "edges": [], "meta": {"empty": True}}

    # Do not simply take the newest records: on a busy day that makes the
    # 7/14/30-day maps identical. Sample cumulative age bands, rank relevance
    # inside every band, then round-robin them into the graph. The selected
    # window therefore has a visible and truthful temporal effect.
    all_bands = ((0, 1), (1, 3), (3, 7), (7, 14), (14, 30))
    bands = [(start, end) for start, end in all_bands if start < days]
    ranked_bands: list[tuple[str, list[tuple[float, Threat, dict[str, Any]]]]] = []
    period_availability: list[tuple[str, int]] = []
    candidate_count = 0
    per_band_pool = max(60, limit * 2)
    for start_day, end_day in bands:
        lower = now - timedelta(days=min(end_day, days))
        upper = now - timedelta(days=start_day)
        band_window = recent.filter(
            published_at__gte=lower,
            published_at__lt=upper,
        )
        label = f"{start_day}–{min(end_day, days)} ngày trước" if start_day else "24 giờ gần nhất"
        period_availability.append((label, band_window.count()))
        band_query = band_window.exclude(pk=focus.pk)
        band_candidates = list(band_query[:per_band_pool])
        candidate_count += len(band_candidates)
        ranked = []
        for item in band_candidates:
            edge = _pair(focus, item)
            if edge:
                ranked.append((edge["score"], item, edge))
        ranked.sort(key=lambda row: (-row[0], -row[1].published_at.timestamp(), -row[1].id))
        if ranked:
            ranked_bands.append((label, ranked))

    selected = []
    selected_period_counts: Counter[str] = Counter()
    cursor = 0
    while len(selected) < limit - 1 and any(cursor < len(rows) for _, rows in ranked_bands):
        for label, rows in ranked_bands:
            if cursor < len(rows) and len(selected) < limit - 1:
                selected.append(rows[cursor])
                selected_period_counts[label] += 1
        cursor += 1
    if focus.published_at:
        focus_age = max(0.0, (now - focus.published_at).total_seconds() / 86400)
        for start_day, end_day in bands:
            if start_day <= focus_age < min(end_day, days):
                focus_label = f"{start_day}–{min(end_day, days)} ngày trước" if start_day else "24 giờ gần nhất"
                selected_period_counts[focus_label] += 1
                break
    nodes = [
        _node(focus, focus=True, wire_rank=wire_ranks.get(focus.id)),
        *[
            _node(item, wire_rank=wire_ranks.get(item.id))
            for _, item, _ in selected
        ],
    ]
    edges = [edge for _, _, edge in selected]

    # A few cross-links make the graph a network rather than a star. Keep this
    # bounded so the map remains readable and API latency stays predictable.
    related_items = [item for _, item, _ in selected[:28]]
    cross = []
    for i, left in enumerate(related_items):
        best = None
        for right in related_items[i + 1 :]:
            edge = _pair(left, right)
            if edge and edge["score"] >= 0.42 and (best is None or edge["score"] > best["score"]):
                best = edge
        if best:
            cross.append(best)
        if len(cross) >= max(5, min(18, len(related_items) // 2)):
            break
    edges.extend(cross)
    type_counts = Counter(edge["type"] for edge in edges)
    decorations = _decorate_graph(nodes, edges)
    return {
        "focus_id": focus.id,
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "days": days,
            "limit": limit,
            "wire_total": wire_total,
            "window_total": sum(count for _, count in period_availability),
            "candidate_count": candidate_count,
            "period_counts": [
                {
                    "label": label,
                    "shown": selected_period_counts[label],
                    "available": available,
                }
                for label, available in period_availability
                if available
            ],
            "relation_counts": dict(type_counts),
            **decorations,
            "generated_by": "rules",
        },
    }


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def analyze_focus_with_ai(*, focus_id: int, limit: int = 36, days: int = 30, user=None) -> dict[str, Any]:
    from apps.core.mindmap_policy import get_effective_user_mindmap_prompt

    policy_prompt = get_effective_user_mindmap_prompt(user)
    graph = build_mindmap(focus_id=focus_id, limit=limit, days=days, user=user)
    if not graph["nodes"]:
        return graph
    policy_hash = hashlib.sha256(policy_prompt.encode("utf-8")).hexdigest()[:12]
    user_key = str(getattr(user, "pk", 0) or 0)
    cache_key = f"mindmap:ai:v12:{user_key}:{policy_hash}:{focus_id}:{days}:{limit}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    node_by_id = {node["id"]: node for node in graph["nodes"]}
    candidates = []
    for edge in graph["edges"]:
        if edge["source"] != focus_id and edge["target"] != focus_id:
            continue
        other_id = edge["target"] if edge["source"] == focus_id else edge["source"]
        node = node_by_id.get(other_id)
        if not node:
            continue
        candidates.append(
            {
                "id": other_id,
                "title": node["title"],
                "summary": node["summary"][:360],
                "rule_reason": edge["reason"],
            }
        )
        if len(candidates) >= 12:
            break
    if not candidates:
        return graph

    from apps.integrations.ai.shopaikey_pool import shopaikey_chat_completion

    focus = node_by_id[focus_id]
    prompt = (
        "Thực hiện chính sách Mindmap dưới đây. Chỉ dùng dữ liệu đã cho; trả JSON hợp lệ, không Markdown. "
        "Mẫu JSON: {\"overview\":\"3-5 câu tiếng Việt\",\"patterns\":[\"ý 1\"],\"cautions\":[\"điểm cần kiểm chứng\"],\"relationships\":[{\"target_id\":1,\"type\":\"same_topic\",\"score\":0.8,\"reason\":\"1-2 câu tiếng Việt\"}]}.\n\n"
        f"CHÍNH SÁCH MINDMAP CỦA TÀI KHOẢN:\n{policy_prompt}\n\n"
        f"TIN TRUNG TÂM: {json.dumps(focus, ensure_ascii=False)}\n"
        f"ỨNG VIÊN: {json.dumps(candidates, ensure_ascii=False)}"
    )
    try:
        result = shopaikey_chat_completion(
            messages=[
                {"role": "system", "content": "Bạn là chuyên viên phân tích OSINT quốc phòng. Trả JSON tiếng Việt hợp lệ, không Markdown và tuân thủ chính sách bằng chứng."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1450,
            temperature=0.05,
            model=str(getattr(settings, "MINDMAP_SHOPAIKEY_MODEL", "qwen-flash") or "qwen-flash"),
            profile="fast",
            timeout=float(getattr(settings, "MINDMAP_AI_TIMEOUT_SECONDS", 12) or 12),
            try_fallback_models=False,
        )
    except RuntimeError:
        # Paid analysis is an optional enhancement. Never discard or turn the
        # deterministic graph into an HTTP 500 when the gateway is congested.
        graph["meta"].update({
            "ai_used": False,
            "ai_error": "Dịch vụ AI chưa phản hồi trong giới hạn thời gian; bản đồ liên kết quy tắc vẫn được giữ nguyên.",
        })
        cache.set(cache_key, graph, timeout=60)
        return graph
    payload = _extract_json(str(result.get("text") or ""))
    allowed_ids = {item["id"] for item in candidates}
    allowed_types = set(_RELATION_LABELS) - {"ai_related"}
    ai_edges = []
    for row in payload.get("relationships") or []:
        if not isinstance(row, dict):
            continue
        try:
            target_id = int(row.get("target_id"))
            score = max(0.0, min(float(row.get("score") or 0), 1.0))
        except (TypeError, ValueError):
            continue
        rel_type = str(row.get("type") or "related_event")
        reason = " ".join(str(row.get("reason") or "").split())[:500]
        if target_id not in allowed_ids or rel_type not in allowed_types or score < 0.45 or len(reason) < 18:
            continue
        if rel_type == "same_topic":
            target_node = node_by_id.get(target_id) or {}
            focus_topics = set(focus.get("contexts") or []) - set(focus.get("countries") or [])
            target_topics = set(target_node.get("contexts") or []) - set(target_node.get("countries") or [])
            shared_countries = set(focus.get("countries") or []) & set(target_node.get("countries") or [])
            shared_capabilities = set(focus.get("capability_groups") or []) & set(target_node.get("capability_groups") or [])
            shared_concepts = set(focus.get("event_concepts") or []) & set(target_node.get("event_concepts") or [])
            shared_topic_labels = set(focus.get("topic_labels") or []) & set(target_node.get("topic_labels") or [])
            independent_concepts = shared_concepts - shared_topic_labels
            shared_procurement = (
                "mua sắm quốc phòng" in shared_topic_labels
                or "mua sắm quốc phòng" in shared_concepts
            )
            shared_missile_capability = "tên lửa" in shared_capabilities
            if (
                shared_procurement
                and shared_missile_capability
                and len(shared_countries) < 2
            ):
                # The model must not link two generic missile-procurement
                # stories that mention only Mỹ, only Trung Quốc, or no common
                # country. Require at least two shared countries.
                continue
            if not (focus_topics & target_topics) or not (
                shared_countries or shared_capabilities or independent_concepts
            ):
                # Enforce the rule after model output as well; prompt wording
                # alone is not a sufficient guard against false positives.
                continue
        if rel_type == "same_capability":
            target_node = node_by_id.get(target_id) or {}
            shared_capabilities = (
                set(focus.get("capability_groups") or [])
                & set(target_node.get("capability_groups") or [])
            )
            if len(shared_capabilities) < 2:
                # A single shared weapon/capability is not an independent
                # relationship, even when the model assigns a high score.
                continue
        if rel_type == "same_country":
            target_node = node_by_id.get(target_id) or {}
            shared_countries = set(focus.get("countries") or []) & set(target_node.get("countries") or [])
            if len(shared_countries) < 2:
                continue
        ai_edges.append(
            {
                "source": focus_id,
                "target": target_id,
                "type": rel_type,
                "label": _RELATION_LABELS[rel_type],
                "score": round(score, 3),
                "reason": reason,
                "provider": f"shopaikey/{result.get('model') or 'qwen-flash'}",
                "ai_verified": True,
            }
        )
        if len(ai_edges) >= 8:
            break
    if ai_edges:
        by_pair = {(min(e["source"], e["target"]), max(e["source"], e["target"])): e for e in graph["edges"]}
        for edge in ai_edges:
            by_pair[(min(edge["source"], edge["target"]), max(edge["source"], edge["target"]))] = edge
        graph["edges"] = list(by_pair.values())
        # AI edges may change bridge rankings and relation diversity.
        decorations = _decorate_graph(graph["nodes"], graph["edges"])
        graph["meta"].update(decorations)
    overview = " ".join(str(payload.get("overview") or "").split())[:1200]
    patterns = [
        " ".join(str(item).split())[:360]
        for item in (payload.get("patterns") or [])
        if len(" ".join(str(item).split())) >= 18
    ][:5]
    cautions = [
        " ".join(str(item).split())[:360]
        for item in (payload.get("cautions") or [])
        if len(" ".join(str(item).split())) >= 18
    ][:4]
    graph["meta"] = {
        **graph["meta"],
        "generated_by": "rules+ai",
        "ai_model": result.get("model") or "qwen-flash",
        "ai_edge_count": len(ai_edges),
        "ai_insight": {
            "overview": overview,
            "patterns": patterns,
            "cautions": cautions,
        },
    }
    cache.set(cache_key, graph, timeout=24 * 3600)
    return graph
