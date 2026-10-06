"""Normalize the curated NewsCrawler RSS catalog and add validated feeds."""

from __future__ import annotations

import json
from pathlib import Path

out = Path(__file__).with_name("rss_sources.json")
items: list[dict] = json.loads(out.read_text(encoding="utf-8"))
# repair-vlc-non-rss-endpoints
replaced_urls = {"https://www.freshnews.com.kh/feed/", "https://laodong.vn/rss/home.rss", "https://vietnamnews.vn/rss"}
items = [item for item in items if item.get("url") not in replaced_urls]
seen: set[str] = {str(item.get("url", "")).strip().lower() for item in items}


def add(
    url: str,
    name: str,
    country: str = "",
    code: str = "",
    confidence: int = 2,
    category: str = "news",
    notes: str = "transport=official-rss | validated=2026-07-21",
):
    u = (url or "").strip()
    if not u or u.lower() in seen:
        return
    seen.add(u.lower())
    items.append(
        {
            "name": name[:64],
            "url": u,
            "country": country or "",
            "country_code": code or "",
            "confidence": int(confidence),
            "category": category,
            "notes": notes,
            "requires_tor": False,
        }
    )


extras = [
    ("https://www.twz.com/feed", "twz", "United States", "US", 1, "news"),
    (
        "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
        "defense-news",
        "United States",
        "US",
        1,
        "news",
    ),
    ("https://breakingdefense.com/feed/", "breaking-defense", "United States", "US", 1, "news"),
    ("https://www.navalnews.com/feed/", "naval-news", "France", "FR", 1, "news"),
    ("https://www.rusi.org/rss/whats-new.xml", "rusi-whats-new", "United Kingdom", "GB", 1, "news"),
    (
        "https://www.whitehouse.gov/briefings-statements/feed/",
        "white-house-briefings",
        "United States",
        "US",
        1,
        "news",
    ),
    ("https://www.army.mil/rss/static/1.xml", "us-army-news", "United States", "US", 1, "news"),
    (
        "https://www.af.mil/DesktopModules/ArticleCS/RSS.ashx"
        "?ContentType=1&Site=1&isdashboardselected=0&max=20",
        "us-air-force-news",
        "United States",
        "US",
        1,
        "news",
    ),
    (
        "https://www.usff.navy.mil/DesktopModules/ArticleCS/RSS.ashx"
        "?ContentType=2&Site=1148&isdashboardselected=0&max=50",
        "us-navy-fleet-forces",
        "United States",
        "US",
        1,
        "news",
    ),
    (
        "https://www.fdd.org/feed/",
        "fdd",
        "United States",
        "US",
        1,
        "news",
    ),
    (
        "https://news.google.com/rss/search?q=di%E1%BB%85n+t%E1%BA%ADp+chung+ph%C3%B2ng+th%E1%BB%A7+d%C3%A2n+s%E1%BB%B1+Vi%E1%BB%87t+Nam+L%C3%A0o+Campuchia+2026+when%3A90d&hl=vi&gl=VN&ceid=VN:vi",
        "google-news-vlc-civil-defense-vi",
        "Vietnam",
        "VN",
        2,
        "news",
        "transport=google-news-search-rss | targeted=Vietnam-Laos-Cambodia civil defense exercise | language=vi | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=qu%C3%A2n+%C4%91%E1%BB%99i+ba+n%C6%B0%E1%BB%9Bc+Vi%E1%BB%87t+Nam+L%C3%A0o+Campuchia+c%E1%BB%A9u+h%E1%BB%99+c%E1%BB%A9u+n%E1%BA%A1n+%E1%BB%A9ng+ph%C3%B3+th%E1%BA%A3m+h%E1%BB%8Da+when%3A90d&hl=vi&gl=VN&ceid=VN:vi",
        "google-news-vlc-rescue-vi",
        "Vietnam",
        "VN",
        2,
        "news",
        "transport=google-news-search-rss | targeted=tri-country disaster response and rescue | language=vi | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=Vietnam+Laos+Cambodia+2026+joint+civil+defense+exercise+when:90d&hl=en&gl=US&ceid=US:en",
        "google-news-vlc-civil-defense-en",
        "Vietnam",
        "VN",
        2,
        "news",
        "transport=google-news-search-rss | targeted=Vietnam-Laos-Cambodia civil defense exercise | language=en | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=Vietnam+Laos+Cambodia+disaster+response+HADR+search+rescue+military+exercise+2026+when:90d&hl=en&gl=US&ceid=US:en",
        "google-news-vlc-hadr-en",
        "Vietnam",
        "VN",
        2,
        "news",
        "transport=google-news-search-rss | targeted=tri-country HADR and search-rescue exercise | language=en | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=Nguy%E1%BB%85n+Tr%C6%B0%E1%BB%9Dng+Th%E1%BA%AFng+ph%C3%B2ng+th%E1%BB%A7+d%C3%A2n+s%E1%BB%B1+L%C3%A0o+Campuchia+2026+when%3A365d&hl=vi&gl=VN&ceid=VN:vi",
        "google-news-vlc-nguyen-truong-thang-vi", "Vietnam", "VN", 2, "news",
        "transport=google-news-search-rss | targeted=Nguyen Truong Thang civil defense | language=vi | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=L%E1%BB%AF+%C4%91o%C3%A0n+249+L%C3%A0o+Campuchia+di%E1%BB%85n+t%E1%BA%ADp+c%E1%BB%A9u+h%E1%BB%99+2026+when%3A365d&hl=vi&gl=VN&ceid=VN:vi",
        "google-news-vlc-brigade-249-vi", "Vietnam", "VN", 2, "news",
        "transport=google-news-search-rss | targeted=Brigade 249 exercise | language=vi | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=Nguyen+Truong+Thang+exercise+Laos+Cambodia+2026+when%3A365d&hl=en&gl=US&ceid=US:en",
        "google-news-vlc-nguyen-truong-thang-en", "Vietnam", "VN", 2, "news",
        "transport=google-news-search-rss | targeted=Nguyen Truong Thang exercise | language=en | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=Engineering+Brigade+249+Laos+Cambodia+2026+when%3A365d&hl=en&gl=US&ceid=US:en",
        "google-news-vlc-brigade-249-en", "Vietnam", "VN", 2, "news",
        "transport=google-news-search-rss | targeted=Engineering Brigade 249 | language=en | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=%E8%B6%8A%E8%80%81%E6%9F%AC+%E8%81%94%E5%90%88%E6%BC%94%E4%B9%A0+%E6%95%91%E6%8F%B4%E6%BC%94%E4%B9%A0+2026+when%3A365d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "google-news-vlc-exercise-zh", "Vietnam", "VN", 2, "news",
        "transport=google-news-search-rss | targeted=trilateral joint rescue exercise | language=zh | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=%E1%9E%9F%E1%9F%85+%E1%9E%9F%E1%9E%BB%E1%9E%81%E1%9E%B6+%E1%9E%9F%E1%9E%98%E1%9E%99%E1%9E%BB%E1%9E%91%E1%9F%92%E1%9E%92+%E1%9E%9C%E1%9F%80%E1%9E%8F%E1%9E%8E%E1%9E%B6%E1%9E%98+2026+when%3A365d&hl=km&gl=KH&ceid=KH:km",
        "google-news-vlc-exercise-km", "Cambodia", "KH", 2, "news",
        "transport=google-news-search-rss | targeted=Sao Sokha exercise | language=km | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=%E0%BB%80%E0%BA%9D%E0%BA%B4%E0%BA%81%E0%BA%8A%E0%BB%89%E0%BA%AD%E0%BA%A1+%E0%BA%AB%E0%BA%A7%E0%BA%BD%E0%BA%94%E0%BA%99%E0%BA%B2%E0%BA%A1+%E0%BA%81%E0%BA%B3%E0%BA%9B%E0%BA%B9%E0%BB%80%E0%BA%88%E0%BA%8D+2026+when%3A365d&hl=lo&gl=LA&ceid=LA:lo",
        "google-news-vlc-exercise-lo", "Laos", "LA", 2, "news",
        "transport=google-news-search-rss | targeted=trilateral rescue exercise | language=lo | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=exercice+conjoint+protection+civile+Vietnam+Laos+Cambodge+2026+when%3A365d&hl=fr&gl=FR&ceid=FR:fr",
        "google-news-vlc-exercise-fr", "Vietnam", "VN", 2, "news",
        "transport=google-news-search-rss | targeted=trilateral civil protection exercise | language=fr | validated=2026-10-05",
    ),
    (
        "https://news.google.com/rss/search?q=%E0%B8%81%E0%B8%B2%E0%B8%A3%E0%B8%9D%E0%B8%B6%E0%B8%81%E0%B8%A3%E0%B9%88%E0%B8%A7%E0%B8%A1+%E0%B8%9B%E0%B9%89%E0%B8%AD%E0%B8%87%E0%B8%81%E0%B8%B1%E0%B8%99%E0%B8%9E%E0%B8%A5%E0%B9%80%E0%B8%A3%E0%B8%B7%E0%B8%AD%E0%B8%99+%E0%B9%80%E0%B8%A7%E0%B8%B5%E0%B8%A2%E0%B8%94%E0%B8%99%E0%B8%B2%E0%B8%A1+%E0%B8%A5%E0%B8%B2%E0%B8%A7+%E0%B8%81%E0%B8%B1%E0%B8%A1%E0%B8%9E%E0%B8%B9%E0%B8%8A%E0%B8%B2+2026+when%3A365d&hl=th&gl=TH&ceid=TH:th",
        "google-news-vlc-exercise-th", "Thailand", "TH", 2, "news",
        "transport=google-news-search-rss | targeted=trilateral civil defense exercise | language=th | validated=2026-10-05",
    ),
]

# user-requested-vlc-2026-domain-rss
extras += [
    ("https://nhandan.vn/rss/home.rss", "nhandan-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://en.nhandan.vn/rss/home.rss", "nhandan-en", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://vietnamplus.vn/rss/home.rss", "vietnamplus-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://en.vietnamplus.vn/rss/home.rss", "vietnamplus-en", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://zh.vietnamplus.vn/rss/home.rss", "vietnamplus-zh", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://vtv.vn/rss/home.rss", "vtv-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://thanhnien.vn/rss/thoi-su/quoc-phong.rss", "thanhnien-defense", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://vnexpress.net/rss/tin-moi-nhat.rss", "vnexpress-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://tuoitre.vn/rss/tin-moi-nhat.rss", "tuoitre-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://dantri.com.vn/rss/home.rss", "dantri-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://tienphong.vn/rss/home.rss", "tienphong-vi", "Vietnam", "VN", 2, "news", "transport=official-rss | topic=VLC-2026"),
    ("https://laopost.com/feed/", "laopost-lo", "Laos", "LA", 2, "news", "transport=official-rss | topic=VLC-2026"),
]
from urllib.parse import quote
domain_feeds = [
    ("qdnd-vi","qdnd.vn","VN"), ("qdnd-en","en.qdnd.vn","VN"),
    ("mod-vn","mod.gov.vn","VN"), ("tapchiqptd","tapchiqptd.vn","VN"),
    ("baomoi","baomoi.com","VN"), ("bodu365","bodu365.co","KH"),
    ("pasaxon","pasaxon.org.la","LA"), ("vnanet","vnanet.vn","VN"),
    ("vov","vov.vn","VN"), ("vietnamnet","vietnamnet.vn","VN"),
    ("bao-hai-quan","baohaiquanvietnam.vn","VN"), ("bien-phong","bienphong.com.vn","VN"),
    ("qpvn","qpvn.vn","VN"), ("kpl","kpl.gov.la","LA"),
    ("lao-gov","lao.gov.la","LA"), ("vientiane-times","vientianetimes.org.la","LA"),
    ("akp","akp.gov.kh","KH"), ("kampuchea-thmey","kampucheathmey.com","KH"),
    ("rasmei","rasmeinews.com","KH"), ("phnompenhpost","phnompenhpost.com","KH"),
    ("nhandan-zh","cn.nhandan.vn","CN"), ("afp","afp.com","FR"),
    ("freshnews","freshnews.com.kh","KH"), ("laodong","laodong.vn","VN"), ("vietnamnews","vietnamnews.vn","VN"),
    ("nation-th","nationthailand.com","TH"),
    ("youtube","youtube.com","VN"), ("facebook","facebook.com","VN"),
    ("tiktok","tiktok.com","VN"),
]
for key, domain, region in domain_feeds:
    if region == "CN":
        query='site:'+domain+' 2026 越南 老挝 柬埔寨 (演习 OR 救援 OR 民防)'
        hl,gl,ceid="zh-CN","CN","CN:zh-Hans"
    elif region in ("LA","KH"):
        query='site:'+domain+' 2026 Vietnam Laos Cambodia (exercise OR rescue OR "disaster response" OR HADR)'
        hl,gl,ceid="en",region,region+":en"
    else:
        query='site:'+domain+' 2026 ("Việt Nam" OR Vietnam) (Lào OR Laos) (Campuchia OR Cambodia) (diễn tập OR exercise OR cứu hộ OR rescue OR "civil defense" OR "phòng thủ dân sự" OR HADR)'
        hl,gl,ceid=("vi","VN","VN:vi") if region=="VN" else ("en",region,region+":en")
    url="https://news.google.com/rss/search?q="+quote(query,safe="")+f"&hl={hl}&gl={gl}&ceid={ceid}"
    add(url,"google-vlc-"+key,{"VN":"Vietnam","KH":"Cambodia","LA":"Laos","CN":"China","FR":"France","TH":"Thailand"}[region],region,2,"news",
        "transport=google-news-search-rss | site="+domain+" | targeted=VLC-2026")

for row in extras:
    add(*row)

out.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {len(items)} sources -> {out}")
from collections import Counter

print("by_confidence", dict(sorted(Counter(i["confidence"] for i in items).items())))
