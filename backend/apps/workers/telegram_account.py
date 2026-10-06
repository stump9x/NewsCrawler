"""Read linked news from one authenticated Telegram group, with durable cursors."""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone as utc_timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from apps.core.crypto import decrypt_secret
from apps.core.security import UnsafeURLError, validate_public_http_url
from apps.intel.models import Threat
from apps.intel.wire_urls import find_threat_by_normalized_url, normalize_wire_url
from apps.workers.feed_dates import parse_feed_datetime

INVITE_HASH = "Aoy1s68rR-cxMWFi"
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.I)


def account_directory() -> Path:
    return Path(settings.BASE_DIR) / ".telegram"


def write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.chmod(0o600)


def source_urls(message) -> list[str]:
    from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

    candidates = []
    for entity, text in message.get_entities_text():
        if isinstance(entity, MessageEntityTextUrl):
            candidates.append(entity.url)
        elif isinstance(entity, MessageEntityUrl):
            candidates.append(text)
    candidates.extend(URL_PATTERN.findall(message.raw_text or ""))
    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if getattr(webpage, "url", None):
        candidates.append(webpage.url)
    found = []
    for candidate in candidates:
        candidate = candidate.rstrip(".,;!?")
        while candidate.endswith(")") and candidate.count(")") > candidate.count("("):
            candidate = candidate[:-1]
        try:
            parsed = urlparse(candidate)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
            if parsed.hostname.lower() in {"t.me", "telegram.me"}:
                if not re.fullmatch(r"/[A-Za-z0-9_]+/\d+", parsed.path):
                    continue
                if parsed.path.startswith("/c/"):
                    continue
            url = normalize_wire_url(candidate)
        except ValueError:
            continue
        if url not in found:
            found.append(url)
    return found


def _telegram_caption_parts(
    caption: str, fallback_title: str, fallback_summary: str
) -> tuple[str, str]:
    """Use the group's prepared headline and summary without retranslating."""
    lines = str(caption or "").splitlines()
    title_index = None
    title = ""
    for index, line in enumerate(lines):
        candidate = re.sub(r"^\W+", "", line, flags=re.UNICODE).strip()
        if not candidate or URL_PATTERN.fullmatch(candidate):
            continue
        if re.fullmatch(r"(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})", candidate):
            continue
        if len(candidate) >= 8:
            title, title_index = candidate[:512], index
            break
    if not title:
        return str(fallback_title or "").strip()[:512], str(fallback_summary or "")[:5000]

    summary_lines = []
    for index, line in enumerate(lines):
        if index == title_index:
            continue
        candidate = re.sub(r"^\W+", "", line, flags=re.UNICODE).strip()
        if not candidate or URL_PATTERN.fullmatch(candidate):
            continue
        if re.fullmatch(r"(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})", candidate):
            continue
        summary_lines.append(candidate)
    summary = "\n".join(summary_lines).strip()
    return title, (summary or str(fallback_summary or ""))[:5000]


async def _read_messages(config: dict, cursor: int, limit: int) -> list[dict]:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import InputPeerChannel, InputPeerChat

    session = decrypt_secret(config["session"])
    api_hash = decrypt_secret(config["api_hash"])
    if not session or not api_hash:
        raise RuntimeError("Telegram account configuration could not be decrypted")
    client = TelegramClient(
        StringSession(session), int(config["api_id"]), api_hash,
        receive_updates=False, request_retries=2, connection_retries=2,
        flood_sleep_threshold=0,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram account requires interactive login again")
        group = (
            InputPeerChannel(int(config["group_id"]), int(config["access_hash"]))
            if config["group_kind"] == "channel"
            else InputPeerChat(int(config["group_id"]))
        )
        options = {"min_id": cursor, "reverse": True, "limit": limit}
        if not cursor:
            options["offset_date"] = datetime.now(utc_timezone.utc) - timedelta(days=30)
        result = []
        async for message in client.iter_messages(group, **options):
            if not message.date:
                continue
            result.append({
                "id": message.id,
                "posted_at": message.date.isoformat(),
                "caption": (message.raw_text or "")[:5000],
                "urls": source_urls(message),
                "message_url": (
                    f"https://t.me/c/{config['group_id']}/{message.id}"
                    if config["group_kind"] == "channel" else ""
                ),
            })
        return result
    finally:
        await client.disconnect()


def _read_article(url: str) -> dict:
    from apps.integrations.web_reader.article_text import extract_article_text

    current = url
    with httpx.Client(timeout=8, follow_redirects=False) as client:
        for _ in range(4):
            validate_public_http_url(current)
            with client.stream("GET", current, headers={"User-Agent": "NewsCrawler/Telegram-Reader"}) as response:
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                if not any(value in response.headers.get("content-type", "").lower() for value in ("html", "text/")):
                    raise ValueError("Source is not a readable article page")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 512_000:
                        break
                raw = bytes(body[:512_000]).decode(response.encoding or "utf-8", errors="replace")
            soup = BeautifulSoup(raw, "html.parser")
            title_node = soup.find("meta", attrs={"property": "og:title"})
            title = str(title_node.get("content") or "") if title_node else ""
            if not title:
                node = soup.find("h1") or soup.find("title")
                title = node.get_text(" ", strip=True) if node else ""
            extracted = extract_article_text(raw, title_hint=title, max_chars=8000)
            published = None
            for key in ("article:published_time", "datePublished", "pubdate", "publishdate", "date"):
                node = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
                if node:
                    published = parse_feed_datetime(node.get("content"))
                    if published:
                        break
            if not published:
                node = soup.find("time", attrs={"datetime": True})
                if node:
                    published = parse_feed_datetime(node.get("datetime"))
            canonical = soup.find("link", rel="canonical")
            if canonical and canonical.get("href"):
                target = urljoin(current, canonical["href"])
                if urlparse(target).hostname == urlparse(current).hostname:
                    current = target
            return {
                "url": normalize_wire_url(current),
                "title": extracted["title"], "text": extracted["text"],
                "published": published.isoformat() if published else None,
            }
    raise ValueError("Source has too many redirects")


def collect_group_links(limit_messages: int = 50, limit_links: int = 5) -> dict:
    from apps.workers.services import ingest_rss_items

    root = account_directory()
    config_path, state_path = root / "config.json", root / "state.json"
    if not config_path.exists():
        return {"skipped": True, "reason": "telegram_account_not_connected"}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("enabled"):
        return {"skipped": True, "reason": "telegram_account_disabled"}
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    pending = state.get("pending", [])
    messages = []
    if len(pending) < 200:
        messages = asyncio.run(asyncio.wait_for(
            _read_messages(config, int(state.get("cursor", 0)), max(1, min(limit_messages, 100))),
            timeout=45,
        ))
        for message in messages:
            for url in message["urls"]:
                pending.append({**message, "url": url, "attempts": 0})
            state["cursor"] = max(int(state.get("cursor", 0)), message["id"])
        state["pending"] = pending
        write_private_json(state_path, state)

    stats = {"messages_seen": len(messages), "links_checked": 0, "duplicates": 0, "unsafe": 0, "expired": 0, "failed": 0}
    remaining, items = [], []
    failed = state.get("failed_links", [])
    now = timezone.now()
    for candidate in pending:
        posted = parse_feed_datetime(candidate.get("posted_at"))
        if not posted or posted < now - timedelta(days=30):
            stats["expired"] += 1
            continue
        next_retry = parse_feed_datetime(candidate.get("next_retry_at"))
        if stats["links_checked"] >= max(1, min(limit_links, 5)) or (next_retry and next_retry > now):
            remaining.append(candidate)
            continue
        stats["links_checked"] += 1
        url = candidate["url"]
        if find_threat_by_normalized_url(url):
            stats["duplicates"] += 1
            continue
        try:
            validate_public_http_url(url)
            article = _read_article(url)
        except UnsafeURLError:
            stats["unsafe"] += 1
            continue
        except (httpx.HTTPError, ValueError, OSError):
            # Retain failures for later polls; a blocked page must not lose its URL.
            candidate["attempts"] = int(candidate.get("attempts", 0)) + 1
            if candidate["attempts"] >= 6:
                failed.append({"url": url, "message_id": candidate["id"], "reason": "source_unreadable"})
                stats["failed"] += 1
                continue
            minutes = min(360, 2 ** min(candidate["attempts"], 8))
            candidate["next_retry_at"] = (now + timedelta(minutes=minutes)).isoformat()
            remaining.append(candidate)
            continue
        if find_threat_by_normalized_url(article["url"]):
            stats["duplicates"] += 1
            continue
        telegram_title, telegram_summary = _telegram_caption_parts(
            candidate["caption"], article["title"], ""
        )
        title = telegram_title[:512]
        if not title or len(article["text"].strip()) < 120:
            candidate["attempts"] = int(candidate.get("attempts", 0)) + 1
            if candidate["attempts"] >= 6:
                failed.append({"url": url, "message_id": candidate["id"], "reason": "insufficient_article_content"})
                stats["failed"] += 1
                continue
            candidate["next_retry_at"] = (now + timedelta(hours=1)).isoformat()
            remaining.append(candidate)
            continue
        if Threat.objects.filter(title__iexact=title).exists():
            stats["duplicates"] += 1
            continue
        items.append({
            "title": title, "summary": telegram_summary,
            "title_vi": telegram_title,
            "title_vi_status": "skipped",
            "title_vi_provider": "telegram-caption",
            "description": candidate["caption"][:1500], "link": article["url"],
            "published": article["published"] or candidate["posted_at"],
            "publication_date_basis": "article" if article["published"] else "telegram_message",
            "feed": "telegram-vlc-group", "category": "news",
            "discovery": "telegram-group", "metadata_only": False,
            "telegram_caption_title": telegram_title,
            "telegram_message_url": candidate["message_url"],
            "telegram_message_id": candidate["id"],
            "telegram_group_title": config["group_title"],
            "telegram_posted_at": candidate["posted_at"],
        })
    # Commit ingestion before advancing the durable queue. If it fails, the
    # queued URLs survive; retrying them is safe through the normal URL dedupe.
    stats.update(ingest_rss_items(items, source_label="telegram-vlc-group"))
    state["pending"] = remaining
    state["failed_links"] = failed[-1000:]
    state["last_scan_at"] = timezone.now().isoformat()
    state["last_scan_stats"] = stats
    write_private_json(state_path, state)
    stats["pending_links"] = len(remaining)
    return stats
