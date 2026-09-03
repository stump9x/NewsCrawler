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

# Canonical IDs from NewsNow's published source directory; aliases occur once.
NEWSNOW = [
    ("baidu", "Baidu", "Tìm kiếm thịnh hành", "hot", "#548fdd"),
    ("bilibili-hot-search", "Bilibili", "Tìm kiếm thịnh hành", "hot", "#45bce4"),
    ("chongbuluo-hot", "Chongbuluo", "Bài viết nổi bật", "hot", "#66b867"),
    ("cls-hot", "Tài Liên Xã", "Tin nổi bật", "hot", "#ef6868"),
    ("coolapk", "Coolapk", "Nổi bật hôm nay", "hot", "#41b586"),
    ("douban", "Douban", "Phim nổi bật", "hot", "#57b966"),
    ("weibo", "Weibo", "Tìm kiếm thời gian thực", "hot", "#e66d6b"),
    ("zhihu", "Zhihu", "Thảo luận nổi bật", "hot", "#508ee5"),
    ("douyin", "Douyin", "Xu hướng video", "hot", "#c083d8"),
    ("hupu", "Hupu", "Bài đăng nổi bật", "hot", "#df6f66"),
    ("v2ex-share", "V2EX", "Chia sẻ mới nhất", "latest", "#8098b0"),
    ("tieba", "Baidu Tieba", "Thảo luận nổi bật", "hot", "#5a99dd"),
    ("toutiao", "Toutiao", "Tin nổi bật", "hot", "#e46b74"),
    ("thepaper", "The Paper", "Bảng tin nổi bật", "hot", "#7a95b3"),
    ("ifeng", "Phượng Hoàng", "Tin nổi bật", "hot", "#dd7467"),
    ("tencent-hot", "Tencent News", "Bản tin tổng hợp", "hot", "#53a4df"),
    ("nowcoder", "Nowcoder", "Bài đăng nổi bật", "hot", "#4facb1"),
    ("sspai", "SSPAI", "Bài viết nổi bật", "hot", "#d86e7a"),
    ("juejin", "Juejin", "Bài viết công nghệ", "hot", "#559cde"),
    ("github-trending-today", "GitHub", "Dự án nổi bật hôm nay", "hot", "#8d9eb4"),
    ("hackernews", "Hacker News", "Bài đăng nổi bật", "hot", "#e79d56"),
    ("producthunt", "Product Hunt", "Sản phẩm nổi bật", "hot", "#eb8467"),
    ("xueqiu-hotstock", "Xueqiu", "Cổ phiếu nổi bật", "hot", "#679ce0"),
    ("steam", "Steam", "Số người đang chơi", "hot", "#6c9dc9"),
    ("freebuf", "Freebuf", "An ninh mạng", "hot", "#69b488"),
    ("qqvideo-tv-hotsearch", "Tencent Video", "Chương trình thịnh hành", "hot", "#55abc7"),
    ("iqiyi-hot-ranklist", "iQIYI", "Chương trình được xem nhiều", "hot", "#7bb854"),
    ("wallstreetcn-hot", "Phố Wall", "Tin được đọc nhiều", "hot", "#6992c7"),
    ("wallstreetcn-quick", "Phố Wall", "Tin nhanh", "latest", "#6992c7"),
    ("wallstreetcn-news", "Phố Wall", "Tin mới nhất", "latest", "#6992c7"),
    ("cls-telegraph", "Tài Liên Xã", "Tin nhanh", "latest", "#ef6868"),
    ("cls-depth", "Tài Liên Xã", "Phân tích chuyên sâu", "latest", "#ef6868"),
    ("zaobao", "Liên Hợp Tảo Báo", "Tin mới nhất", "latest", "#e58585"),
    ("ithome", "IT Home", "Tin công nghệ mới", "latest", "#e0768c"),
    ("solidot", "Solidot", "Tin công nghệ", "latest", "#57afa9"),
    ("dongqiudi", "Dongqiudi", "Tin bóng đá", "latest", "#78b858"),
    ("aihot", "AIHOT", "Tin trí tuệ nhân tạo", "latest", "#709beb"),
    ("sputniknewscn", "Sputnik", "Tin quốc tế", "latest", "#d99665"),
    ("cankaoxiaoxi", "Tin Tham Khảo", "Tin mới nhất", "latest", "#cd8588"),
    ("pcbeta-windows11", "PCBeta", "Windows 11", "latest", "#69a3d4"),
    ("mktnews-flash", "MKTNews", "Tin nhanh thị trường", "latest", "#9291cf"),
    ("gelonghui", "Gelonghui", "Sự kiện mới nhất", "latest", "#749ec7"),
    ("fastbull-express", "FastBull", "Tin nhanh", "latest", "#61b294"),
    ("fastbull-news", "FastBull", "Tin tiêu điểm", "latest", "#61b294"),
    ("jin10", "Jin10", "Dữ liệu và tin nhanh", "latest", "#6d9de8"),
    ("kaopu", "Kaopu News", "Tin quốc tế", "latest", "#8e9aad"),
    ("chongbuluo-latest", "Chongbuluo", "Bài viết mới", "latest", "#66b867"),
]
CHANNELS = {"all": "Tổng hợp", "news": "Tin tức", "finance": "Tài chính", "tech": "Công nghệ", "ent": "Giải trí", "sports": "Thể thao", "video": "Video", "other": "Khác"}
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
    return [{"id": "newsnow:" + source, "provider": "newsnow", "name": config[1], "subtitle": config[2], "accent": config[4], "url": PROVIDER_URLS["newsnow"], "items": dedupe_items(items)}]


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
    if provider == "newsnow":
        return parse_newsnow(fetch_url(PROVIDER_URLS[provider] + "/api/s", params={"id": source}).json(), source)
    if provider == "rebang":
        return parse_rebang(fetch_url(PROVIDER_URLS[provider] + "/channel/" + source).text)
    return parse_sopilot(fetch_url(PROVIDER_URLS[provider]).text)
