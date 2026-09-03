"""Read public ranking feeds. No research jobs, topic filters or news ingest."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache.backends.filebased import FileBasedCache

# One curated overview; source order follows the user's priorities.
# The user's NewsNow "Tik Tok" screenshot resolves to source ID douyin.
# Display that feed once as TikTok, preserving its upstream/cache identity.
NEWSNOW = [
    ("baidu", "Baidu", "Tìm kiếm thịnh hành", "#548fdd"),
    ("tiktok", "TikTok", "Xu hướng video", "#52bac4"),
    ("sputniknewscn", "Sputnik", "Tin quốc tế", "#d99665"),
    ("tencent-hot", "Tencent News", "Tin tức tổng hợp", "#53a4df"),
    ("weibo", "Weibo", "Tìm kiếm thịnh hành", "#e66d6b"),
    ("hackernews", "Hacker News", "Bài đăng nổi bật", "#e79d56"),
    ("github-trending-today", "GitHub", "Dự án nổi bật hôm nay", "#8d9eb4"),
    ("bing", "Bing", "Tin tức tổng hợp", "#42b5a4"),
    ("ifeng", "Phoenix", "Tin Phượng Hoàng", "#dd7467"),
    ("freebuf", "Freebuf", "An ninh mạng", "#69b488"),
]
SOURCE_ALIASES = {"tiktok": "douyin"}
CHANNELS = {"all": "Tin tức tổng hợp"}
PLATFORM_NAMES = {"抖音": "Douyin", "微博": "Weibo", "百度": "Baidu", "快手": "Kuaishou", "知乎": "Zhihu", "腾讯网": "Tencent News", "哔哩哔哩": "Bilibili", "豆瓣": "Douban", "百度贴吧": "Baidu Tieba", "必应": "Bing", "梨视频": "Pear Video", "知乎日报": "Nhật báo Zhihu", "简书": "Jianshu"}
PROVIDER_URLS = {"newsnow": "https://newsnow.busiyi.world", "rebang": "https://top.open2hub.com"}


def trend_cache():
    path = getattr(settings, "TREND_CACHE_DIR", Path(__file__).resolve().parents[2] / ".trend-cache")
    return FileBasedCache(str(path), {"OPTIONS": {"MAX_ENTRIES": 20000}, "TIMEOUT": 86400 * 7})


def safe_url(value, base=""):
    url = urljoin(base, str(value or ""))
    parts = urlsplit(url)
    return url if parts.scheme in {"http", "https"} and parts.hostname else ""


def dedupe_items(rows):
    """Remove repeated entries within one board; preserve the source's ranking."""
    seen, out = set(), []
    for row in rows:
        key = row.get("url") or row["title"]
        if key in seen:
            continue
        seen.add(key)
        row["id"] = hashlib.sha256((row["title"] + "\n" + key).encode()).hexdigest()[:24]
        out.append(row)
    return out


def fetch_url(url, *, params=None):
    with httpx.Client(timeout=httpx.Timeout(12, connect=8), follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; NewsCrawler/1.0; public-trends)", "Accept": "application/json,text/html,application/rss+xml"}) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response


def parse_newsnow(payload, source):
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Nguồn chưa trả về bảng xếp hạng.")
    config = next(row for row in NEWSNOW if row[0] == source)
    items = []
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not row.get("title"):
            continue
        items.append({"rank": rank, "title": str(row["title"]), "url": safe_url(row.get("url") or row.get("mobileUrl"), PROVIDER_URLS["newsnow"]), "metrics": {}})
    return [{"id": "newsnow:" + source, "provider": "newsnow", "name": config[1], "subtitle": config[2], "accent": config[3], "url": PROVIDER_URLS["newsnow"], "items": dedupe_items(items)}]


def parse_rebang(html):
    soup = BeautifulSoup(html, "html.parser")
    boards, seen = [], set()
    for card in soup.select(".card-rebang"):
        heading = card.select_one(".platform-title")
        if not heading:
            continue
        name = heading.get_text(" ", strip=True)
        subtitle = card.select_one(".platform-time")
        subtitle = subtitle.get_text(" ", strip=True).split(" - ")[-1] if subtitle else "Bảng xếp hạng"
        signature = name + "|" + subtitle
        if signature in seen:
            continue
        seen.add(signature)
        items = []
        for rank, link in enumerate(card.select("a.list-item-link"), 1):
            title = link.select_one(".list-text")
            if title:
                number = link.select_one(".list-number")
                items.append({"rank": int(number.get_text()) if number and number.get_text().isdigit() else rank, "title": title.get_text(" ", strip=True), "url": safe_url(link.get("href"), PROVIDER_URLS["rebang"]), "metrics": {}})
        icon = card.select_one("img[src]")
        boards.append({"id": "rebang:" + hashlib.sha256(signature.encode()).hexdigest()[:16], "provider": "rebang", "name": PLATFORM_NAMES.get(name, name), "subtitle": subtitle, "accent": "#dc6567", "url": PROVIDER_URLS["rebang"], "icon": safe_url(icon.get("src"), PROVIDER_URLS["rebang"]) if icon else "", "items": dedupe_items(items)})
    if not boards:
        raise ValueError("REBANG chưa trả về bảng xếp hạng.")
    return boards


def collect_boards(provider, source):
    if provider == "newsnow" and source == "bing":
        boards = parse_rebang(fetch_url(PROVIDER_URLS["rebang"] + "/channel/all").text)
        board = next((row for row in boards if row["name"] == "Bing"), None)
        if not board:
            raise ValueError("REBANG chưa trả về bảng Bing.")
        config = next(row for row in NEWSNOW if row[0] == "bing")
        # Stable overview ID, with actual source retained for attribution.
        return [{**board, "id": "newsnow:bing", "subtitle": config[2], "accent": config[3]}]
    if provider == "newsnow":
        return parse_newsnow(fetch_url(PROVIDER_URLS[provider] + "/api/s", params={"id": SOURCE_ALIASES.get(source, source)}).json(), source)
    if provider == "rebang":
        return parse_rebang(fetch_url(PROVIDER_URLS[provider] + "/channel/" + source).text)
    raise ValueError("Nguồn xu hướng không hợp lệ.")
