"""Normalize the curated NewsCrawler RSS catalog and add validated feeds."""

from __future__ import annotations

import json
from pathlib import Path

out = Path(__file__).with_name("rss_sources.json")
items: list[dict] = json.loads(out.read_text(encoding="utf-8"))
seen: set[str] = {str(item.get("url", "")).strip().lower() for item in items}


def add(url: str, name: str, country: str = "", code: str = "", confidence: int = 2, category: str = "news"):
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
            "notes": "transport=official-rss | validated=2026-07-21",
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
]
for row in extras:
    add(*row)

out.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {len(items)} sources -> {out}")
from collections import Counter

print("by_confidence", dict(sorted(Counter(i["confidence"] for i in items).items())))
