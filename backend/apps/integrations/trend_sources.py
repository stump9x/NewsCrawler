"""Read public ranking feeds. No research jobs, topic filters or news ingest."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache.backends.filebased import FileBasedCache

# One curated overview; source order follows the user's priorities.
# The user's NewsNow "Tik Tok" screenshot resolves to source ID douyin.
# Keep both requested display slots, sharing that feed and translation cache.
NEWSNOW = [
    ("baidu", "Baidu", "Tìm kiếm thịnh hành", "#548fdd"),
    ("tiktok", "TikTok", "Bảng Douyin trên NewsNow", "#52bac4"),
    ("weibo", "Weibo", "Tìm kiếm thịnh hành", "#e66d6b"),
    ("douyin", "Douyin", "Xu hướng video", "#c083d8"),
    ("tencent-hot", "Tencent News", "Tin tức tổng hợp", "#53a4df"),
    ("sputniknewscn", "Sputnik", "Tin quốc tế", "#d99665"),
    ("nowcoder", "Nowcoder", "Bài đăng nổi bật", "#4facb1"),
    ("hackernews", "Hacker News", "Bài đăng nổi bật", "#e79d56"),
    ("github-trending-today", "GitHub", "Dự án nổi bật hôm nay", "#8d9eb4"),
    ("aihot", "AIHOT", "Tin trí tuệ nhân tạo", "#709beb"),
    ("zhihu", "Zhihu", "Thảo luận nổi bật", "#508ee5"),
    ("bing", "Bing", "Tin tức tổng hợp", "#42b5a4"),
    ("ifeng", "Phoenix", "Tin Phượng Hoàng", "#dd7467"),
    ("freebuf", "Freebuf", "An ninh mạng", "#69b488"),
]
SOURCE_ALIASES = {"tiktok": "douyin"}
CHANNELS = {"all": "Tin tức tổng hợp"}
PLATFORM_NAMES = {"抖音": "Douyin", "微博": "Weibo", "百度": "Baidu", "快手": "Kuaishou", "知乎": "Zhihu", "腾讯网": "Tencent News", "哔哩哔哩": "Bilibili", "豆瓣": "Douban", "百度贴吧": "Baidu Tieba", "必应": "Bing", "梨视频": "Pear Video", "知乎日报": "Nhật báo Zhihu", "简书": "Jianshu"}
PROVIDER_URLS = {"newsnow": "https://newsnow.busiyi.world", "sopilot": "https://sopilot.net/zh/hot-tweets", "rebang": "https://top.open2hub.com"}


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


def parse_sopilot(html):
    """Decode public Next.js serialized data, never execute downloaded JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    stream = ""
    for tag in soup.select("script"):
        text = (tag.string or "").strip().rstrip(";")
        if not text.startswith("self.__next_f.push("):
            continue
        try:
            packet = json.loads(text[len("self.__next_f.push("):-1])
            if len(packet) > 1 and isinstance(packet[1], str):
                stream += packet[1]
        except (ValueError, TypeError):
            continue
    match = re.search(r'"initialTweets"\s*:', stream)
    if not match:
        raise ValueError("SoPilot chưa trả về bài đăng.")
    rows, _ = json.JSONDecoder().raw_decode(stream[match.end():].lstrip())
    # Long posts are separate React Flight text frames. Frame lengths are UTF-8
    # bytes, not Python characters; resolving them avoids returning "$13" or
    # silently dropping the tail of a Chinese post.
    encoded = stream.encode("utf-8")
    references, consumed = {}, 0
    for frame in re.finditer(rb"([0-9a-f]+):T([0-9a-f]+),", encoded):
        if frame.start() < consumed:
            continue
        end = frame.end() + int(frame.group(2), 16)
        if end <= len(encoded):
            references["$" + frame.group(1).decode()] = encoded[frame.end():end].decode("utf-8")
            consumed = end
    items = []
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not row.get("text"):
            continue
        handle = str(row.get("screen_name") or "")
        tweet_id = str(row.get("tweet_id") or "")
        url = f"https://x.com/{handle}/status/{tweet_id}" if re.fullmatch(r"\w+", handle) and tweet_id.isdigit() else PROVIDER_URLS["sopilot"]
        text = references.get(str(row["text"]), str(row["text"]))
        if re.fullmatch(r"\$[0-9a-f]+", text):
            raise ValueError("Chưa đọc đủ nội dung bài đăng SoPilot.")
        items.append({"rank": rank, "title": text.removeprefix("$") if text.startswith("$$") else text, "url": url, "author": str(row.get("nickname") or handle), "handle": handle, "avatar": safe_url(row.get("avatar")), "published_at": str(row.get("created_at") or "").removeprefix("$D"), "category": str(row.get("type") or ""), "metrics": {"likes": row.get("favorites"), "reposts": row.get("retweets"), "comments": row.get("replies"), "views": row.get("views"), "followers": row.get("followers_count"), "probability": row.get("viral_score"), "predicted_views": row.get("predicted_views")}})
    return [{"id": "sopilot:hot", "provider": "sopilot", "name": "SoPilot · X", "subtitle": "Bài đăng đang lan truyền", "accent": "#7260ed", "url": PROVIDER_URLS["sopilot"], "items": dedupe_items(items)}]


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
    return parse_sopilot(fetch_url(PROVIDER_URLS[provider]).text)
