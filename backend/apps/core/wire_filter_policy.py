"""Runtime-managed policies for the deterministic Trạm tin tức filter.

The owner-less record is the administrator policy used by the global ingest
worker. Each operational user may keep a private policy draft without changing
the shared corpus or another account. Only explicit ``GIỮ:`` and ``LOẠI:``
directives affect matching; the five evidence-based topic gates stay fixed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from django.db import IntegrityError
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.utils import OperationalError, ProgrammingError

from .models import WireFilterPrompt
from .wire_prompt import DEFAULT_WIRE_FILTER_PROMPT

MAX_WIRE_FILTER_PROMPT_CHARS = 12000
WIRE_FILTER_PROMPT_CACHE_SECONDS = 30

LEGACY_WIRE_FILTER_PROMPT = """MỤC ĐÍCH
Đánh giá từng tin để quyết định có đưa vào Trạm tin tức hay không. Bộ lọc dùng quy tắc xác định, không gọi AI và không tiêu tốn token.

DỮ LIỆU CẦN ĐỌC
Tiêu đề, tiêu đề tiếng Việt, tóm tắt, nội dung trích xuất, nguồn, đường dẫn, thời điểm, quốc gia và khu vực liên quan.

ƯU TIÊN GIỮ LẠI
- kế hoạch và chiến lược quốc gia về quân sự, quốc phòng;
- chính sách quốc phòng, chủ quyền và toàn vẹn lãnh thổ;
- tác chiến, chiến dịch, triển khai, tuần tra, răn đe;
- huấn luyện, diễn tập, bắn đạn thật, sẵn sàng chiến đấu;
- tổ chức biên chế, cơ cấu lực lượng, chỉ huy, thế trận;
- ngân sách, mua sắm và công nghiệp quốc phòng;
- vũ khí, đạn dược, tên lửa, tàu chiến, tàu ngầm, máy bay chiến đấu, radar, vệ tinh và hệ thống chỉ huy;
- tác chiến mạng, chiến tranh thông tin và tác chiến điện tử khi gắn với quân sự, quốc phòng, an ninh quốc gia hoặc hạ tầng trọng yếu.

RÀO CHẮN GIỮ TIN
1. Tin phải có nội dung quân sự, quốc phòng hoặc tác chiến mạng thực chất.
2. Chỉ nhắc một quốc gia, căn cứ, đơn vị, quan chức, loại vũ khí hoặc thẻ chủ đề thì chưa đủ.
3. Tin vũ khí chỉ thuộc một quốc gia sẽ bị loại nếu không có ý nghĩa chiến lược, tác động khu vực, chuyển giao, liên minh, xung đột, răn đe hoặc ảnh hưởng xuyên biên giới.
4. Không suy diễn quốc gia bị tác động chỉ từ quốc gia của nguồn xuất bản.
5. Tin chỉ nói về mua sắm hoặc đặt mua vũ khí của quốc gia ngoài Đông Nam Á, Đài Loan và Trung Quốc thì loại khỏi Trạm tin tức; tin không xác định rõ quốc gia không tự động bị loại theo quy tắc này.

RÀO CHẮN LOẠI TIN
Loại nội dung đời sống mềm, gia đình, ẩm thực, sức khỏe, giải trí, thể thao, hoạt động cộng đồng, nghi lễ, quảng bá, thời tiết, du lịch, tai nạn dân sự và pháp đình dân sự nếu không có kết quả quân sự cụ thể.

TÙY CHỈNH CỦA NGƯỜI DÙNG
Mỗi dòng bắt đầu bằng GIỮ: hoặc LOẠI: và tiếp theo là các cụm từ, ngăn cách bằng dấu chấm phẩy. LOẠI được ưu tiên hơn GIỮ. Các dòng này chỉ tinh chỉnh thêm, không được vượt qua rào chắn quân sự, quốc gia và nội dung đời sống mềm.

GIỮ: kế hoạch quốc phòng; chiến lược quốc gia; hoạt động tác chiến; huấn luyện quân sự; tổ chức biên chế; ngân sách quốc phòng; mua sắm quốc phòng; tác chiến mạng; tác chiến điện tử; chính sách chủ quyền
LOẠI: đời sống quân nhân; hoạt động cộng đồng; thể thao quân đội; lễ kỷ niệm; tin giải trí; hồ sơ cá nhân; quảng bá đơn vị

KẾT QUẢ
Đạt yêu cầu thì đưa vào Trạm tin tức và gắn thẻ phù hợp; không đạt thì không hiển thị."""

_DIRECTIVE_RE = re.compile(r"^\s*(GIỮ|GIU|LOẠI|LOAI)\s*:\s*(.+)$", re.IGNORECASE)
_CACHE = {"expires_at": 0.0, "prompt": DEFAULT_WIRE_FILTER_PROMPT}


# Fixed safety exclusions for all accounts. Each tuple is an AND group.
GLOBAL_WIRE_NOISE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("primary election",), ("midterm election",), ("bầu cử sơ bộ",),
    ("bầu cử giữa kỳ",), ("balance of power", "election"),
    ("als risk",), ("nguy cơ mắc bệnh als",),
    ("wildfire response",), ("forest fire response",),
    ("cháy rừng", "vệ binh quốc gia"), ("narcan",), ("lost children",),
    ("trẻ em bị lạc",), ("community response", "national guard"),
    ("community support", "national guard"), ("takes command", "central air forces"),
    ("assumes command", "central"), ("nhận chỉ huy", "bộ tư lệnh"), ("bổ nhiệm chỉ huy", "bộ tư lệnh"),
    ("case study", "investment"), ("nghiên cứu trường hợp", "đầu tư"),
    ("cpo", "exports"), ("cpo", "xuất khẩu"),
    ("rebels killed", "clash"), ("insurgents killed", "clash"),
    ("phiến quân bị giết", "đụng độ"), ("phiến quân thiệt mạng", "đụng độ"),
    ("built to counter the soviet navy",),
    ("được xây dựng để chống lại hải quân liên xô",),
    ("i called her", "battleship"), ("tôi đã gọi cô ta là", "tàu"),
    ("quyết định hôm qua", "sứ mạng ngày mai"),
    ("meet dr", "mission"), ("làm quen với tiến sĩ",),
    ("f4u corsair", "distinctive look"), ("f4u corsair", "vẻ ngoài đặc trưng"),
    ("former", "sailors", "say goodbye"), ("cựu thủy thủ", "nói lời tạm biệt"),
    ("old salt", "last look"),
)



_ALLOWED_WEAPON_PROCUREMENT_GEO = frozenset(
    {
        "vietnam",
        "geo-philippines",
        "geo-thailand",
        "geo-indonesia",
        "geo-malaysia",
        "geo-cambodia",
        "geo-laos",
        "geo-myanmar",
        "geo-singapore",
        "geo-southeast-asia",
        "geo-taiwan",
        "geo-china",
    }
)

_WEAPON_PROCUREMENT_RE = (
    r"(procurement|acquisition|purchase|purchases|purchasing|buy|buys|bought|"
    r"contract|award|awarded|mua sắm|mua|đặt mua|hợp đồng|采购|购买|订购|"
    r"order(?:s|ed)?\s+(?:\d+|more|new|additional|another|two|three|four|"
    r"five|six|seven|eight|nine|ten))"
)
_WEAPON_SYSTEM_RE = (
    r"(weapon|arms|missile|rocket|torpedo|munition|ammunition|fighter|aircraft|"
    r"warship|frigate|destroyer|submarine|tank|artillery|radar|air defense|"
    r"vũ khí|tên lửa|đạn dược|máy bay chiến đấu|tàu chiến|tàu ngầm|xe tăng|"
    r"pháo binh|radar|phòng không|武器|导弹|军舰|潜艇|战机)"
)


def global_external_weapon_procurement_query() -> Q:
    """Return a DB-safe exclusion for out-of-scope weapon procurement."""
    from django.db.models import Subquery
    from apps.intel.models import Threat

    procurement_ids = Threat.objects.filter(
        tags__slug="procurement"
    ).values("pk")
    text_query = Q()
    for field in ("title", "title_vi"):
        text_query |= Q(
            **{f"{field}__iregex": rf"{_WEAPON_PROCUREMENT_RE}.*{_WEAPON_SYSTEM_RE}"}
        )
        text_query |= Q(
            **{f"{field}__iregex": rf"{_WEAPON_SYSTEM_RE}.*{_WEAPON_PROCUREMENT_RE}"}
        )
    allowed_geo_ids = Threat.objects.filter(
        tags__slug__in=_ALLOWED_WEAPON_PROCUREMENT_GEO
    ).values("pk")
    country_ids = Threat.objects.filter(
        tags__slug__startswith="geo-"
    ).values("pk")
    return (
        Q(pk__in=Subquery(procurement_ids))
        & Q(pk__in=Subquery(country_ids))
        & text_query
        & ~Q(pk__in=Subquery(allowed_geo_ids))
    )

def global_wire_display_exclusion_query() -> Q:
    """Build the account-independent safety query for legacy Wire rows."""
    fields = ("title", "title_vi", "summary")
    result = Q()
    for group in GLOBAL_WIRE_NOISE_GROUPS:
        group_query = Q()
        for phrase in group:
            phrase_query = Q()
            for field in fields:
                phrase_query |= Q(**{f"{field}__icontains": phrase})
            group_query &= phrase_query
        result |= group_query
    return result


@dataclass(frozen=True)
class WireFilterDirectives:
    keep: tuple[str, ...]
    exclude: tuple[str, ...]


def clear_wire_filter_prompt_cache() -> None:
    _CACHE["expires_at"] = 0.0


def get_wire_filter_prompt_record() -> WireFilterPrompt:
    """Return the owner-less administrator policy used by the ingest worker."""
    try:
        record, _ = WireFilterPrompt.objects.get_or_create(
            singleton_key="default",
            defaults={"prompt": DEFAULT_WIRE_FILTER_PROMPT, "owner": None},
        )
        return record
    except IntegrityError:
        return WireFilterPrompt.objects.get(singleton_key="default")


def get_user_wire_filter_prompt_record(user) -> WireFilterPrompt:
    """Return a user's private policy, initialized from the current admin policy."""
    if user.is_superuser:
        return get_wire_filter_prompt_record()
    existing = WireFilterPrompt.objects.filter(owner=user).first()
    if existing is not None:
        return existing
    admin_record = get_wire_filter_prompt_record()
    admin_prompt = admin_record.prompt
    try:
        record, _ = WireFilterPrompt.objects.get_or_create(
            owner=user,
            defaults={
                "singleton_key": f"user-{user.pk}",
                "prompt": admin_prompt,
                "favorite_recommendations_enabled": admin_record.favorite_recommendations_enabled,
                "updated_by": user,
            },
        )
        return record
    except IntegrityError:
        return WireFilterPrompt.objects.get(owner=user)


def get_effective_user_wire_filter_prompt(user) -> str:
    """Policy applied to one account's Wire views without affecting other users."""
    if not user or not getattr(user, "is_authenticated", False):
        return get_wire_filter_prompt()
    if user.is_superuser:
        return get_wire_filter_prompt()
    record = WireFilterPrompt.objects.filter(owner=user).only("prompt").first()
    if record is not None:
        return record.prompt
    return get_wire_filter_prompt()


def _wire_phrase_query(phrase: str) -> Q:
    return (
        Q(title__icontains=phrase)
        | Q(title_vi__icontains=phrase)
        | Q(summary__icontains=phrase)
    )


def apply_user_wire_policy(queryset, user, *, prioritize: bool = True):
    """Apply one account's exclusions, optionally prioritizing GIỮ phrases.

    The global administrator policy still controls ingest. A personal policy can
    hide or prioritize rows inside that approved corpus, never restore a row that
    the system policy rejected and never alter another account's queryset.
    ``prioritize=False`` is used by the publication timeline so every view keeps
    the canonical newest-first display order; the policy still filters excluded rows.
    """
    queryset = queryset.exclude(global_wire_display_exclusion_query())
    # Scope is evaluated on article evidence at ingest/reclassification time.
    # A blanket procurement veto would also hide Zumwalt/Guam modernization.
    directives = parse_wire_filter_directives(
        get_effective_user_wire_filter_prompt(user)
    )
    for phrase in directives.exclude:
        queryset = queryset.exclude(_wire_phrase_query(phrase))

    queryset = annotate_favorite_recommendations(queryset, user)

    keep_query = Q()
    for phrase in directives.keep:
        keep_query |= _wire_phrase_query(phrase)
    if directives.keep and prioritize:
        existing_order = tuple(queryset.query.order_by) or ("-published_at", "-id")
        queryset = queryset.annotate(
            personal_policy_keep=Case(
                When(keep_query, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by("-personal_policy_keep", *existing_order)
    return queryset



def get_user_wire_recommendations_enabled(user) -> bool:
    """Return the account-local favorite-based recommendation switch."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    record = WireFilterPrompt.objects.filter(owner=user).only(
        "favorite_recommendations_enabled"
    ).first()
    return bool(
        getattr(record, "favorite_recommendations_enabled", True)
        if record is not None
        else True
    )


def annotate_favorite_recommendations(queryset, user):
    """Require a specific subtopic AND country shared with the same favorite.

    Generic topic/source tags never count. Favorites rejected by the current
    scope do not train recommendations. Keep the publication timeline intact.
    """
    if not get_user_wire_recommendations_enabled(user):
        return queryset.annotate(
            personal_interest_score=Value(0, output_field=IntegerField())
        )

    from django.db.models import Exists, OuterRef, Subquery
    from apps.intel.models import ThreatFavorite
    from apps.intel.filters import WIRE_COUNTRY_FILTER_SLUGS
    from .wire_topics import TOPIC_LABELS

    through = queryset.model.tags.through
    # These subqueries are nested inside the favorite EXISTS, so the second
    # OuterRef resolves to the candidate article, not the favorite record.
    candidate_topics = through.objects.filter(
        threat_id=OuterRef(OuterRef("pk")),
        tag__slug__in=TOPIC_LABELS,
    ).values("tag_id")
    candidate_countries = through.objects.filter(
        threat_id=OuterRef(OuterRef("pk")),
        tag__slug__in=WIRE_COUNTRY_FILTER_SLUGS,
    ).values("tag_id")
    matches = (
        ThreatFavorite.objects.filter(user=user, threat__wire_relevant=True)
        .exclude(threat_id=OuterRef("pk"))
        .filter(threat__tags__in=Subquery(candidate_topics))
        .filter(threat__tags__in=Subquery(candidate_countries))
    )
    return queryset.annotate(
        personal_interest_score=Case(
            When(Q(wire_relevant=True) & Exists(matches), then=Value(3)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

def get_wire_filter_prompt() -> str:
    now = time.monotonic()
    if now < float(_CACHE["expires_at"]):
        return str(_CACHE["prompt"])
    try:
        record = get_wire_filter_prompt_record()
        prompt = (record.prompt or DEFAULT_WIRE_FILTER_PROMPT).strip()
    except (OperationalError, ProgrammingError):
        # Safe during rolling deploys before the migration has completed.
        prompt = DEFAULT_WIRE_FILTER_PROMPT
    _CACHE["prompt"] = prompt
    _CACHE["expires_at"] = now + WIRE_FILTER_PROMPT_CACHE_SECONDS
    return prompt


def parse_wire_filter_directives(prompt: str) -> WireFilterDirectives:
    keep: list[str] = []
    exclude: list[str] = []
    for raw_line in (prompt or "").splitlines():
        match = _DIRECTIVE_RE.match(raw_line)
        if not match:
            continue
        target = exclude if match.group(1).casefold().startswith("lo") else keep
        for phrase in re.split(r"[;\n]", match.group(2)):
            normalized = " ".join(phrase.casefold().split()).strip(" .,-")
            if 3 <= len(normalized) <= 160 and normalized not in target:
                target.append(normalized)
    return WireFilterDirectives(keep=tuple(keep), exclude=tuple(exclude))


def evaluate_wire_filter_prompt(text: str, *, prompt: str | None = None) -> tuple[bool, bool]:
    """Return ``(explicit_keep, explicit_exclude)`` for normalized article text."""
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False, False
    directives = parse_wire_filter_directives(get_wire_filter_prompt() if prompt is None else prompt)
    explicit_exclude = any(phrase in normalized for phrase in directives.exclude)
    explicit_keep = any(phrase in normalized for phrase in directives.keep)
    return explicit_keep, explicit_exclude
