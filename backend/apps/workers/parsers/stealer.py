"""
Parse infostealer credential dumps (RedLine / Raccoon / Vidar style).

Supported line shapes (skip comments / blanks):
  - url:username:password
  - URL | USER | PASS
  - Soft / browser exports with tab separators
  - Multi-line blocks:
        URL: https://...
        Username: ...
        Password: ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse


STEALER_HINTS = {
    "redline": re.compile(r"redline", re.I),
    "raccoon": re.compile(r"raccoon|rastealer", re.I),
    "vidar": re.compile(r"vidar", re.I),
}

URL_USER_PASS = re.compile(
    r"^(?P<url>https?://[^\s|:]+|\S+\.[^\s|:]+)"
    r"\s*[|:]\s*"
    r"(?P<user>[^\s|:]+)"
    r"\s*[|:]\s*"
    r"(?P<password>.+)$",
    re.IGNORECASE,
)

PIPE_LINE = re.compile(
    r"^(?P<url>[^|]+)\|(?P<user>[^|]+)\|(?P<password>.+)$"
)

TAB_LINE = re.compile(
    r"^(?P<url>[^\t]+)\t(?P<user>[^\t]+)\t(?P<password>.+)$"
)

BLOCK_URL = re.compile(r"^\s*(?:URL|Host|Hostname)\s*[:=]\s*(?P<value>.+)\s*$", re.I)
BLOCK_USER = re.compile(
    r"^\s*(?:Username|User|Login|Email)\s*[:=]\s*(?P<value>.+)\s*$", re.I
)
BLOCK_PASS = re.compile(r"^\s*(?:Password|Pass|Pwd)\s*[:=]\s*(?P<value>.+)\s*$", re.I)


@dataclass(frozen=True)
class ParsedCredential:
    url: str = ""
    domain: str = ""
    email: str = ""
    username: str = ""
    password: str = ""
    stealer_family: str = "unknown"
    raw_line: str = ""


def detect_stealer_family(text: str, default: str = "unknown") -> str:
    for family, pattern in STEALER_HINTS.items():
        if pattern.search(text):
            return family
    return default


def extract_domain(url: str) -> str:
    candidate = (url or "").strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return ""
    return host.lower().lstrip(".")


def _split_identity(user: str) -> tuple[str, str]:
    user = (user or "").strip()
    if "@" in user and " " not in user:
        return user, user.split("@", 1)[0]
    return "", user


def _normalize_url(url: str) -> str:
    url = (url or "").strip().strip("\"'")
    if not url:
        return ""
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "ftps://")):
        return url
    if "://" in url:
        return url
    if "." in url:
        return f"https://{url}"
    return url


def parse_credential_line(
    line: str, stealer_family: str = "unknown"
) -> ParsedCredential | None:
    raw = line.rstrip("\n")
    stripped = raw.strip()
    if not stripped or stripped.startswith(("#", ";", "//")):
        return None

    match = URL_USER_PASS.match(stripped) or PIPE_LINE.match(stripped) or TAB_LINE.match(
        stripped
    )
    if not match:
        return None

    url = _normalize_url(match.group("url"))
    email, username = _split_identity(match.group("user"))
    password = match.group("password").strip()
    if not password:
        return None

    return ParsedCredential(
        url=url[:2048],
        domain=extract_domain(url),
        email=email[:254],
        username=username[:255],
        password=password[:512],
        stealer_family=stealer_family,
        raw_line=raw[:4000],
    )


def parse_stealer_log(
    content: str, stealer_family: str | None = None
) -> list[ParsedCredential]:
    """Parse dump text into credential records (deduped by email/user+domain+password)."""
    family = stealer_family or detect_stealer_family(content)
    results: list[ParsedCredential] = []
    seen: set[tuple[str, str, str, str]] = set()

    # Multi-line blocks first
    current: dict[str, str] = {}
    for line in content.splitlines():
        m_url = BLOCK_URL.match(line)
        m_user = BLOCK_USER.match(line)
        m_pass = BLOCK_PASS.match(line)
        if m_url:
            if current.get("url") and current.get("user") and current.get("password"):
                _append_block(current, family, results, seen)
            current = {"url": m_url.group("value").strip()}
            continue
        if m_user and current is not None:
            current["user"] = m_user.group("value").strip()
            continue
        if m_pass and current is not None:
            current["password"] = m_pass.group("value").strip()
            if current.get("url") and current.get("user"):
                _append_block(current, family, results, seen)
                current = {}
            continue

        parsed = parse_credential_line(line, stealer_family=family)
        if parsed:
            key = (parsed.email, parsed.username, parsed.domain, parsed.password)
            if key not in seen:
                seen.add(key)
                results.append(parsed)

    if current.get("url") and current.get("user") and current.get("password"):
        _append_block(current, family, results, seen)

    return results


def _append_block(
    block: dict[str, str],
    family: str,
    results: list[ParsedCredential],
    seen: set[tuple[str, str, str, str]],
) -> None:
    url = _normalize_url(block.get("url", ""))
    email, username = _split_identity(block.get("user", ""))
    password = (block.get("password") or "").strip()
    if not password:
        return
    parsed = ParsedCredential(
        url=url[:2048],
        domain=extract_domain(url),
        email=email[:254],
        username=username[:255],
        password=password[:512],
        stealer_family=family,
        raw_line=f"URL: {url} | User: {email or username}",
    )
    key = (parsed.email, parsed.username, parsed.domain, parsed.password)
    if key not in seen:
        seen.add(key)
        results.append(parsed)


def iter_batches(items: Iterable[ParsedCredential], size: int = 200):
    batch: list[ParsedCredential] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
