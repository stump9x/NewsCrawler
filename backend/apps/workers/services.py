"""Persist parsed stealer rows and feed payloads into intel models."""

from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.conf import settings

from apps.core.crypto import encrypt_secret, password_fingerprint
from apps.core.wire_filter_policy import (
    GLOBAL_WIRE_NOISE_GROUPS,
    evaluate_wire_filter_prompt,
)
from apps.core.wire_topics import TOPIC_LABELS, classify_wire_topics
from apps.intel.models import CompromisedCredential, DataLeak, Indicator, Tag, Threat
from apps.intel.watching import (
    match_indicator_against_rules,
    match_leak_against_rules,
    match_threat_against_rules,
)
from apps.workers.feed_dates import (
    clamp_published_at,
    is_within_max_age,
    parse_feed_datetime,
    resolve_item_published,
)
from apps.workers.geography import detect_geography_tag_slugs
from apps.workers.parsers.stealer import ParsedCredential, parse_stealer_log

logger = logging.getLogger(__name__)

_DEFENSE_SECURITY_SIGNAL_RE = re.compile(
    r"(?i)(?:\b(?:"
    r"cyber warfare|cyber operations?|cyber command|military cyber|"
    r"defen[cs]e cyber|information warfare|electronic warfare|"
    r"state[\s-]sponsored|nation[\s-]state|apt(?:\s+group)?|"
    r"cyber espionage|military espionage|critical infrastructure|"
    r"wiper malware|zero[\s-]day|supply chain attack|"
    r"command and control|c4isr|c5isr|jadc2"
    r")\b|"
    r"网络战|网空作战|网络部队|网络司令部|信息战|电子战|网络间谍|关键基础设施|"
    r"tác chiến mạng|chiến tranh mạng|tác chiến thông tin|tác chiến điện tử|"
    r"gián điệp mạng|hạ tầng trọng yếu)"
)


# Wire: regional priority for Vietnam-related intel (severity high + pin to top).
VIETNAM_KEYWORDS = (
    "vietnam",
    "viet nam",
    "việt nam",
    "viet-nam",
    "vietnamese",
    "người việt",
    "hanoi",
    "ha noi",
    "hà nội",
    "ho chi minh",
    "hồ chí minh",
    "saigon",
    "sài gòn",
    "tp.hcm",
    "tp hcm",
    "vncert",
    "vnisa",
    "bac ninh",
    "bắc ninh",
    "da nang",
    "đà nẵng",
    "hai phong",
    "hải phòng",
    "can tho",
    "cần thơ",
    "công ty cổ phần",
    "cong ty co phan",
    "công ty tnhh",
    "cong ty tnhh",
    "tập đoàn",
    "tap doan",
)

# .vn domains / paths are a strong Vietnam signal even without the word "Vietnam".
_VN_TLD_RE = re.compile(r"(?<![a-z0-9-])(?:[a-z0-9-]+\.)+vn\b", re.IGNORECASE)
_VN_ENTITY_RE = re.compile(
    r"c(?:ô|o)ng\s+ty(?:\s+c(?:ổ|o)\s+ph(?:ầ|a)n|\s+tnhh)?",
    re.IGNORECASE,
)


def secrss_wire_priority() -> int:
    """Boost SecRSS so Chinese analysis titles translate ahead of generic EN noise."""
    return int(getattr(settings, "WIRE_SECRSS_PRIORITY", 45) or 45)


def secrss_max_age_days() -> int:
    """SecRSS deep-analysis pieces stay useful longer than breaking news."""
    return int(getattr(settings, "WIRE_SECRSS_MAX_AGE_DAYS", 90) or 90)


def is_secrss_item(item: dict[str, Any]) -> bool:
    blob = " ".join(
        str(item.get(key) or "")
        for key in ("feed", "feed_url", "link", "url", "website_url")
    ).casefold()
    return "secrss.com" in blob


def is_secrss_analysis_signal(text: str) -> bool:
    """Defense / cyber-intel cues common on 安全内参 (beyond strict military keywords)."""
    return bool(
        re.search(
            r"(?:"
            r"APT|JCWA|CMMC|JADC2|PLA|SS7|"
            r"网络战|网空|网军|网络部队|网络司令部|网络攻击|网络武器|网络安全|"
            r"情报|军用|军工|国防|军事|导弹|海军|空军|陆军|作战|指挥控制|"
            r"漏洞|恶意软件|监视|印太|台湾|台海|南海|人工智能|AI|"
            r"cyber(?:\s|-)?(?:war|attack|ops|command)|malware|ransomware|"
            r"intelligence|missile|naval|military"
            r")",
            text,
            flags=re.IGNORECASE,
        )
    )


def vietnam_wire_priority() -> int:
    return int(getattr(settings, "WIRE_VIETNAM_PRIORITY", 100) or 100)


def strategic_wire_priority() -> int:
    return int(getattr(settings, "WIRE_STRATEGIC_PRIORITY", 50) or 50)


# Back-compat aliases for tests/imports.
VIETNAM_WIRE_PRIORITY = 100
STRATEGIC_WIRE_PRIORITY = 50


def is_vietnam_related(*parts: str) -> bool:
    """True when title/summary/feed metadata mentions Vietnam."""
    text = " ".join(str(p or "") for p in parts)
    if not text.strip():
        return False
    folded = text.casefold()
    if any(k.casefold() in folded for k in VIETNAM_KEYWORDS):
        return True
    if _VN_TLD_RE.search(text):
        return True
    if _VN_ENTITY_RE.search(text):
        return True
    return False


def threat_looks_vietnam_related(
    *,
    title: str = "",
    summary: str = "",
    source_url: str = "",
    raw_payload: Any = None,
    country_code: str = "",
) -> bool:
    """Shared detector for RSS + ransomware ingest and retag backfills."""
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    country = str(
        country_code
        or payload.get("country_code")
        or payload.get("country")
        or ""
    ).strip()
    if country.upper() in {"VN", "VNM", "VIETNAM", "VIET NAM"}:
        return True
    blob = " ".join(
        [
            title,
            summary,
            source_url,
            str(payload.get("description") or ""),
            str(payload.get("summary") or ""),
            str(payload.get("victim") or ""),
            str(payload.get("domain") or ""),
            str(payload.get("website") or ""),
            str(payload.get("post_url") or ""),
            str(payload.get("url") or ""),
            str(payload.get("link") or ""),
            country,
        ]
    )
    return is_vietnam_related(blob)


def is_defense_security_signal(text: str) -> bool:
    """Cyber/security signal with military, state or strategic relevance."""
    return bool(_DEFENSE_SECURITY_SIGNAL_RE.search(text or ""))


# Curated defense FeedSource country codes that imply Indo-Pacific / major actor context
# even when the headline omits an explicit country name.
# GB/UK intentionally excluded: UK publisher feeds still require Indo-Pacific / priority
# actors in the article (avoids "British Army in Estonia"-only noise).
WIRE_FEED_COUNTRY_CODES = frozenset(
    {
        "US",
        "USA",
        "CN",
        "CHN",
        "JP",
        "JPN",
        "TW",
        "TWN",
        "PH",
        "PHL",
        "VN",
        "VNM",
        "LA",
        "LAO",
        "TH",
        "THA",
        "KH",
        "KHM",
        "ID",
        "IDN",
        "MY",
        "MYS",
        "AU",
        "AUS",
        "IN",
        "IND",
        "KR",
        "KOR",
        "SG",
        "SGP",
        "RU",
        "RUS",
        "UA",
        "UKR",
        "MM",
        "MMR",
        "NZ",
        "NZL",
    }
)

# Priority / monitored geography for Wire *inclusion* (matches search dropdown + Indo-Pacific).
# UK/Canada/Pakistan/Estonia/etc. are tagged when present but do NOT alone keep a story.
# Co-mention with a priority country keeps the item and retains all country flags/tags.
WIRE_PRIORITY_GEO_SLUGS = frozenset(
    {
        "vietnam",
        "geo-china",
        "geo-united-states",
        "geo-philippines",
        "geo-taiwan",
        "geo-thailand",
        "geo-indonesia",
        "geo-malaysia",
        "geo-japan",
        "geo-cambodia",
        "geo-laos",
        "geo-australia",
        "geo-russia",
        "geo-ukraine",
        "geo-myanmar",
        "geo-india",
        "geo-south-korea",
        "geo-north-korea",
        "geo-singapore",
        "geo-new-zealand",
        "geo-southeast-asia",
        "geo-asia-pacific",
    }
)

_WIRE_REGION_GEO_SLUGS = frozenset(
    {
        "geo-southeast-asia",
        "geo-asia-pacific",
        "geo-middle-east",
        "geo-europe",
        "geo-latin-america",
        "geo-north-america",
        "geo-africa",
        "geo-emea",
    }
)


def is_monitored_country_context(text: str) -> bool:
    """Monitored Indo-Pacific / priority defense actors (EN + CJK + VI).

    UK/Britain alone is NOT enough — requires Indo-Pacific / listed actors so
    European-theatre-only stories (e.g. British Army in Estonia) are excluded.
    """
    return bool(
        re.search(
            r"\b("
            r"china|chinese|pla|prc|beijing|"
            r"united states|usa|american|pentagon|us forces|us navy|us army|"
            r"us air force|us marine|department of defense|department of war|"
            r"taiwan|taiwanese|taipei|"
            r"japan|japanese|tokyo|"
            r"philippines|philippine|filipino|manila|"
            r"west philippine sea|\bwps\b|scarborough(?:\s+shoal)?|"
            r"second thomas(?:\s+shoal)?|ayungin(?:\s+shoal)?|"
            r"vietnam|viet nam|vietnamese|hanoi|"
            r"laos|lao|laotian|vientiane|"
            r"thailand|thai|bangkok|"
            r"cambodia|cambodian|khmer|phnom penh|"
            r"indonesia|indonesian|jakarta|"
            r"malaysia|malaysian|kuala lumpur|"
            r"australia|australian|canberra|"
            r"india|indian|new delhi|modi|"
            r"south korea|korean|seoul|rok|"
            r"north korea|dprk|pyongyang|"
            r"singapore|singaporean|"
            r"new zealand|"
            r"russia|russian|moscow|"
            r"ukraine|ukrainian|kyiv|kiev|"
            r"myanmar|burma|burmese|"
            r"south china sea|taiwan strait|indo-pacific|asia-pacific|asean|"
            r"west philippine sea|"
            r"indopacom|pacific fleet|quad alliance|quad"
            r")\b|"
            # Bare US token (US Air Force) — avoid matching inside words.
            r"(?<![a-z0-9])us(?![a-z0-9])|"
            # Avoid \\b around dotted abbreviations like U.S. / U.S.A.
            r"(?<![a-z0-9])u\.s\.a?(?![a-z0-9])|"
            r"中国|美国|美军|美太空军|美陆军|美海军|美空军|台湾|台灣|日本|菲律宾|越南|老挝|泰国|柬埔寨|印度尼西亚|马来西亚|"
            r"澳大利亚|印度|韩国|韓國|朝鲜|朝鮮|新加坡|俄罗斯|俄羅斯|乌克兰|烏克蘭|缅甸|"
            r"việt nam|hà nội|đài loan|nhật bản|lào|thái lan|campuchia|úc|nga|ukraina|myanmar|"
            r"trung quốc|mỹ|hoa kỳ|philippines|philippine|australia|"
            r"解放军|自卫队|自衛隊|海上自卫队|海上自衛隊|防卫省|防衛省|南海|台海|"
            r"国家安全局|\bnsa\b|\btni\b",
            text,
        )
    )


def feed_implies_monitored_country(item: dict[str, Any]) -> bool:
    """True when the curated FeedSource is tagged to a monitored country."""
    code = str(item.get("country_code") or "").strip().upper()
    if code in WIRE_FEED_COUNTRY_CODES:
        return True
    country = str(item.get("country") or "").strip()
    if country and is_monitored_country_context(country.casefold()):
        return True
    return False


def _wire_content_geography_slugs(item: dict[str, Any]) -> list[str]:
    """Geography slugs from article content (not publisher feed country_code)."""
    return detect_geography_tag_slugs(
        item.get("title"),
        item.get("title_vi"),
        item.get("summary"),
        item.get("description"),
        item.get("content"),
        "",
        country_code="",
        feed_url=str(item.get("feed_url") or ""),
        source_url=str(item.get("link") or item.get("url") or ""),
    )


def is_non_priority_country_only(item: dict[str, Any]) -> bool:
    """
    True when content names country-level geography and none are priority/monitored.

    Examples: Canada-only or Pakistan-only → True (exclude).
    Canada+Australia or Pakistan+India → False (keep, with all country tags).
    No country named → False (do not veto; other relevance rules still apply).
    """
    slugs = _wire_content_geography_slugs(item)
    country_slugs = [
        slug
        for slug in slugs
        if slug == "vietnam"
        or (slug.startswith("geo-") and slug not in _WIRE_REGION_GEO_SLUGS)
    ]
    if not country_slugs:
        return False
    return not any(slug in WIRE_PRIORITY_GEO_SLUGS for slug in slugs)


# Absolute soft veto — never overridden by hard-ops name-drops in PR copy
# (e.g. "Community Day" + "multi-domain missions" in the blurb).
_ABSOLUTE_SOFT_NEWS_RE = re.compile(
    r"(?:"
    # Kitchen-table / family lifestyle / morale meal fluff
    r"\bkitchen[\s-]?table\b|"
    r"\bdinner[\s-]?table\b|"
    r"\bstarts at (?:the )?(?:home|kitchen|table)\b|"
    r"\breadiness starts at\b|"
    r"\brecipe(?:s)?\b|"
    r"\bcookbooks?\b|"
    r"\bcooking tips?\b|"
    r"\bmeal[\s-]?prep\b|"
    r"\bmeal brings?\b|"
    r"\bsix courses\b|"
    r"\bbuffet of\b|"
    r"\bdining facilit(?:y|ies)\b|"
    r"\broach(?:es)? infestation\b|"
    r"\bparenting\b|"
    r"\braising (?:kids|children)\b|"
    r"\bwellness\b|"
    r"\bself[\s-]?care\b|"
    r"\bmindfulness\b|"
    r"\blifestyle\b|"
    r"\bhuman[\s-]?interest\b|"
    r"\bcelebrities\b|"
    r"\bcelebrity\b|"
    r"\bentertainment (?:news|industry|show|segment)\b|"
    r"\bwork[\s-]?life balance\b|"
    r"\bfamily dinner\b|"
    r"\btips for (?:military )?famil(?:y|ies)\b|"
    r"\bhomemakers?\b|"
    r"\bdating tips?\b|"
    r"\bbeauty tips?\b|"
    # Base community-day / outreach PR (ignore multi-domain PR blurb)
    r"\bcommunity day\b|"
    r"\bcommunity outreach\b|"
    r"\bstrengthen(?:s|ed|ing)? ties with (?:the )?community\b|"
    r"\bopen house\b|"
    r"\bsunscreen\b|"
    r"\bjust fun\b|"
    r"\bnew parent support\b|"
    r"\bfamily day\b|"
    r"\bholiday party\b|"
    r"\bribbon[\s-]?cutting\b|"
    r"\bchild\s*(?:&|and)\s*youth services\b|"
    # Fundraising / galas / obituaries
    r"\bfundraiser\b|"
    r"\bfund[\s-]?rais(?:e|ing|er|ers)\b|"
    r"\bcampaign raises\b|"
    r"\braises? \$\d|"
    r"\bgala\b|"
    r"\bcharity (?:ball|dinner|event|gala)\b|"
    r"\bobituar(?:y|ies)\b|"
    r"\bpasses away\b|"
    r"\bin memoriam\b|"
    r"\bfunerals?\b|"
    # EFMP / military-kids lifestyle camps
    r"\befmp\b|"
    r"\bmilitary kids?\b|"
    r"\bteaches?(?: \w+){0,6} to cook\b|"
    r"\bcook(?:ing)? camp\b|"
    # Fitness admin / body-composition scoring (not ops)
    r"\bwaist[\s-]?to[\s-]?height\b|"
    r"\bbody[\s-]?composition\b|"
    r"\bfitness (?:test|assessment|scoring|standard|center)s?\b|"
    r"\bwill not be scored\b|"
    # Human-interest profiles / service-member sports hobbies
    r"\bmeet the\b|"
    r"\bpowerlift(?:ing|er|ers)?\b|"
    r"\b(?:state |national |world )?powerlifting records?\b|"
    r"\bsetting .{0,40}\b(?:state |national )?records?\b|"
    r"\bguardian setting\b|"
    r"\bhobby\b|"
    r"\bpersonal (?:profile|story|journey)\b|"
    # VI lifestyle / meal fluff
    r"bàn ăn|nấu ăn|công thức nấu|"
    r"nuôi dạy(?: con)?|lối sống|"
    r"sức khỏe tinh thần|mẹo gia đình|"
    r"người nổi tiếng"
    r")",
    re.IGNORECASE,
)

# Lifestyle / sports / ceremonial / tabloid / civilian-disaster noise.
# Soft veto is skipped when hard operational substance is also present
# (e.g. CBRN force protection at a World Cup venue).
_SOFT_NEWS_NOISE_RE = re.compile(
    r"(?:"
    # Sports / celebrity athletics (URL path or headline) — overrideable
    r"/sports/|"
    r"\b(?:wnba|nba|nfl|mlb|nhl|fifa|uefa)\b|"
    r"\ball[\s-]?star(?:s)?\b|"
    r"\bmuay thai\b|"
    r"\bworld champion\b|"
    r"\bholds court at\b|"
    r"\bsports? (?:news|page|section|celebrity)\b|"
    r"\bflag football\b|"
    r"\ball[\s-]?army sports\b|"
    r"\bfootball (?:teams?|players?)\b|"
    r"\bahead of (?:the )?\d{4} olympics\b|"
    r"\bthế vận hội\b|"
    r"\bbóng đá\b|"
    # Wildlife / nature curiosities (not mil strategy)
    r"\bwhale sharks?\b|"
    r"\bsnacking\b.{0,40}\b(?:shark|whale|wildlife)\b|"
    r"\b(?:shark|whale)\b.{0,40}\bsnacking\b|"
    r"\bwildlife (?:curiosit|feature|story|documentary)\b|"
    r"\bnature (?:curiosit|feature|story|documentary)\b|"
    # Ceremonial / youth civic visits / change-of-command pageantry
    r"\bboys nation\b|"
    r"\bgirls nation\b|"
    r"\blay(?:s|ing)? (?:a )?wreath\b|"
    r"\bwreath[\s-]?laying\b|"
    r"\byouth (?:delegates?|leaders?) visit\b|"
    r"\bdelegates visit (?:the )?(?:pentagon|memorial)\b|"
    r"\bchange of command(?: ceremony)?\b|"
    r"\brelinquish(?:es|ed)? command\b|"
    r"lễ bàn giao chỉ huy|"
    # Tabloid crime / legal-defense / traffic-court fluff
    r"\bmurder case\b|"
    r"\bcause of death\b|"
    r"\b(?:defense|defence) teams?\b|"
    r"\b(?:defense|defence) (?:counsel|attorney|lawyer)s?\b|"
    r"\bpost[\s-]?mortem\b|"
    r"\bautopsy\b|"
    r"\bkhám nghiệm tử thi\b|"
    r"\bhighway crash\b|"
    r"\bfatal .{0,40}(?:crash|accident)\b|"
    r"\btraffic accident\b|"
    # Weather-only advisories / civilian ferry cancellations
    r"\brough seas\b|"
    r"\bsea travel suspended\b|"
    r"\bweather (?:advisory|alert|warning)\b|"
    r"\bsmall craft advisories?\b|"
    r"\b(?:canceled|cancelled) ferry\b|"
    r"\bferry (?:trips?|sailings?) (?:canceled|cancelled|suspended)\b|"
    # Courts / impeachment / prosecution (not military defense)
    r"\bimpeach(?:ment|ed|ing)?\b|"
    r"\bsenator[\s-]?judges?\b|"
    r"\bvp trial\b|"
    r"\bimpeachment trial\b|"
    r"\b(?:defense|defence)\s*,\s*prosecution\b|"
    r"\bprosecution\s*,\s*(?:defense|defence)\b|"
    r"\b(?:defense|defence)\s+and\s+prosecution\b|"
    r"\bprosecution (?:eye|eyes|seeks?|argues?|rests?|has completed|completed)\b|"
    r"\bper[\s-]?article presentation\b|"
    r"\bhasten .{0,30}\b(?:impeach|trial)\b|"
    r"\bcourt (?:procedural|procedure|hearing)\b|"
    # Port pollution / local environmental enforcement (not security ops)
    r"\bpollutant\b|"
    r"\b(?:oil |chemical |toxic )?pollution\b|"
    r"\b(?:port |terminal )drain\b|"
    r"\btraces? .{0,60}\b(?:pollutant|pollution|contaminat)\b|"
    r"\b(?:oil|chemical|toxic) spill\b|"
    r"\benvironmental (?:spill|pollution|contamination|cleanup|enforcement)\b|"
    r"\bwater (?:pollution|contamination)\b|"
    # Civilian maritime disasters / civil shipping enforcement (not naval combat).
    # Hard-ops override keeps war/SINKEX items (cruise missile hits, live-fire sinking).
    r"\bships? sinking\b|"
    r"\b(?:ship|ferry|boat|vessel)s? (?:sank|sinks|sinking|capsized|capsiz(?:e|es|ing))\b|"
    r"\bshipping firm\b|"
    r"\bmaritime (?:industry )?authority\b|"
    r"\bfined (?:p|₱)\s?\d|"
    r"\bfined .{0,20}\bpesos?\b|"
    r"\bcruise ships?\b|"
    r"\b(?:ship|ferry|boat|vessel)s? hits? (?:a )?(?:lock|pier|dock|wall|bridge|jetty)\b|"
    r"\binjur(?:e|es|ing|ed) \d+ (?:people|passengers|tourists|crew)\b|"
    r"\bcivilian (?:maritime|boating|shipping) accident\b|"
    r"\btourist (?:boat|ferry|ship) (?:accident|collision|capsiz)\b|"
    r"\bpassenger (?:ship|ferry|boat).{0,40}\b(?:sank|sinking|capsized|accident|disaster)\b|"
    r"đắm tàu|chìm tàu|tàu (?:chìm|đắm)|đắm phà|"
    # VI / EN / JP protocol & thin itinerary notices (not substantive outcomes)
    r"về cuộc hội đàm|"
    r"về cuộc gặp|"
    r"về cuộc họp|"
    r"về lịch trình(?: công tác)?|"
    r"lịch trình công tác(?: nước ngoài)?|"
    r"会談について|"
    r"会合について|"
    r"出張予定について|"
    r"視察の予定について|"
    r"\bitinerary\b|"
    r"\bschedule of (?:the )?visit\b|"
    r"\bvisit schedule\b|"
    r"\boverseas (?:visit )?schedule\b|"
    r"\bprogram(?:me)? of (?:the )?visit\b|"
    r"\bon the (?:minister(?:'s)? )?(?:overseas )?schedule\b|"
    r"\bthin (?:protocol|itinerary) notice\b|"
    r"thể thao|vô địch thế giới|"
    r"ô nhiễm|du thuyền|du lịch|"
    r"luận tội|truy tố"
    r")",
    re.IGNORECASE,
)

# Legal "defense/defence" (= counsel / impeachment), not military defense.
_LEGAL_DEFENSE_PHRASE_RE = re.compile(
    r"(?:"
    r"\b(?:defense|defence)\s+teams?\b|"
    r"\b(?:defense|defence)\s+(?:counsel|attorney|lawyer|lawyers)\b|"
    r"\blegal\s+(?:defense|defence)\b|"
    r"\b(?:defense|defence)\s+(?:argument|arguments|claim|claims)\b|"
    r"\b(?:defense|defence)\s+(?:in|for)\s+(?:the\s+)?(?:murder|criminal|civil|trial|impeach)\b|"
    r"\b(?:defense|defence)\s*,\s*prosecution\b|"
    r"\bprosecution\s*,\s*(?:defense|defence)\b|"
    r"\b(?:defense|defence)\s+and\s+prosecution\b|"
    r"\bimpeach(?:ment|ed|ing)?\b|"
    r"\bper[\s-]?article (?:presentation|trial)\b"
    r")",
    re.IGNORECASE,
)

# Hard operational / strategic substance — overrides soft-news veto when present.
_HARD_MIL_OPERATIONAL_RE = re.compile(
    r"(?:"
    r"\b(?:a2/?ad|anti[\s-]?access|area[\s-]?denial)\b|"
    r"\b(?:isr|c4isr|c5isr|jadc2)\b|"
    r"\b(?:doctrine|operational art|warfighting|multi[\s-]?domain|"
    r"war plans?|theater strategy|campaign plan|"
    r"force posture|live[\s-]?fire|munitions?|hypersonic|"
    r"ballistic missile|cruise missile|aircraft carrier|submarine|"
    r"destroyer|frigate|fighter jets?|fighter aircraft|bomber|amphibious|"
    r"deploys? (?:troops|forces|ships|aircraft|fighters)|"
    r"deployment of (?:troops|forces|ships|aircraft)|"
    r"(?:joint|naval|military|live[\s-]?fire|combat|field|war|bilateral|"
    r"multilateral|amphibious)\s+exercises?\b|"
    r"\blive[\s-]?fire drills?\b|"
    r"deterrence posture|nuclear triad|"
    r"national (?:defense|defence|security|military) strategy|"
    r"military strategy|defense strategy|defence strategy|"
    r"weapons? programs?|arms (?:race|transfer|sale)|"
    r"pla navy|pla air force|\bpla\b|"
    # Indo-Pacific maritime security (overrides soft local noise)
    r"\b(?:south china sea|west philippine sea|\bwps\b)\b|"
    r"\b(?:scarborough|second thomas|ayungin)(?:\s+shoal)?\b|"
    r"\b(?:naval|navy|coast guard) (?:patrol|confrontation|standoff|clash)\b|"
    # Substantive alliance / NATO outcomes (not thin meet-and-greet notices)
    r"\bnato (?:summit|communique|joint statement|strategic concept)\b|"
    r"\b(?:agrees?|agreed|approves?|approved|commits?|committed) .{0,40}"
    r"\b(?:force posture|munitions|deployment|deterrence)\b|"
    # Force-protection / CBRN / EW ops (keeps e.g. World Cup venue security)
    r"\bcbrn\b|"
    r"\b(?:chemical|biological|radiological|nuclear)\s+"
    r"(?:defense|defence|warfare|threat|response)\b|"
    r"\b(?:iew|electronic warfare)\b|"
    r"\bforce protection\b|"
    r"\bsecures? (?:multiple )?(?:stadiums?|venues?|installations?)\b)\b|"
    r"反介入|区域拒止|作战趋势|作战样式|多域|联合作战|弹药|高超音速|"
    r"学说|作战艺术|军演|演习|航母|潜艇|战备|导弹|"
    r"南海|西菲律宾海|南沙|"
    r"xu hướng tác chiến|tác chiến đa miền|học thuyết|"
    r"chiến lược quân sự|chiến lược quốc phòng|diễn tập|"
    r"tên lửa|tàu ngầm|tàu sân bay|đạn dược"
    r")",
    re.IGNORECASE,
)

# Topic tags that should float above unsorted Wire noise.
_STRATEGIC_WIRE_BOOST_TAGS = frozenset(
    {
        "combat-trends",
        "national-strategy",
        "force-posture",
        "exercises",
        "maritime",
        "procurement",
        "defense-policy",
        "cyber-operations",
        "security-cooperation",
    }
)


def _mask_legal_defense_phrases(text: str) -> str:
    """Remove legal-counsel 'defense/defence' so it is not treated as military."""
    return _LEGAL_DEFENSE_PHRASE_RE.sub(" ", text or "")


def is_soft_news_noise(text: str) -> bool:
    """True for lifestyle / sports / ceremonial / PR / civilian-disaster fluff.

    Absolute soft patterns (community day, meal PR, kitchen-table, …) always veto,
    even when the blurb name-drops multi-domain / missions. Other soft patterns
    still yield to hard operational substance (e.g. CBRN at a World Cup venue,
    merchant ship hit by cruise missiles).
    """
    if _ABSOLUTE_SOFT_NEWS_RE.search(text):
        return True
    if not _SOFT_NEWS_NOISE_RE.search(text):
        return False
    return not _HARD_MIL_OPERATIONAL_RE.search(text)


def is_military_context(text: str) -> bool:
    """Conventional military / defense reporting."""
    text = _mask_legal_defense_phrases(text)
    return bool(
        re.search(
            r"\b(military|defen[cs]e|armed forces|army|navy|air force|space force|"
            r"marine corps|marines|soldier|soldiers|troop|troops|service member|"
            r"warfighter|battalion|brigade|regiment|destroyer|frigate|amphibious|"
            r"special forces|special operations|national security|national defense|"
            r"secretary of defense|secdef|defense ministry|\bmod\b|"
            r"missile|weapon|warship|submarine|aircraft carrier|fighter|exercise|drill|"
            r"live-fire|war ?game|wargame|readiness|rearm|"
            r"deployment|base|"
            # Bare "maritime" matches civilian MARINA / industry authority noise —
            # require security/ops sense (or coast guard / port security).
            r"maritime (?:security|domain|patrol|militia|operations?|strategy|"
            r"awareness|forces?|strike)|"
            r"port security|coast ?guards?|cyber warfare|security cooperation|"
            r"peacekeeping|force posture|procurement|strategic|nuclear|"
            r"alliance|treaty|deterrence|sanctions|territorial dispute|"
            r"foreign policy|defense diplomacy|security policy|"
            r"self-defense forces|jsdf|pla navy|pla air force|"
            r"west philippine sea|scarborough shoal|second thomas shoal|ayungin|"
            r"armed forces of the philippines|\bafp\b|philippine navy|philippine coast ?guards?|"
            r"(?:us|u\.s\.|american|allied) forces\b|"
            r"\b(?:air ?strike|missile strike|naval strike|drone strike)s?\b|"
            r"\bnaval patrols?\b|"
            r"command and control|jadc2|joint all-domain|"
            r"a2/?ad|anti-access|area denial|\bisr\b|c4isr|c5isr|"
            r"doctrine|operational art|warfighting|multi-domain|"
            r"war plan|theater strategy|munitions|hypersonic|"
            r"national defense strategy|national security strategy|military strategy|"
            r"force design|force structure|"
            r"pentagon|department of defense|department of war|\bdod\b|\bdow\b)\b|"
            r"军事|国防|解放军|美军|美太空军|美陆军|美海军|美空军|太空军|"
            r"陆军|海军|空军|军演|演习|训练|武器|导弹|军舰|航母|战机|部署|基地|"
            r"南海|台海|海警|网络战|信息战|安全合作|维和|战略|同盟|威慑|制裁|"
            r"防卫|防衛|自卫队|自衛隊|海上自卫队|海上自衛隊|军事|軍事|演习|演習|导弹|ミサイル|"
            r"指挥控制|指挥系统|联合全域|战备|反介入|区域拒止|作战趋势|学说|作战艺术|"
            r"quân sự|quốc phòng|quân đội|hải quân|không quân|tên lửa|tàu chiến|"
            r"đối ngoại quốc phòng|chính sách đối ngoại|"
            r"xu hướng tác chiến|chiến lược quân sự|học thuyết|tác chiến đa miền|"
            r"pertahanan|militer|angkatan bersenjata|angkatan laut|angkatan udara|"
            r"กองทัพ|ทหาร|กลาโหม",
            text,
        )
    )


def is_military_cyber_context(text: str) -> bool:
    """
    Cyber / information / electronic warfare tied to military or defense forces.

    Accepts military cyber operations; rejects generic cybersecurity tips.
    """
    text = _mask_legal_defense_phrases(text)
    explicit = bool(
        re.search(
            r"\b("
            r"cyber warfare|cyberwar|cyber-war|cyber operations?|cyber ops|"
            r"cyber command|cyber force|cyber unit|cyber defense force|"
            r"afp cyber|philippine cyber|dict cybersecurity|ncert|"
            r"national cybersecurity|cyber defense|"
            r"network warfare|information warfare|electronic warfare|"
            r"c4isr|c5isr|military cyber|defense cyber|defence cyber|"
            r"weaponiz(?:e|ed|ing) (?:ai|data)|ai weaponization|"
            r"offensive cyber|defensive cyber|tailored access operations"
            r")\b|"
            r"网络战|网空作战|网络空间作战|信息战|电子战|赛博空间|网络攻防|"
            r"网络作战|网军|网络部队|网络司令部|指挥系统|特定入侵|"
            r"tác chiến mạng|chiến tranh mạng|tác chiến thông tin|tác chiến điện tử",
            text,
        )
    )
    if explicit:
        return True

    cyber_signal = bool(
        re.search(
            r"\b("
            r"cyber|cyberattack|cyber-attack|cybersecurity|hack|hacker|malware|ransomware|"
            r"phishing|vulnerability|vulnerabilities|exploit|intrusion|"
            r"ss7|zero[- ]day|spyware|surveillance|espionage|"
            r"data leak|breach|compromise|botnet|ddos|defacement|defaced"
            r")\b|"
            r"网络攻击|网络防御|漏洞|黑客|木马|勒索|钓鱼|入侵|监视|监控|"
            r"窃密|泄密|渗透|后门|零日|电信漏洞|篡改|网站遭篡改",
            text,
        )
    )
    if not cyber_signal:
        return False

    military_target = bool(
        re.search(
            r"\b("
            r"military|defen[cs]e|armed forces|army|navy|air force|space force|marine|"
            r"soldier|troop|pentagon|warship|submarine|base|battlefield|"
            r"combat|weapon|missile|command and control|force posture|"
            r"self-defense forces|jsdf|pla\b|nsa|cyber command|"
            r"philippines|philippine|\bafp\b|pcg|dnd|dict|ncert|"
            r"national security|critical infrastructure"
            r")\b|"
            r"军事|军工|军队|部队|美军|美太空军|美陆军|美海军|美空军|解放军|"
            r"自卫队|自衛隊|防卫省|防衛省|太空军|陆军|海军|空军|"
            r"作战|战场|战机|军舰|导弹|指挥|士兵|部队位置|国防部|国家安全局|"
            r"quân sự|quốc phòng|quân đội|hải quân|không quân|chiến đấu",
            text,
        )
    )
    return military_target


_DEFENSE_FOCUS_TOPIC_RE = re.compile(
    r"(?i)(?:"
    # National plans, strategy, policy, sovereignty and defense resources.
    r"\b(?:national|defen[cs]e|military|security|maritime|naval|nuclear)\s+"
    r"(?:plan|planning|strategy|strategic review|policy|doctrine|posture|budget|"
    r"spending|appropriation|modernization|modernisation|reform|white paper)\b|"
    r"\b(?:grand strategy|national interests?|strategic autonomy|territorial "
    r"integrity|sovereignty|sovereign rights?|territorial dispute|maritime "
    r"claims?|exclusive economic zone|\beez\b|indo[\s-]?pacific|"
    r"free and open indo[\s-]?pacific|deterrence|maritime defen[cs]e)\b|"
    r"\b(?:defen[cs]e|military)\s+(?:budget|spending|funding|appropriations?|"
    r"acquisition|procurement|contract|industry|industrial base|exports?|"
    r"imports?|cooperation|agreement)\b|"
    # Combat activity, operational posture and training.
    r"\b(?:combat|kinetic|military|naval|air|ground|amphibious|special "
    r"operations?|cyber|information|electronic)\s+"
    r"(?:operation|operations|mission|missions|campaign|campaigns|strike|"
    r"strikes|attack|attacks|offensive|warfare|deployment|deployments|patrol|"
    r"patrols|exercise|exercises|drill|drills|training)\b|"
    r"\b(?:air|missile|drone|naval|precision|long-range)\s+strikes?\b|"
    r"\b(?:war|warfare|battle|battlefield|invasion|incursion|interception|"
    r"blockade|mobilization|mobilisation|rules of engagement|live[\s-]?fire|"
    r"wargames?|war games?|combat readiness|operational readiness)\b|"
    # Force design, command arrangements and organizational structure.
    r"\b(?:force|fleet|army|navy|air force|marine corps|space force)\s+"
    r"(?:design|structure|posture|organization|organisation|reorganization|"
    r"reorganisation|modernization|modernisation|readiness|expansion|"
    r"transformation|command)\b|"
    r"\b(?:command and control|joint command|combatant command|order of battle|"
    r"battalion|brigade|regiment|division|squadron|task force)\b|"
    # Weapons, military platforms, munitions and enabling systems.
    r"\b(?:weapon systems?|weapons?|arms|munitions?|ammunition|missiles?|"
    r"rockets?|torpedoes?|artillery|howitzers?|tanks?|armou?red vehicles?|"
    r"fighter jets?|fighter aircraft|bombers?|combat aircraft|warships?|"
    r"destroyers?|frigates?|corvettes?|aircraft carriers?|submarines?|"
    r"military drones?|combat drones?|uncrewed systems?|unmanned systems?|"
    r"unmanned (?:aerial|surface|underwater) vehicles?|\buavs?\b|\buuvs?\b|"
    r"\bugvs?\b|\bccas?\b|autonomous (?:weapons?|vehicles?|systems?)|"
    r"air defen[cs]e|"
    r"missile defen[cs]e|radars?|sensors?|satellite targeting|"
    r"electronic warfare|c4isr|c5isr|jadc2|hypersonic)\b|"
    # Vietnamese equivalents of the requested focus areas.
    r"kế hoạch quốc phòng|chiến lược quốc gia|chiến lược quân sự|"
    r"chiến lược quốc phòng|chính sách quốc phòng|chính sách an ninh|"
    r"chủ quyền|toàn vẹn lãnh thổ|tác chiến|hoạt động quân sự|"
    r"chiến dịch quân sự|huấn luyện quân sự|diễn tập|bắn đạn thật|"
    r"tổ chức biên chế|cơ cấu lực lượng|thế trận|sẵn sàng chiến đấu|"
    r"ngân sách quốc phòng|chi tiêu quốc phòng|mua sắm quốc phòng|"
    r"công nghiệp quốc phòng|tác chiến mạng|chiến tranh mạng|"
    r"tác chiến điện tử|vũ khí|tên lửa|tàu chiến|tàu ngầm|máy bay chiến đấu|"
    # Common Chinese/Japanese defense coverage terms.
    r"国家战略|国家安全战略|国防战略|军事战略|国防政策|国防预算|军费|"
    r"主权|领土完整|作战|军事行动|军演|演习|实弹|训练|部队编制|"
    r"军队改革|战备|采购|军工|网络战|电子战|武器|导弹|军舰|潜艇|战机|"
    r"国家安全保障戦略|防衛戦略|防衛政策|防衛予算|主権|領土|"
    r"作戦|軍事演習|実弾|訓練|部隊編成|防衛装備|サイバー戦|電子戦|"
    r"ミサイル|潜水艦|戦闘機"
    r")"
)

_DEFENSE_PROGRAM_ACTION_RE = re.compile(
    r"(?i)(?:\b(?:"
    r"program|programme|project|portfolio|roadmap|requirement|competition|"
    r"plan|planning|develop|development|research|study|prototype|"
    r"test|testing|trial|training|exercise|drill|readiness|"
    r"operation|operations|mission|missions|attack|attacks|strike|strikes|"
    r"deploy|deployment|patrol|patrols|"
    r"search|searches|searching|seek|seeks|seeking|"
    r"validate|validation|demonstrat(?:e|es|ed|ion)|"
    r"produce|production|manufactur(?:e|ing)|deliver|delivery|"
    r"contract|award|order|select|selection|accept|accepts|accepted|"
    r"acquire|acquisition|procure|"
    r"procurement|moderniz(?:e|es|ed|ation)|modernis(?:e|es|ed|ation)|"
    r"upgrade|integration|capability|capabilities|field|fielding|"
    r"launch|flight|commission|decommission|replace|replacement|"
    r"funding|investment|autonomy|autonomous"
    r")\b|"
    r"发展|建设|规划|升级|研究|能力|试验|测试|训练|采购|列装"
    r")"
)


def is_defense_focus_topic(text: str) -> bool:
    """True for the strategic and operational defense topics kept on The Wire."""
    cleaned = _mask_legal_defense_phrases(text)
    if _DEFENSE_FOCUS_TOPIC_RE.search(cleaned):
        return True
    if _HARD_MIL_OPERATIONAL_RE.search(cleaned):
        return True
    return is_military_context(cleaned) and bool(
        _DEFENSE_PROGRAM_ACTION_RE.search(cleaned)
    )


_LOCAL_WEAPON_STORY_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:weapon systems?|weapons?|arms|munitions?|ammunition|missiles?|"
    r"rockets?|torpedoes?|artillery|howitzers?|tanks?|armou?red vehicles?|"
    r"fighter jets?|fighter aircraft|bombers?|combat aircraft|warships?|"
    r"destroyers?|frigates?|corvettes?|aircraft carriers?|submarines?|"
    r"military drones?|combat drones?|uncrewed systems?|unmanned systems?|"
    r"air defen[cs]e|missile defen[cs]e|hypersonic weapons?|radars?)\b|"
    r"vũ khí|tên lửa|ngư lôi|pháo binh|xe tăng|xe bọc thép|máy bay chiến đấu|"
    r"tàu chiến|tàu ngầm|máy bay không người lái|phòng không|siêu thanh|"
    r"武器|导弹|火箭|鱼雷|火炮|坦克|装甲车|战机|轰炸机|军舰|潜艇|无人机|防空|高超音速|"
    r"兵器|ミサイル|ロケット|魚雷|砲|戦車|装甲車|戦闘機|爆撃機|軍艦|潜水艦|無人機|防空"
    r")"
)

_CROSS_BORDER_WEAPON_IMPACT_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:regional|region-wide|international|global|cross[\s-]?border|"
    r"overseas|foreign|neighboring|neighbouring|allied|allies|alliance|"
    r"coalition|joint|multinational|bilateral|trilateral|partner|partnership|"
    r"co[\s-]?develop|licensed production|technology transfer|export|exports|"
    r"import|imports|arms transfer|weapons? transfer|foreign military sales?|"
    r"deliver(?:y|ies|ed)? to|suppl(?:y|ies|ied) to|sell|sale to|"
    r"(?:equip|equips|equipped|to equip) .{0,60}(?:navy|army|air force|frigates?|warships?)|"
    r"(?:lets?|allows?|enables?) .{0,50}(?:build(?:ing)?|produc(?:e|ing)|manufactur(?:e|ing)) (?:its|their) own|"
    r"orders? .{0,40}(?:fighters?|aircraft|warships?|helicopters?|missiles?|"
    r"submarines?|tanks?)|purchases? .{0,40}(?:fighters?|aircraft|warships?|missiles?|"
    r"submarines?|tanks?)|buys? .{0,40}(?:fighters?|aircraft|warships?|missiles?|"
    r"submarines?|tanks?)|"
    r"deterren(?:t|ce)|balance of power|strategic balance|arms race|"
    r"threaten|threat to|against|conflict|war in|invasion|sanctions?|"
    r"indo[\s-]?pacific|asia[\s-]?pacific|south china sea|east china sea|"
    r"taiwan strait|korean peninsula|middle east|europe|nato|asean|aukus|quad)\b|"
    r"khu vực|quốc tế|xuyên biên giới|nước ngoài|đồng minh|liên minh|liên quân|"
    r"đa quốc gia|song phương|ba bên|xuất khẩu|nhập khẩu|chuyển giao|cung cấp cho|"
    r"bán cho|răn đe|cân bằng sức mạnh|chạy đua vũ trang|đe dọa|chống lại|"
    r"xung đột|chiến tranh|xâm lược|trừng phạt|Ấn Độ Dương[\s-]?Thái Bình Dương|"
    r"Biển Đông|eo biển Đài Loan|bán đảo Triều Tiên|"
    r"Indo[\s-]?Pasifik|Asia[\s-]?Pasifik|keseimbangan kuasa|kerja sama|"
    r"地区|区域|国际|跨境|海外|盟友|联盟|联合|多国|双边|三边|出口|进口|转让|"
    r"提供给|出售给|威慑|力量平衡|军备竞赛|威胁|冲突|战争|入侵|制裁|印太|南海|台海|"
    r"地域|国際|越境|海外|同盟|連合|多国籍|二国間|三国間|輸出|輸入|移転|"
    r"供与|売却|抑止|勢力均衡|軍拡競争|脅威|紛争|戦争|侵攻|制裁|インド太平洋|南シナ海|台湾海峡"
    r")"
)

# A weapon story can concern one country and still be strategically important.
# Keep these signals out of the local-product cleanup: national choices, force
# posture/readiness, operational training, strategic systems, major spending,
# and the ability to sustain production are all useful to defense analysts.
_STRATEGIC_LOCAL_WEAPON_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:strategy|strategic|doctrine|posture|deterren(?:t|ce)|"
    r"national security|national defen[cs]e|defen[cs]e policy|military policy|"
    r"force structure|force design|order of battle|readiness|combat readiness|"
    r"operational readiness|modernization|modernisation|mobilization|mobilisation)\b|"
    r"\b(?:exercise|exercises|drill|drills|training|live[\s-]?fire|rimpac|"
    r"deployment|combat operation|military operation|operational mission|"
    r"patrol|interception|war game|wargame)\b|"
    r"\b(?:nuclear|thermonuclear|icbm|slbm|strategic bomber|hypersonic|"
    r"long[\s-]?range strike|second[\s-]?strike|missile silo|nuclear triad)\b|"
    r"\b(?:industrial base|shipbuilding capacity|production capacity|"
    r"production rate|mass production|supply chain|stockpile|inventory shortage|"
    r"munitions shortage|ammunition shortage|triple production|surge production)\b|"
    r"\b(?:weapons?|missiles?|munitions?|ammunition|rocket motors?|propulsion|"
    r"submarines?|air defen[cs]e|missile defen[cs]e)\b.{0,45}"
    r"\b(?:production|manufacturing|output|supply|stockpile|inventory)\b|"
    r"\b(?:boost|ramp|expand|increase|rebuild|double|triple|quadruple|"
    r"four[\s-]?fold)\b.{0,45}\b(?:production|output|supply|stockpile|inventory)\b|"
    r"\b(?:replacement|modernization|modernisation) (?:plan|program|programme)\b|"
    r"\b(?:ballistic missile submarine|strategic submarine|attack submarine|"
    r"missile tracking|missile warning|early warning|integrated air and missile defen[cs]e)\b|"
    r"\b(?:long[\s-]?range|precision strike|cross[\s-]?domain|non[\s-]?kinetic|"
    r"command and control|cyber command|network command|drone army|"
    r"defen[cs]e plans?|combat debut|operational capability|arsenal)\b|"
    r"\b(?:submarine|aircraft carrier|warship) contract\b|"
    r"\b(?:replacements?|successors?)\b.{0,45}\b(?:plan|program|arrive|gap|delay)\b|"
    r"\breplacements? for\b|"
    r"\bretir(?:e|es|ed|ing|ement)\b.{0,60}\breplacement\b|"
    r"\breplacement\b.{0,45}\b(?:arrive|gap|delay)\b|"
    r"\b(?:shipyard|shipbuilding|homeport|naval base|carrier dock|"
    r"dock for aircraft carriers|delivery (?:delay|backlog)|build schedule)\b|"
    r"\b(?:security|cyber) incident\b.{0,45}\b(?:offline|weapon|defen[cs]e system)\b|"
    r"\bdefen[cs]e systems?\b.{0,60}\b(?:offline|security incident|cyber incident)\b|"
    r"\b(?:aircraft carrier|submarine|warship)\b.{0,70}"
    r"\b(?:delivery backlog|takes? .{0,20}(?:years?|decade)|occupying the space|cannot build)\b|"
    r"\btakes? more than (?:a |one )?decade\b|"
    r"\bstrengthen(?:s|ed|ing)?\b.{0,45}\b(?:air|missile) defen[cs]e\b|"
    r"\b(?:pacific operations?|future fights?)\b|"
    r"\b\d{3,4}[,\d]*\s*(?:km|kilomet(?:er|re)s?|miles?)[\s-]+(?:range|strike)\b|"
    r"(?:\$|usd\s*)\d+(?:[.,]\d+)?\s*(?:billion|bn)\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:billion|bn)\s*(?:dollars?|usd)\b|"
    r"chiến lược|học thuyết|thế trận|răn đe|an ninh quốc gia|"
    r"chính sách quốc phòng|cơ cấu lực lượng|tổ chức biên chế|"
    r"sẵn sàng chiến đấu|hiện đại hóa|động viên|diễn tập|huấn luyện|"
    r"bắn đạn thật|triển khai lực lượng|hoạt động tác chiến|tuần tra|đánh chặn|"
    r"hạt nhân|siêu thanh|tấn công tầm xa|bộ ba hạt nhân|hầm phóng tên lửa|"
    r"công nghiệp quốc phòng|năng lực sản xuất|sản xuất hàng loạt|"
    r"chuỗi cung ứng|kho dự trữ|thiếu hụt đạn dược|"
    r"(?:tăng|mở rộng|đẩy mạnh|khôi phục).{0,30}(?:sản xuất|sản lượng|nguồn cung|kho dự trữ)|"
    r"kế hoạch thay thế|chương trình hiện đại hóa|"
    r"tàu ngầm tên lửa đạn đạo|tàu ngầm chiến lược|tàu ngầm tấn công|"
    r"cảnh báo sớm|theo dõi tên lửa|phòng thủ tên lửa tích hợp|"
    r"\b\d+(?:[.,]\d+)?\s*tỷ\s*(?:usd|đô la|dollar)?\b|"
    r"战略|学说|态势|威慑|国家安全|国防政策|部队编制|战备|现代化|动员|"
    r"军演|演习|实弹|训练|部署|作战行动|作战能力|巡逻|拦截|核武|高超音速|远程打击|"
    r"跨域|非动能|反导|网络司令部|"
    r"军工|生产能力|量产|供应链|库存|弹药短缺|"
    r"戦略|ドクトリン|態勢|抑止|国家安全保障|防衛政策|部隊編成|即応性|"
    r"近代化|動員|軍事演習|実弾|実射|訓練|配備|作戦|哨戒|迎撃|核|極超音速|"
    r"防衛産業|生産能力|量産|供給網|備蓄"
    r")"
)


def is_local_single_country_weapon_news(item: dict[str, Any]) -> bool:
    """True only for a weapon story confined to one country with no wider impact."""
    category = str(item.get("category") or "news").lower()
    if category not in {"news", "other"}:
        return False
    text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "title_vi", "summary", "description", "content")
    )
    if not _LOCAL_WEAPON_STORY_RE.search(text):
        return False

    slugs = _wire_content_geography_slugs(item)
    countries = {
        slug
        for slug in slugs
        if slug == "vietnam"
        or (slug.startswith("geo-") and slug not in _WIRE_REGION_GEO_SLUGS)
    }
    regions = {slug for slug in slugs if slug in _WIRE_REGION_GEO_SLUGS}
    if len(countries) != 1 or regions:
        return False
    if _CROSS_BORDER_WEAPON_IMPACT_RE.search(text):
        return False
    if _STRATEGIC_LOCAL_WEAPON_VALUE_RE.search(text):
        return False
    return True



_EXTERNAL_WEAPON_PROCUREMENT_RE = re.compile(
    r"(procurement|acquisition|purchase|purchases|purchasing|buy|buys|bought|"
    r"contract|award|awarded|mua sắm|mua|đặt mua|hợp đồng|采购|购买|订购|"
    r"order(?:s|ed)\s+(?:\d+|more|new|additional|another|two|three|four|"
    r"five|six|seven|eight|nine|ten))"
    r".{0,140}(weapon|arms|missile|rocket|torpedo|munition|ammunition|fighter|"
    r"aircraft|warship|frigate|destroyer|submarine|tank|artillery|radar|"
    r"air defense|vũ khí|tên lửa|đạn dược|máy bay chiến đấu|tàu chiến|"
    r"tàu ngầm|xe tăng|pháo binh|phòng không|武器|导弹|军舰|潜艇|战机)"
    r"|"
    r"(weapon|arms|missile|rocket|torpedo|munition|ammunition|fighter|"
    r"aircraft|warship|frigate|destroyer|submarine|tank|artillery|radar|"
    r"air defense|vũ khí|tên lửa|đạn dược|máy bay chiến đấu|tàu chiến|"
    r"tàu ngầm|xe tăng|pháo binh|phòng không|武器|导弹|军舰|潜艇|战机)"
    r".{0,140}(procurement|acquisition|purchase|purchases|purchasing|buy|"
    r"buys|bought|contract|award|awarded|mua sắm|mua|đặt mua|hợp đồng|采购|购买|订购|"
    r"order(?:s|ed)\s+(?:\d+|more|new|additional|another|two|three|four|"
    r"five|six|seven|eight|nine|ten))",
    re.IGNORECASE | re.DOTALL,
)



def is_external_weapon_procurement(item: dict[str, Any]) -> bool:
    """Reject weapon procurement outside SEA, Taiwan and China."""
    title_text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "title_vi")
    )
    if not _EXTERNAL_WEAPON_PROCUREMENT_RE.search(title_text):
        return False
    text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "title_vi", "summary", "description", "content")
    )
    if not _EXTERNAL_WEAPON_PROCUREMENT_RE.search(text):
        return False
    slugs = set(_wire_content_geography_slugs(item))
    if not slugs:
        return False
    allowed = {
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
    # If the article explicitly names an allowed geography, retain it.
    # Otherwise it is an out-of-scope country procurement story.
    return not bool(slugs & allowed)

def is_global_wire_noise(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split())
    return any(all(phrase in normalized for phrase in group) for group in GLOBAL_WIRE_NOISE_GROUPS)


def is_wire_relevant(item: dict[str, Any], *, prompt: str | None = None) -> bool:
    """Keep evidenced developments in the seven configured editorial clusters."""
    category = str(item.get("category") or "news").lower()
    # Substantive gates inspect article content, not feed/country metadata.
    content_text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "title_vi", "summary", "description", "content")
    ).casefold()
    _, explicit_exclude = evaluate_wire_filter_prompt(content_text, prompt=prompt)
    if is_global_wire_noise(content_text):
        return False
    if explicit_exclude:
        return False
    if category not in {"news", "other"}:
        return False
    return classify_wire_topics(item).relevant


def website_tag_slug(item: dict[str, Any]) -> str:
    """Return a stable site-* tag for RSS, Tor, sitemap, or Searx items."""
    candidate = str(
        item.get("feed_url")
        or item.get("website_url")
        or item.get("link")
        or item.get("url")
        or ""
    ).strip()
    host = (urlparse(candidate).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]

    # Onion addresses are unreadable and exceed Tag's max length; use source name.
    if host.endswith(".onion"):
        host = str(item.get("feed") or "onion").strip().lower()
    if not host:
        host = str(item.get("feed") or "").strip().lower()

    clean = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    if not clean:
        return ""
    return f"site-{clean}"[:64].rstrip("-")


def _classify_rss_item(item: dict[str, Any]) -> tuple[str, str, list[str], Decimal, int]:
    """Return (threat_source, severity, tag_slugs, evidence_score, wire_priority)."""
    discovery = str(item.get("discovery") or "")
    is_x_wire = discovery == "x-wire" or str(item.get("engine") or "") == "x_twitter"
    text = f"{item.get('title') or ''} {item.get('summary') or ''} {item.get('description') or ''}".lower()
    topic_match = classify_wire_topics(item)
    vietnam = is_vietnam_related(" ".join(topic_match.evidence))

    # Emit only source and substantive military topic tags.
    tags: list[str] = list(topic_match.tags)
    if is_x_wire:
        tags.append("x")
        handle = str(item.get("x_handle") or "").lstrip("@").strip().lower()
        if handle:
            tags.append(f"x-{re.sub(r'[^a-z0-9]+', '-', handle).strip('-')}"[:64])
    website_tag = website_tag_slug(item)
    if website_tag:
        tags.append(website_tag)

    if is_x_wire:
        source = Threat.Source.X
        severity = Threat.Severity.MEDIUM
        score = Decimal("60")
    else:
        source = Threat.Source.NEWS
        severity = Threat.Severity.MEDIUM
        score = Decimal("50")

    military_topics = {
        "exercises": r"\b(exercise|drill|maneuver|war game|joint training)\b|演习|军演|训练|diễn tập",
        "maritime": (
            r"\b(south china sea|west philippine sea|\bwps\b|taiwan strait|maritime|"
            r"coast guard|sovereignty|scarborough(?:\s+shoal)?|"
            r"second thomas(?:\s+shoal)?|ayungin(?:\s+shoal)?)\b|南海|台海|海警|主权"
        ),
        "procurement": r"\b(procurement|acquisition|contract|weapon|missile|aircraft|warship|equipment)\b|采购|武器|导弹|军舰|装备",
        "force-posture": (
            r"\b(force posture|deployment|base|reform|reorganization|strategy|"
            r"force design|force structure)\b|部署|基地|改革|战略|编制"
        ),
        "combat-trends": (
            r"\b(a2/?ad|anti[\s-]?access|area[\s-]?denial|\bisr\b|c4isr|c5isr|"
            r"operational art|doctrine|warfighting|multi[\s-]?domain|"
            r"joint all[\s-]?domain|jadc2|munitions?|hypersonic|"
            r"loitering munition|drone swarm|unmanned|uas|uuv|"
            r"combat trends?|tactical innovation|theater (?:war|strategy|campaign))\b|"
            r"反介入|区域拒止|作战趋势|作战样式|多域|联合作战|弹药|高超音速|"
            r"学说|作战艺术|"
            r"xu hướng tác chiến|tác chiến đa miền|học thuyết|đạn dược"
        ),
        "national-strategy": (
            r"\b(national (?:defense|defence|security|military) strategy|"
            r"defense strategy|defence strategy|military strategy|"
            r"\bnds\b|\bnss\b|strategic guidance|strategic concept|"
            r"deterrence strategy|war plans?|campaign plan|grand strategy)\b|"
            r"国家安全战略|国防战略|军事战略|战略指导|战争计划|"
            r"chiến lược quân sự|chiến lược quốc phòng|chiến lược an ninh quốc gia"
        ),
        "cyber-operations": (
            r"\b(cyber warfare|cyberwar|cyber operations?|cyber ops|cyber command|"
            r"network warfare|information warfare|electronic warfare|"
            r"military cyber|defense cyber|defence cyber|c4isr|c5isr|"
            r"afp cyber|philippine cyber|national cybersecurity|"
            r"offensive cyber|weaponiz(?:e|ed|ing) (?:ai|data)|"
            r"ss7|military phones?|combat targeting)\b|"
            r"网络战|信息战|电子战|网空作战|网络攻防|网军|网络部队|指挥系统|"
            r"电信漏洞|监视美军|作战打击|"
            r"tác chiến mạng|chiến tranh mạng"
        ),
        "security-cooperation": r"\b(defense cooperation|security cooperation|peacekeeping|military diplomacy)\b|防务合作|安全合作|维和",
        "defense-policy": (
            r"\b(defense policy|security policy|foreign policy|alliance|treaty|"
            r"deterrence|sanctions|territorial dispute)\b|防务政策|安全政策|外交政策|同盟|威慑|制裁"
        ),
        "analysis": r"\b(analysis|assessment|think tank|report|white paper)\b|分析|评估|报告|白皮书",
    }
    for topic, pattern in military_topics.items():
        if re.search(pattern, text, flags=re.IGNORECASE) and topic not in tags:
            tags.append(topic)

    wire_priority = 0
    if is_defense_security_signal(text):
        score = max(score, Decimal("62"))
        wire_priority = max(wire_priority, max(1, strategic_wire_priority() // 2))
    if is_x_wire and not vietnam:
        wire_priority = max(wire_priority, max(1, strategic_wire_priority() // 2))
    # Float combat-trend / national-strategy / posture stories above unsorted noise.
    if any(tag in _STRATEGIC_WIRE_BOOST_TAGS for tag in tags):
        wire_priority = max(wire_priority, max(1, strategic_wire_priority() // 2))
    if "combat-trends" in tags or "national-strategy" in tags:
        wire_priority = max(wire_priority, secrss_wire_priority())
        score = max(score, Decimal("60"))
    if vietnam:
        severity = Threat.Severity.HIGH
        score = max(score, Decimal("85"))
        wire_priority = vietnam_wire_priority()
        if "vietnam" not in tags:
            tags.append("vietnam")
    else:
        feed_blob = " ".join(
            str(item.get(key) or "")
            for key in ("feed", "feed_url", "link", "url", "website_url")
        ).casefold()
        if "secrss.com" in feed_blob:
            wire_priority = max(wire_priority, secrss_wire_priority())

    for geo_tag in detect_geography_tag_slugs(
        *topic_match.evidence,
        country_code="",
        feed_url="",
        source_url="",
    ):
        if geo_tag not in tags:
            tags.append(geo_tag)

    return source, severity, tags, score, wire_priority


def _password_fingerprint(password: str) -> str:
    return password_fingerprint(password)


def _ensure_tags(slugs: list[str]) -> list[Tag]:
    out: list[Tag] = []
    for slug in slugs:
        clean = (slug or "").strip().lower().replace(" ", "-")[:64]
        if not clean:
            continue
        tag, _ = Tag.objects.get_or_create(
            slug=clean, defaults={"name": TOPIC_LABELS.get(clean, clean.replace("-", " ").title())}
        )
        out.append(tag)
    return out


@transaction.atomic
def ingest_stealer_content(
    *,
    leak_id: int | None,
    content: str,
    stealer_family: str | None = None,
    create_leak: bool = False,
    leak_title: str = "Stealer log ingest",
) -> dict[str, Any]:
    leak = None
    if leak_id:
        leak = DataLeak.objects.select_for_update().get(pk=leak_id)
    elif create_leak:
        leak = DataLeak.objects.create(
            title=leak_title,
            leak_type=DataLeak.LeakType.STEALER_LOG,
            severity=DataLeak.Severity.HIGH,
            source=DataLeak.Source.OTHER,
            description="Created by Celery stealer ingest worker",
        )

    parsed = parse_stealer_log(content, stealer_family=stealer_family)
    created = 0
    skipped = 0

    for row in parsed:
        exists = CompromisedCredential.objects.filter(
            leak=leak,
            email=row.email,
            username=row.username,
            domain=row.domain,
            password_fingerprint=_password_fingerprint(row.password) or "",
        ).exists()
        if exists and row.password:
            skipped += 1
            continue
        # Also skip identical raw triple when no password fingerprint yet
        if not row.password:
            skipped += 1
            continue

        CompromisedCredential.objects.create(
            leak=leak,
            email=row.email,
            username=row.username,
            password=encrypt_secret(row.password),
            password_fingerprint=_password_fingerprint(row.password),
            url=row.url,
            domain=row.domain,
            stealer_family=(
                row.stealer_family
                if row.stealer_family
                in {
                    choice.value
                    for choice in CompromisedCredential.StealerFamily
                }
                else CompromisedCredential.StealerFamily.UNKNOWN
            ),
            raw_line=row.raw_line,
            metadata={"ingested_by": "workers.parse_stealer_log"},
        )
        created += 1

        if row.domain:
            ind, _ = Indicator.objects.update_or_create(
                ioc_type=Indicator.Type.DOMAIN,
                normalized_value=row.domain.lower(),
                defaults={
                    "value": row.domain,
                    "source": "stealer_log",
                    "confidence": Indicator.Confidence.MEDIUM,
                    "description": f"Domain seen in stealer credentials (leak={getattr(leak, 'id', None)})",
                    "last_seen": timezone.now(),
                    "is_active": True,
                },
            )
            match_indicator_against_rules(ind)

    if leak is not None:
        leak.record_count = leak.credentials.count()
        if leak.leak_type != DataLeak.LeakType.STEALER_LOG:
            leak.leak_type = DataLeak.LeakType.STEALER_LOG
        leak.save(update_fields=["record_count", "leak_type", "updated_at"])
        match_leak_against_rules(leak)

    return {
        "leak_id": getattr(leak, "id", None),
        "parsed": len(parsed),
        "created": created,
        "skipped": skipped,
    }


def _safe_decimal(value: Any, places_as_str: str = "0.0") -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def ingest_cve_items(items: list[dict[str, Any]]) -> dict[str, int]:
    created = 0
    updated = 0
    translate_ids: list[int] = []
    for item in items:
        cve_id = (
            item.get("id")
            or item.get("cve")
            or item.get("cve_id")
            or ""
        )
        cve_id = str(cve_id).strip().upper()
        if not cve_id.startswith("CVE-"):
            continue

        summary = (
            item.get("summary")
            or item.get("description")
            or item.get("details")
            or ""
        )
        cvss = _safe_decimal(
            item.get("cvss")
            or item.get("cvss3")
            or (item.get("metrics") or {}).get("cvss")
        )
        published = parse_datetime(str(item.get("Published") or item.get("published") or ""))
        if published is None:
            published = timezone.now()

        severity = Threat.Severity.MEDIUM
        if cvss is not None:
            if cvss >= Decimal("9.0"):
                severity = Threat.Severity.CRITICAL
            elif cvss >= Decimal("7.0"):
                severity = Threat.Severity.HIGH
            elif cvss >= Decimal("4.0"):
                severity = Threat.Severity.MEDIUM
            else:
                severity = Threat.Severity.LOW

        title = f"{cve_id}: {(summary or 'No summary')[:180]}"
        obj, was_created = Threat.objects.update_or_create(
            title=title[:512],
            source=Threat.Source.CVE_FEED,
            defaults={
                "summary": str(summary)[:5000],
                "severity": severity,
                "status": Threat.Status.NEW,
                "published_at": published,
                "cvss_score": cvss,
                "cve_ids": [cve_id],
                "evidence_score": cvss or Decimal("0"),
                "raw_payload": item,
                "source_url": f"https://cve.circl.lu/cve/{cve_id}",
            },
        )
        Indicator.objects.update_or_create(
            ioc_type=Indicator.Type.CVE,
            normalized_value=cve_id.lower(),
            defaults={
                "value": cve_id,
                "source": "cve.circl.lu",
                "confidence": Indicator.Confidence.HIGH,
                "description": str(summary)[:2000],
                "last_seen": timezone.now(),
                "is_active": True,
                "metadata": {"cvss": str(cvss) if cvss is not None else None},
            },
        )
        obj.tags.add(*_ensure_tags(["site-cve-circl-lu"]))
        if was_created:
            created += 1
            match_threat_against_rules(obj)
            from apps.integrations.ai.translate import apply_inline_rule_translation

            if not apply_inline_rule_translation(obj):
                translate_ids.append(obj.id)
        else:
            updated += 1
            obj.indicators.add(
                *Indicator.objects.filter(
                    ioc_type=Indicator.Type.CVE, normalized_value=cve_id.lower()
                )[:1]
            )

    if translate_ids:
        from apps.integrations.ai.translate import enqueue_title_translations

        enqueue_title_translations(translate_ids)
    return {"created": created, "updated": updated, "processed": created + updated}


_ONION_IN_URL_RE = re.compile(r"\.onion(?:[/:?#]|$)", re.I)
_DOMAIN_HINT_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)\b",
    re.I,
)


def _is_onion_url(value: str) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return False
    host = (urlparse(text).hostname or "").casefold()
    return host.endswith(".onion") or bool(_ONION_IN_URL_RE.search(text))


def _ransomware_clearnet_url(item: dict[str, Any]) -> str:
    """Prefer ransomware.live / clearnet detail pages — never onion claim URLs."""
    for key in ("url", "post_url", "link", "website"):
        value = str(item.get(key) or "").strip()
        if value.lower().startswith(("https://", "http://")) and not _is_onion_url(value):
            return value
    return "https://www.ransomware.live/"


def _scrub_onion_from_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Drop onion destinations from stored Wire payload (metadata-only policy)."""
    out: dict[str, Any] = {}
    for key, value in dict(item or {}).items():
        if isinstance(value, str) and _is_onion_url(value):
            continue
        if key in {"claim_url", "screenshot"} and isinstance(value, str) and _is_onion_url(value):
            continue
        out[key] = value
    return out


def _domain_hint_from_text(text: str) -> str:
    match = _DOMAIN_HINT_RE.search(str(text or ""))
    if not match:
        return ""
    host = match.group(1).casefold()
    if host.endswith(".onion") or host in {"www.ransomware.live", "ransomware.live"}:
        return ""
    return host


def ingest_ransomware_items(items: list[dict[str, Any]]) -> dict[str, int]:
    created = 0
    updated = 0
    translate_ids: list[int] = []
    for item in items:
        victim = (
            item.get("victim")
            or item.get("post_title")
            or item.get("name")
            or item.get("company")
            or ""
        )
        group = item.get("group") or item.get("group_name") or item.get("gang") or "unknown"
        victim = str(victim).strip()
        if not victim:
            continue
        title = f"Ransomware: {victim} ({group})"[:512]
        discovered = parse_datetime(
            str(item.get("discovered") or item.get("published") or item.get("date") or "")
        )
        source_url = _ransomware_clearnet_url(item)
        safe_payload = _scrub_onion_from_payload(item)
        description = str(
            item.get("description") or item.get("summary") or item.get("activity") or ""
        ).strip()
        domain = str(item.get("domain") or item.get("website") or "").strip()
        if not domain:
            domain = _domain_hint_from_text(description)
        if description:
            summary = description[:5000]
        elif domain:
            summary = f"Victim reported by ransomware.live / group={group} / domain={domain}"
        else:
            summary = f"Victim reported by ransomware.live / group={group}"

        vietnam = threat_looks_vietnam_related(
            title=title,
            summary=summary,
            source_url=source_url,
            raw_payload=safe_payload,
            country_code=str(item.get("country") or item.get("country_code") or ""),
        )
        tags = ["site-ransomware-live"]
        severity = Threat.Severity.HIGH
        score = Decimal("70")
        wire_priority = strategic_wire_priority()
        if vietnam:
            tags.append("vietnam")
            score = Decimal("85")
            wire_priority = vietnam_wire_priority()
        for geo_tag in detect_geography_tag_slugs(
            title,
            summary,
            description,
            str(item.get("country") or ""),
            country_code=str(item.get("country_code") or item.get("country") or ""),
        ):
            if geo_tag not in tags:
                tags.append(geo_tag)

        obj, was_created = Threat.objects.update_or_create(
            title=title,
            source=Threat.Source.RANSOMWARE,
            defaults={
                "summary": summary,
                "severity": severity,
                "status": Threat.Status.NEW,
                "published_at": clamp_published_at(discovered, now=timezone.now()),
                "evidence_score": score,
                "wire_priority": wire_priority,
                "raw_payload": safe_payload,
                "source_url": source_url[:2048],
            },
        )
        obj.tags.add(*_ensure_tags(tags))
        if domain:
            host = domain.replace("https://", "").replace("http://", "").split("/")[0]
            if host and not host.casefold().endswith(".onion"):
                Indicator.objects.update_or_create(
                    ioc_type=Indicator.Type.DOMAIN,
                    normalized_value=host.lower(),
                    defaults={
                        "value": host,
                        "source": "ransomware.live",
                        "confidence": Indicator.Confidence.MEDIUM,
                        "last_seen": timezone.now(),
                        "is_active": True,
                    },
                )
        if was_created:
            created += 1
            match_threat_against_rules(obj)
            from apps.integrations.ai.translate import apply_inline_rule_translation

            if not apply_inline_rule_translation(obj):
                translate_ids.append(obj.id)
        else:
            updated += 1
    if translate_ids:
        from apps.integrations.ai.translate import enqueue_title_translations

        enqueue_title_translations(translate_ids)
    return {"created": created, "updated": updated, "processed": created + updated}


def ingest_rss_items(items: list[dict[str, Any]], *, source_label: str = "rss") -> dict[str, int]:
    """Create Wire threats from RSS items.

    Age window: all countries use the latest 30 days by default.

    Already-ingested items (same normalized source_url or title+source) are
    skipped — no rewrite.
    """
    created = 0
    updated = 0
    skipped_old = 0
    skipped_existing = 0
    skipped_irrelevant = 0
    skipped_unsafe = 0
    general_days = int(getattr(settings, "WIRE_MAX_AGE_DAYS", 30) or 30)
    vietnam_days = int(
        getattr(settings, "WIRE_VIETNAM_MAX_AGE_DAYS", general_days)
        or general_days
    )
    now = timezone.now()
    translate_ids: list[int] = []

    from apps.intel.wire_urls import find_threat_by_normalized_url, normalize_wire_url
    from apps.workers.feeds.forum_safety import prepare_wire_item_for_safety

    # Newest first so a large batch surfaces fresh high-signal intel before older rows.
    def _item_sort_key(row: dict[str, Any]) -> float:
        published = resolve_item_published(row)
        return published.timestamp() if published is not None else 0.0

    for raw_item in sorted(items, key=_item_sort_key, reverse=True):
        item = prepare_wire_item_for_safety(raw_item)
        if item is None:
            if str(raw_item.get("title") or "").strip():
                skipped_unsafe += 1
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        from apps.workers.feeds.clients import absolutize_feed_link

        link = normalize_wire_url(
            absolutize_feed_link(
                str(item.get("link") or item.get("url") or ""),
                str(item.get("feed_url") or ""),
            )
        )[:2048]
        if link:
            item["link"] = link
        summary = str(item.get("summary") or item.get("description") or "")[:5000]
        if not is_wire_relevant(item):
            skipped_irrelevant += 1
            continue
        published = resolve_item_published(item)
        if published is not None:
            item["published"] = published.isoformat()
        vietnam_hint = threat_looks_vietnam_related(
            title=title,
            summary=summary,
            source_url=link,
            raw_payload=item,
            country_code=str(item.get("country_code") or ""),
        )
        max_age_days = vietnam_days if vietnam_hint else general_days
        if is_secrss_item(item):
            max_age_days = max(max_age_days, secrss_max_age_days())
        # Missing/unparseable dates must not be treated as "published now".
        if not is_within_max_age(published, max_age_days=max_age_days, now=now):
            skipped_old += 1
            continue

        # Cheap existence check before classify/write (avoid re-scanning known items).
        existing = find_threat_by_normalized_url(link) if link else None
        if existing is None:
            category = str(item.get("category") or "news").lower()
            if category == "cert":
                threat_source_guess = Threat.Source.CERT
            elif category == "ransomware":
                threat_source_guess = Threat.Source.RANSOMWARE
            else:
                threat_source_guess = Threat.Source.NEWS
            existing = (
                Threat.objects.filter(title=title[:512], source=threat_source_guess)
                .only("id", "raw_payload")
                .first()
            )
        if existing is not None:
            image_url = str(item.get("image_url") or "").strip()
            payload = dict(existing.raw_payload or {})
            if image_url and not str(payload.get("image_url") or "").strip():
                payload["image_url"] = image_url[:2048]
                existing.raw_payload = payload
                existing.save(update_fields=["raw_payload", "updated_at"])
                updated += 1
                continue
            skipped_existing += 1
            continue

        threat_source, severity, tag_slugs, score, wire_priority = _classify_rss_item(item)
        published_at = clamp_published_at(published, now=now)
        from django.db import IntegrityError

        try:
            obj = Threat.objects.create(
                title=title[:512],
                source=threat_source,
                status=Threat.Status.NEW,
                summary=summary,
                severity=severity,
                evidence_score=score,
                wire_priority=wire_priority,
                wire_relevant=True,
                raw_payload={**item, "feed_source": source_label,
                             "wire_scope": classify_wire_topics(item).as_payload()},
                source_url=link,
                published_at=published_at,
            )
        except IntegrityError:
            # Concurrent ingest of the same normalized URL — treat as skip.
            if link and find_threat_by_normalized_url(link) is not None:
                skipped_existing += 1
                continue
            raise
        tags = _ensure_tags(tag_slugs)
        if tags:
            obj.tags.add(*tags)
        created += 1
        match_threat_against_rules(obj)
        from apps.integrations.ai.translate import apply_inline_rule_translation

        if not apply_inline_rule_translation(obj):
            translate_ids.append(obj.id)

    if translate_ids:
        from apps.integrations.ai.translate import enqueue_title_translations

        enqueue_title_translations(translate_ids)

    # Keep the Wire bounded between daily housekeeping runs. Deletion happens
    # oldest-first in small batches to avoid a large transaction after ingest.
    from apps.intel.retention import trim_wire_overflow

    trimmed_oldest = trim_wire_overflow()

    return {
        "created": created,
        "updated": updated,
        "skipped_old": skipped_old,
        "skipped_existing": skipped_existing,
        "skipped_irrelevant": skipped_irrelevant,
        "skipped_unsafe": skipped_unsafe,
        "trimmed_oldest": trimmed_oldest,
        "processed": created + updated,
    }
