from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from django.core.management.base import BaseCommand

from apps.core.security import validate_fetch_http_url
from apps.intel.models import FeedSource
from apps.workers.feeds.clients import _is_same_publisher_url, _looks_like_rss_or_atom


class AlternateFeedParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link":
            return
        data = {str(k).lower(): str(v or "") for k, v in attrs}
        rel = data.get("rel", "").lower()
        mime = data.get("type", "").lower()
        if "alternate" in rel and any(x in mime for x in ("rss", "atom")):
            if data.get("href"):
                self.urls.append(data["href"])


def homepage_from_notes(source: FeedSource) -> str:
    match = re.search(r"(?:^|\|\s*)homepage=([^|\s]+)", source.notes or "")
    raw = (match.group(1).strip() if match else source.url)
    if raw.endswith((".html", ".htm", ".aspx")):
        return raw
    return raw.rstrip("/") + "/"


def is_feed_document(body: str) -> bool:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return False
    local_name = root.tag.rsplit("}", 1)[-1].casefold()
    return local_name in {"rss", "feed", "rdf"}


def safe_get(client: httpx.Client, url: str) -> httpx.Response:
    current = validate_fetch_http_url(url, allow_http=True)
    for _ in range(6):
        response = client.get(current)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                response.raise_for_status()
            current = validate_fetch_http_url(
                urljoin(str(response.url), location), allow_http=True
            )
            continue
        response.raise_for_status()
        return response
    raise httpx.TooManyRedirects("official source exceeded 5 redirects")


def fetch_text(client: httpx.Client, url: str) -> str:
    response = safe_get(client, url)
    if len(response.content) > 2_000_000:
        raise ValueError("response_too_large")
    return response.text


def discover_one(source: FeedSource) -> dict:
    homepage = homepage_from_notes(source)
    result = {"id": source.id, "name": source.name, "homepage": homepage, "feed": ""}
    headers = {
        "User-Agent": "NewsCrawler/1.0 (+first-party RSS discovery)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.8",
    }
    with httpx.Client(timeout=10.0, follow_redirects=False, headers=headers) as client:
        html = ""
        final_homepage = homepage
        try:
            response = safe_get(client, homepage)
            if len(response.content) <= 2_000_000:
                html = response.text
                if _is_same_publisher_url(str(response.url), homepage):
                    final_homepage = str(response.url)
        except Exception as exc:
            result["homepage_error"] = str(exc)[:160]

        candidates: list[str] = []
        if html:
            parser = AlternateFeedParser()
            parser.feed(html)
            candidates.extend(urljoin(final_homepage, item) for item in parser.urls)

        parsed = urlparse(final_homepage)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        candidates.extend(
            origin + suffix
            for suffix in ("/feed/", "/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml")
        )
        seen = set()
        for candidate in candidates:
            candidate = candidate.split("#", 1)[0]
            if candidate in seen or not _is_same_publisher_url(candidate, homepage):
                continue
            seen.add(candidate)
            try:
                body = fetch_text(client, candidate)
                if is_feed_document(body):
                    result["feed"] = candidate
                    break
            except Exception:
                continue
    return result


class Command(BaseCommand):
    help = (
        "Discover first-party RSS/Atom endpoints for active sources. "
        "Homepage HTML is never kept as a feed transport."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=8)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        sources = list(
            FeedSource.objects.filter(is_active=True)
            .exclude(name__startswith="topic-")
            .order_by("id")
        )
        results = []
        with ThreadPoolExecutor(max_workers=max(1, min(16, options["workers"]))) as pool:
            futures = {pool.submit(discover_one, source): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "id": source.id, "name": source.name,
                        "homepage": homepage_from_notes(source), "feed": "",
                        "error": str(exc)[:160],
                    }
                results.append(result)
                mode = "RSS" if result.get("feed") else "SKIP"
                self.stdout.write(
                    f"{mode:4} {source.name}: {result.get('feed') or result.get('homepage')}"
                )

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run only; pass --apply to update sources."))
            return

        FeedSource.objects.filter(name__startswith="topic-").delete()
        applied = []
        official_rss = 0
        deactivated = 0
        for result in results:
            source = FeedSource.objects.filter(pk=result["id"]).first()
            if source is None:
                continue
            homepage = result["homepage"]
            feed_url = str(result.get("feed") or "").strip()
            if not feed_url:
                source.is_active = False
                source.last_status = "disabled"
                source.last_error = "no_first_party_rss_found"
                source.notes = (
                    f"homepage={homepage} | transport=none | "
                    "DISABLED: no first-party RSS/Atom endpoint found"
                )
                source.save(
                    update_fields=[
                        "is_active",
                        "last_status",
                        "last_error",
                        "notes",
                        "updated_at",
                    ]
                )
                deactivated += 1
                continue

            conflict = FeedSource.objects.filter(url=feed_url).exclude(pk=source.pk).first()
            if conflict:
                conflict.delete()
            source.url = feed_url
            source.notes = (
                f"homepage={homepage} | transport=official-rss | "
                "Indo-Pacific military-defense OSINT"
            )
            source.is_active = True
            source.last_status = ""
            source.last_error = ""
            source.last_body_sha256 = ""
            source.http_etag = ""
            source.http_last_modified = ""
            source.processing_version = 0
            source.consecutive_failures = 0
            source.save()
            official_rss += 1
            applied.append({
                "name": source.name,
                "url": source.url,
                "country": source.country,
                "country_code": source.country_code,
                "confidence": source.confidence,
                "category": source.category,
                "notes": source.notes,
                "requires_tor": source.requires_tor,
            })

        # Remove superseded aggregator rows and make restart seeding deterministic.
        FeedSource.objects.filter(url__icontains="news.google.com").delete()
        seed = Path(__file__).resolve().parents[3] / "workers" / "feeds" / "rss_sources.json"
        seed.write_text(json.dumps(applied, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Applied first-party sources: rss={official_rss} "
            f"deactivated_no_rss={deactivated} total={len(applied)}"
        ))
