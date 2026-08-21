"""Deterministic country and region tagging for Wire threat content."""

from __future__ import annotations

import re
from urllib.parse import urlparse


# Slugs are prefixed so the UI can always render geographic tags after topics.
# Vietnam keeps its established slug because it also controls Wire retention/priority.
_GEO_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "vietnam",
        (
            "vietnam",
            "viet nam",
            "việt nam",
            "vietnamese",
            "hanoi",
            "hà nội",
            "ha noi",
            "ho chi minh",
            "hồ chí minh",
            "saigon",
        ),
    ),
    (
        "geo-united-states",
        (
            "united states",
            "u.s.",
            "usa",
            "u.s.a.",
            "american",
            "pentagon",
            "washington dc",
            "us army",
            "us navy",
            "us air force",
            "us marine",
            "us forces",
            "department of defense",
            "mỹ",
            "hoa kỳ",
            "美国",
            "美军",
            "アメリカ",
            "米国",
        ),
    ),
    (
        "geo-united-kingdom",
        (
            "united kingdom",
            "u.k.",
            "britain",
            "british",
            "british army",
            "british navy",
            "england",
            "scotland",
            "wales",
            "london",
            "anh quốc",
            "vương quốc anh",
            "イギリス",
            "英国",
        ),
    ),
    ("geo-canada", ("canada", "canadian", "ottawa", "カナダ", "加国")),
    (
        "geo-australia",
        (
            "australia",
            "australian",
            "canberra",
            "adf",
            "úc",
            "オーストラリア",
            "豪州",
        ),
    ),
    (
        "geo-new-zealand",
        ("new zealand", "wellington", "ニュージーランド", "nzdf"),
    ),
    (
        "geo-china",
        (
            "china",
            "chinese",
            "beijing",
            "prc",
            "people's republic of china",
            "people's liberation army",
            "pla navy",
            "pla",
            "trung quốc",
            "trung hoa",
            "中国",
            "解放军",
            "中国人民解放軍",
        ),
    ),
    (
        "geo-russia",
        (
            "russia",
            "russian",
            "moscow",
            "nga",
            "俄罗斯",
            "俄羅斯",
            "ロシア",
            "露国",
        ),
    ),
    (
        "geo-ukraine",
        (
            "ukraine",
            "ukrainian",
            "kyiv",
            "kiev",
            "ukraina",
            "乌克兰",
            "烏克蘭",
            "ウクライナ",
        ),
    ),
    ("geo-germany", ("germany", "german", "berlin", "ドイツ", "独国", "đức")),
    (
        "geo-france",
        (
            "france",
            "french",
            "paris",
            "フランス",
            "仏国",
            "cộng hòa pháp",
            "nước pháp",
        ),
    ),
    (
        "geo-italy",
        ("italy", "italian", "イタリア", "伊国", "italia"),
    ),
    ("geo-spain", ("spain", "spanish", "スペイン")),
    (
        "geo-netherlands",
        ("netherlands", "dutch", "オランダ", "hà lan"),
    ),
    ("geo-belgium", ("belgium", "belgian", "ベルギー")),
    ("geo-poland", ("poland", "polish", "warsaw", "ポーランド")),
    ("geo-romania", ("romania", "romanian")),
    ("geo-estonia", ("estonia", "estonian", "tallinn", "エストニア")),
    ("geo-latvia", ("latvia", "latvian", "riga")),
    ("geo-lithuania", ("lithuania", "lithuanian", "vilnius")),
    ("geo-switzerland", ("switzerland", "swiss", "スイス")),
    ("geo-austria", ("austria", "austrian", "オーストリア")),
    ("geo-sweden", ("sweden", "swedish", "スウェーデン", "thụy điển")),
    ("geo-norway", ("norway", "norwegian", "ノルウェー")),
    ("geo-denmark", ("denmark", "danish", "デンマーク", "đan mạch")),
    ("geo-finland", ("finland", "finnish", "フィンランド")),
    ("geo-ireland", ("ireland", "irish")),
    (
        "geo-portugal",
        ("portugal", "portuguese", "ポルトガル", "bồ đào nha"),
    ),
    ("geo-czech-republic", ("czech republic", "czechia", "czech")),
    ("geo-slovakia", ("slovakia", "slovak")),
    ("geo-greece", ("greece", "greek")),
    (
        "geo-turkey",
        ("turkey", "türkiye", "turkish", "トルコ", "thổ nhĩ kỳ"),
    ),
    ("geo-israel", ("israel", "israeli", "イスラエル")),
    ("geo-iran", ("iran", "iranian", "イラン")),
    ("geo-iraq", ("iraq", "iraqi")),
    ("geo-saudi-arabia", ("saudi arabia", "saudi")),
    ("geo-united-arab-emirates", ("united arab emirates", "u.a.e.", "uae", "emirati")),
    ("geo-qatar", ("qatar", "qatari")),
    (
        "geo-india",
        ("india", "indian", "new delhi", "ấn độ", "インド"),
    ),
    ("geo-pakistan", ("pakistan", "pakistani", "islamabad")),
    ("geo-bangladesh", ("bangladesh", "bangladeshi")),
    ("geo-sri-lanka", ("sri lanka",)),
    (
        "geo-japan",
        (
            "japan",
            "japanese",
            "tokyo",
            "jsdf",
            "self-defense forces",
            "nhật bản",
            "nhật",
            "日本",
            "東京",
            "自衛隊",
            "自卫队",
            "防衛省",
            "防衛大臣",
            "防衛相",
        ),
    ),
    (
        "geo-south-korea",
        (
            "south korea",
            "south korean",
            "republic of korea",
            "seoul",
            "rok",
            "hàn quốc",
            "韩国",
            "韓國",
            "韓国",
            "ソウル",
        ),
    ),
    (
        "geo-north-korea",
        (
            "north korea",
            "north korean",
            "dprk",
            "pyongyang",
            "triều tiên",
            "朝鲜",
            "朝鮮",
            "北朝鮮",
        ),
    ),
    (
        "geo-taiwan",
        (
            "taiwan",
            "taiwanese",
            "taipei",
            "đài loan",
            "台湾",
            "台灣",
            "臺灣",
            "台北",
        ),
    ),
    ("geo-singapore", ("singapore", "singaporean", "シンガポール")),
    ("geo-malaysia", ("malaysia", "malaysian", "kuala lumpur", "マレーシア")),
    (
        "geo-indonesia",
        ("indonesia", "indonesian", "jakarta", "tni", "インドネシア"),
    ),
    (
        "geo-thailand",
        ("thailand", "thai", "bangkok", "thái lan", "タイ"),
    ),
    (
        "geo-philippines",
        (
            "philippines",
            "philippine",
            "filipino",
            "manila",
            "west philippine sea",
            "scarborough",
            "ayungin",
            "second thomas",
            "フィリピン",
        ),
    ),
    (
        "geo-myanmar",
        ("myanmar", "burma", "burmese", "miến điện", "ミャンマー"),
    ),
    (
        "geo-cambodia",
        ("cambodia", "cambodian", "khmer", "phnom penh", "campuchia", "カンボジア"),
    ),
    (
        "geo-laos",
        ("laos", "lao", "laotian", "vientiane", "lào", "ラオス"),
    ),
    ("geo-brazil", ("brazil", "brazilian")),
    ("geo-mexico", ("mexico", "mexican")),
    ("geo-argentina", ("argentina", "argentinian")),
    ("geo-chile", ("chile", "chilean")),
    ("geo-colombia", ("colombia", "colombian")),
    ("geo-south-africa", ("south africa", "south african")),
    ("geo-nigeria", ("nigeria", "nigerian")),
    ("geo-kenya", ("kenya", "kenyan")),
    ("geo-egypt", ("egypt", "egyptian")),
    ("geo-southeast-asia", ("southeast asia", "south-east asia", "asean")),
    (
        "geo-asia-pacific",
        (
            "asia pacific",
            "asia-pacific",
            "apac",
            "indo-pacific",
            "インド太平洋",
        ),
    ),
    ("geo-middle-east", ("middle east", "middle eastern")),
    ("geo-europe", ("europe", "european union", "eu member")),
    ("geo-latin-america", ("latin america", "latin american", "latam")),
    ("geo-north-america", ("north america", "north american")),
    ("geo-africa", ("africa", "african union")),
    ("geo-emea", ("emea",)),
)

_COUNTRY_CODE_SLUGS = {
    "VN": "vietnam",
    "VNM": "vietnam",
    "US": "geo-united-states",
    "USA": "geo-united-states",
    "GB": "geo-united-kingdom",
    "GBR": "geo-united-kingdom",
    "CA": "geo-canada",
    "AU": "geo-australia",
    "CN": "geo-china",
    "RU": "geo-russia",
    "UA": "geo-ukraine",
    "DE": "geo-germany",
    "FR": "geo-france",
    "IN": "geo-india",
    "JP": "geo-japan",
    "KR": "geo-south-korea",
    "KP": "geo-north-korea",
    "SG": "geo-singapore",
    "MY": "geo-malaysia",
    "ID": "geo-indonesia",
    "TH": "geo-thailand",
    "PH": "geo-philippines",
    "TW": "geo-taiwan",
    "KH": "geo-cambodia",
    "LA": "geo-laos",
    "BR": "geo-brazil",
    "MX": "geo-mexico",
    "AR": "geo-argentina",
    "CL": "geo-chile",
    "CO": "geo-colombia",
    "ZA": "geo-south-africa",
    "NG": "geo-nigeria",
    "KE": "geo-kenya",
    "EG": "geo-egypt",
    "SA": "geo-saudi-arabia",
    "AE": "geo-united-arab-emirates",
    "QA": "geo-qatar",
    "TR": "geo-turkey",
    "PL": "geo-poland",
    "RO": "geo-romania",
    "EE": "geo-estonia",
    "LV": "geo-latvia",
    "LT": "geo-lithuania",
    "NL": "geo-netherlands",
    "BE": "geo-belgium",
    "ES": "geo-spain",
    "IT": "geo-italy",
    "SE": "geo-sweden",
    "NO": "geo-norway",
    "DK": "geo-denmark",
    "FI": "geo-finland",
    "CH": "geo-switzerland",
    "AT": "geo-austria",
    "IE": "geo-ireland",
    "PT": "geo-portugal",
    "GR": "geo-greece",
    "CZ": "geo-czech-republic",
    "SK": "geo-slovakia",
    "PK": "geo-pakistan",
    "BD": "geo-bangladesh",
    "LK": "geo-sri-lanka",
    "NZ": "geo-new-zealand",
    "HK": "geo-china",
    "IR": "geo-iran",
    "IQ": "geo-iraq",
    "IL": "geo-israel",
}

# ccTLD / common second-level cc suffixes → ISO 3166-1 alpha-2.
_COMPOUND_TLD_CODES: dict[str, str] = {
    "co.uk": "GB",
    "org.uk": "GB",
    "ac.uk": "GB",
    "gov.uk": "GB",
    "com.au": "AU",
    "net.au": "AU",
    "org.au": "AU",
    "co.jp": "JP",
    "ne.jp": "JP",
    "or.jp": "JP",
    "com.br": "BR",
    "org.br": "BR",
    "com.vn": "VN",
    "org.vn": "VN",
    "gov.vn": "VN",
    "edu.vn": "VN",
    "co.id": "ID",
    "or.id": "ID",
    "web.id": "ID",
    "com.my": "MY",
    "org.my": "MY",
    "com.sg": "SG",
    "org.sg": "SG",
    "co.kr": "KR",
    "or.kr": "KR",
    "com.tw": "TW",
    "org.tw": "TW",
    "com.kh": "KH",
    "org.kh": "KH",
    "com.la": "LA",
    "org.la": "LA",
    "com.tr": "TR",
    "org.tr": "TR",
    "com.mx": "MX",
    "org.mx": "MX",
    "com.ar": "AR",
    "org.ar": "AR",
    "co.za": "ZA",
    "org.za": "ZA",
    "com.ng": "NG",
    "org.ng": "NG",
    "co.nz": "NZ",
    "org.nz": "NZ",
    "com.hk": "HK",
    "org.hk": "HK",
}

_CCTLD_CODES: dict[str, str] = {
    "vn": "VN",
    "uk": "GB",
    "us": "US",
    "de": "DE",
    "fr": "FR",
    "jp": "JP",
    "ru": "RU",
    "in": "IN",
    "au": "AU",
    "br": "BR",
    "id": "ID",
    "th": "TH",
    "ph": "PH",
    "my": "MY",
    "sg": "SG",
    "kr": "KR",
    "tw": "TW",
    "kh": "KH",
    "la": "LA",
    "cn": "CN",
    "it": "IT",
    "es": "ES",
    "nl": "NL",
    "pl": "PL",
    "ro": "RO",
    "ua": "UA",
    "za": "ZA",
    "ng": "NG",
    "mx": "MX",
    "ar": "AR",
    "cl": "CL",
    "co": "CO",
    "pe": "PE",
    "ke": "KE",
    "eg": "EG",
    "sa": "SA",
    "ae": "AE",
    "qa": "QA",
    "tr": "TR",
    "se": "SE",
    "no": "NO",
    "dk": "DK",
    "fi": "FI",
    "ch": "CH",
    "at": "AT",
    "ie": "IE",
    "pt": "PT",
    "gr": "GR",
    "cz": "CZ",
    "sk": "SK",
    "pk": "PK",
    "bd": "BD",
    "lk": "LK",
    "nz": "NZ",
    "hk": "HK",
    "ir": "IR",
    "iq": "IQ",
    "il": "IL",
    "ca": "CA",
    "be": "BE",
}

_COUNTRY_NAMES: dict[str, str] = {
    "VN": "Vietnam",
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "CN": "China",
    "RU": "Russia",
    "UA": "Ukraine",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "PL": "Poland",
    "RO": "Romania",
    "CH": "Switzerland",
    "AT": "Austria",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "IE": "Ireland",
    "PT": "Portugal",
    "GR": "Greece",
    "CZ": "Czech Republic",
    "SK": "Slovakia",
    "TR": "Turkey",
    "IL": "Israel",
    "IR": "Iran",
    "IQ": "Iraq",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "QA": "Qatar",
    "IN": "India",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "LK": "Sri Lanka",
    "JP": "Japan",
    "KR": "South Korea",
    "TW": "Taiwan",
    "SG": "Singapore",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "TH": "Thailand",
    "PH": "Philippines",
    "KH": "Cambodia",
    "LA": "Laos",
    "BR": "Brazil",
    "MX": "Mexico",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "ZA": "South Africa",
    "NG": "Nigeria",
    "KE": "Kenya",
    "EG": "Egypt",
    "NZ": "New Zealand",
    "HK": "Hong Kong",
}


def country_name_for_code(country_code: str) -> str:
    return _COUNTRY_NAMES.get(str(country_code or "").strip().upper(), "")


def infer_country_from_domain(domain: str) -> tuple[str, str]:
    """Best-effort ISO code + display name from a defaced host / URL."""
    raw = str(domain or "").strip().lower()
    if not raw:
        return "", ""
    if raw.startswith(("http://", "https://")):
        host = (urlparse(raw).hostname or "").lower()
    else:
        host = raw.split("/")[0].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return "", ""

    suffix2 = ".".join(parts[-2:])
    code = _COMPOUND_TLD_CODES.get(suffix2) or _CCTLD_CODES.get(parts[-1], "")
    if not code:
        return "", ""
    return code, country_name_for_code(code)


def infer_country_from_flag_html(html: str) -> tuple[str, str]:
    """Parse Zone-H / Haxor archive flag icons (``flag flag-us`` + title)."""
    fragment = str(html or "")
    match = re.search(r"flag\s+flag-([a-z]{2})\b", fragment, re.I)
    if not match:
        return "", ""
    code = match.group(1).upper()
    label_match = re.search(
        r"(?:title|alt)=['\"]([^'\"]+)['\"]",
        fragment[match.start() : match.start() + 180],
        re.I,
    )
    if not label_match:
        label_match = re.search(
            r"(?:title|alt)=['\"]([^'\"]+)['\"]",
            fragment[max(0, match.start() - 80) : match.start()],
            re.I,
        )
    name = label_match.group(1).strip() if label_match else ""
    if not name:
        name = country_name_for_code(code)
    return code, name


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias).replace(r"\ ", r"[\s-]+")
    # CJK scripts do not use spaces, so Python's \w boundaries would reject
    # country names embedded naturally in compounds such as 日本の自衛隊.
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", alias):
        # Avoid treating インド太平洋 (Indo-Pacific) as India (インド).
        if alias == "インド":
            return re.compile(r"インド(?!太平洋)")
        return re.compile(escaped, re.IGNORECASE)
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


_GEO_PATTERNS = tuple(
    (slug, tuple(_alias_pattern(alias) for alias in aliases))
    for slug, aliases in _GEO_ALIASES
)


# Bare two-letter tokens that are too ambiguous as generic aliases but safe
# when standing alone (e.g. "US Army", "UK MoD"). Avoid matching inside words.
_BARE_COUNTRY_TOKEN_SLUGS: tuple[tuple[str, str], ...] = (
    ("us", "geo-united-states"),
    ("uk", "geo-united-kingdom"),
)
_BARE_COUNTRY_TOKEN_RE = re.compile(
    r"(?<![a-z0-9.])("
    + "|".join(re.escape(token) for token, _ in _BARE_COUNTRY_TOKEN_SLUGS)
    + r")(?![a-z0-9.])",
    re.IGNORECASE,
)

# Japan MoD press often uses 日X / 日XY compounds (日米, 日仏, 日英伊).
_JP_PARTNER_KANJI: dict[str, str] = {
    "米": "geo-united-states",
    "中": "geo-china",
    "韓": "geo-south-korea",
    "朝": "geo-north-korea",
    "台": "geo-taiwan",
    "英": "geo-united-kingdom",
    "仏": "geo-france",
    "伊": "geo-italy",
    "独": "geo-germany",
    "加": "geo-canada",
    "豪": "geo-australia",
    "印": "geo-india",
    "比": "geo-philippines",
    "越": "vietnam",
    "蘭": "geo-netherlands",
    "瑞": "geo-sweden",
    "丁": "geo-denmark",
    "葡": "geo-portugal",
    "土": "geo-turkey",
    "泰": "geo-thailand",
    "露": "geo-russia",
}
_JP_BILATERAL_RE = re.compile(
    r"日([" + "".join(map(re.escape, _JP_PARTNER_KANJI)) + r"]{1,4})"
)

# Vietnamese bilateral short forms after translation: "Nhật - Pháp", "Nhật Bản-Ý".
_VI_PARTNER_ALIASES: tuple[tuple[str, str], ...] = (
    ("pháp", "geo-france"),
    ("ý", "geo-italy"),
    ("italia", "geo-italy"),
    ("anh", "geo-united-kingdom"),
    ("đức", "geo-germany"),
    ("hà lan", "geo-netherlands"),
    ("úc", "geo-australia"),
    ("mỹ", "geo-united-states"),
    ("hoa kỳ", "geo-united-states"),
    ("trung quốc", "geo-china"),
    ("hàn quốc", "geo-south-korea"),
    ("đài loan", "geo-taiwan"),
    ("ấn độ", "geo-india"),
    ("nga", "geo-russia"),
    ("canada", "geo-canada"),
    ("thổ nhĩ kỳ", "geo-turkey"),
    ("türkiye", "geo-turkey"),
    ("thụy điển", "geo-sweden"),
    ("đan mạch", "geo-denmark"),
    ("bồ đào nha", "geo-portugal"),
)
_VI_BILATERAL_RE = re.compile(
    r"(?:nhật\s*bản|nhật)\s*[-–—/]\s*("
    + "|".join(re.escape(alias) for alias, _ in sorted(_VI_PARTNER_ALIASES, key=lambda x: -len(x[0])))
    + r")\b",
    re.IGNORECASE,
)

# Official defense publisher hosts that reliably imply a country of origin are
# handled in ``geography_slugs_from_feed_host`` (mod.go.jp, defense.gov, …).


def geography_slugs_from_feed_host(*urls: str) -> list[str]:
    """Map known official defense hosts to a country slug."""
    found: list[str] = []
    for raw in urls:
        host = (urlparse(str(raw or "").strip()).hostname or "").lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if not host:
            continue
        if host == "mod.go.jp" or host.endswith(".mod.go.jp"):
            if "geo-japan" not in found:
                found.append("geo-japan")
            continue
        if host in {"defense.gov", "www.defense.gov"} or host.endswith(".defense.gov"):
            if "geo-united-states" not in found:
                found.append("geo-united-states")
            continue
        if host.endswith("defence.gov.au"):
            if "geo-australia" not in found:
                found.append("geo-australia")
            continue
    return found


def _detect_jp_bilateral_slugs(text: str) -> list[str]:
    found: list[str] = []
    for match in _JP_BILATERAL_RE.finditer(text):
        if "geo-japan" not in found:
            found.append("geo-japan")
        for char in match.group(1):
            slug = _JP_PARTNER_KANJI.get(char)
            if slug and slug not in found:
                found.append(slug)
    return found


def _detect_vi_bilateral_slugs(text: str) -> list[str]:
    found: list[str] = []
    partner_map = dict(_VI_PARTNER_ALIASES)
    for match in _VI_BILATERAL_RE.finditer(text):
        if "geo-japan" not in found:
            found.append("geo-japan")
        partner = match.group(1).casefold()
        slug = partner_map.get(partner)
        if slug and slug not in found:
            found.append(slug)
    return found


def detect_geography_tag_slugs(
    *parts: str,
    country_code: str = "",
    feed_url: str = "",
    source_url: str = "",
) -> list[str]:
    """Return stable geography slugs found explicitly in content."""
    text = " ".join(str(part or "") for part in parts).strip()
    found: list[str] = []

    code_slug = _COUNTRY_CODE_SLUGS.get(str(country_code or "").strip().upper())
    if code_slug:
        found.append(code_slug)

    for slug in geography_slugs_from_feed_host(feed_url, source_url):
        if slug not in found:
            found.append(slug)

    if text:
        for slug, patterns in _GEO_PATTERNS:
            if slug not in found and any(pattern.search(text) for pattern in patterns):
                found.append(slug)
        token_to_slug = dict(_BARE_COUNTRY_TOKEN_SLUGS)
        for match in _BARE_COUNTRY_TOKEN_RE.finditer(text):
            slug = token_to_slug.get(match.group(1).casefold())
            if slug and slug not in found:
                found.append(slug)
        for slug in _detect_jp_bilateral_slugs(text):
            if slug not in found:
                found.append(slug)
        for slug in _detect_vi_bilateral_slugs(text):
            if slug not in found:
                found.append(slug)
    # "South Africa" contains the continent name but the country is more precise.
    if "geo-south-africa" in found and "geo-africa" in found:
        found.remove("geo-africa")
    return found


def attach_threat_geography_tags(threat) -> list[str]:
    """Detect and attach missing geography tags from title/title_vi/summary/feed."""
    from apps.intel.models import Tag, Threat

    payload = threat.raw_payload if isinstance(threat.raw_payload, dict) else {}
    country_code = ""
    if threat.source == Threat.Source.RANSOMWARE:
        country_code = str(payload.get("country_code") or payload.get("country") or "")
    elif payload.get("discovery") == "zoneh-archive":
        country_code = str(payload.get("country_code") or "")

    slugs = detect_geography_tag_slugs(
        threat.title,
        getattr(threat, "title_vi", "") or "",
        threat.summary,
        payload.get("description") or "",
        payload.get("summary") or "",
        payload.get("content") or "",
        str(payload.get("country") or "") if country_code else "",
        country_code=country_code,
        feed_url=str(payload.get("feed_url") or ""),
        source_url=str(getattr(threat, "source_url", "") or payload.get("link") or ""),
    )
    if not slugs:
        return []

    existing = set(threat.tags.values_list("slug", flat=True))
    missing = [slug for slug in slugs if slug not in existing]
    if not missing:
        return slugs

    Tag.objects.bulk_create(
        [
            Tag(
                slug=slug,
                name=slug.removeprefix("geo-").replace("-", " ").title(),
            )
            for slug in missing
        ],
        ignore_conflicts=True,
    )
    tags = list(Tag.objects.filter(slug__in=missing))
    if tags:
        threat.tags.add(*tags)
    return slugs
