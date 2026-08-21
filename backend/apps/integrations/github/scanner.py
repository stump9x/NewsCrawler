"""Optimized two-lane GitHub code search with incremental persist and low memory use."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.github.client import (
    GitHubAPIError,
    GitHubClient,
    GitHubRateLimitError,
)
from apps.integrations.github.detector import SecretAlert, detect_secrets
from apps.integrations.models import GitHubFinding, GitHubScan
from apps.intel.models import DataLeak
from apps.intel.watching import match_leaks_against_rules

logger = logging.getLogger(__name__)

HIGH_VALUE_EXTENSIONS = {
    "env",
    "yml",
    "yaml",
    "json",
    "ini",
    "conf",
    "config",
    "properties",
    "sql",
    "xml",
    "tf",
    "py",
    "js",
    "ts",
    "php",
    "java",
    "cs",
    "go",
    "rb",
    "sh",
    "ps1",
}
SENSITIVE_NAMES = {
    ".env",
    ".env.production",
    ".env.local",
    "credentials.json",
    "secrets.yml",
    "secrets.yaml",
    "application.properties",
    "docker-compose.yml",
    "settings.py",
    "config.json",
    "appsettings.json",
}
# Config / secret-bearing extensions — prefer body download for leak detection.
# Note: plain `.json` is NOT always fetched (document corpora burn the budget).
ALWAYS_FETCH_EXTENSIONS = {
    "env",
    "yml",
    "yaml",
    "ini",
    "conf",
    "config",
    "properties",
    "sql",
    "tf",
    "toml",
    "cfg",
    "pem",
}
CONFIG_PATH_HINTS = (
    "secret",
    "credential",
    "password",
    ".env",
    "docker-compose",
    "appsettings",
    "application.properties",
    "settings.py",
    "/config/",
    "\\config\\",
)

# Keyword + secret-term co-occurrence searches (fills Alerts column).
# Keep each query to a single extra term — GitHub rejects large OR groups.
SECRET_SEARCH_TERMS = (
    "password",
    "DB_PASSWORD",
    "DATABASE_URL",
    "SECRET_KEY",
    "MYSQL_ROOT_PASSWORD",
    "AWS_SECRET_ACCESS_KEY",
    "postgres://",
    "mysql://",
    "connectionString",
)

# Targeted filename searches — one qualifier per query.
# GitHub code search REST rejects >5 OR/AND/NOT and parenthesized groups (HTTP 422).
SENSITIVE_FILENAMES = (
    ".env",
    ".env.production",
    ".env.local",
    "credentials.json",
    "secrets.yml",
    "secrets.yaml",
    "docker-compose.yml",
    "application.properties",
    "settings.py",
    "config.json",
    "appsettings.json",
)
SEVERITY_SCORE = {"info": 0, "medium": 20, "high": 60, "critical": 100}
MAX_MATCH_LINES = 20
MAX_FRAGMENT_CHARS = 8_000


def _quoted_keyword(keyword: str) -> str:
    clean = " ".join((keyword or "").split())
    return f'"{clean.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'


def _build_search_lanes(phrase: str) -> list[tuple[str, int, str]]:
    """Build lanes: secret co-occurrence → non-.txt → filenames → .txt.

    Each item is (query, max_pages, kind) where kind drives fetch priority.
    """
    lanes: list[tuple[str, int, str]] = [
        (f"{phrase} {term} in:file", 1, "secret") for term in SECRET_SEARCH_TERMS
    ]
    lanes.append((f"{phrase} in:file -extension:txt", 10, "non_txt"))
    lanes.extend(
        (f"{phrase} filename:{name}", 1, "filename") for name in SENSITIVE_FILENAMES
    )
    lanes.append((f"{phrase} in:file extension:txt", 5, "txt"))
    return lanes


def _is_query_parse_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "422" in message and (
        "parse" in message
        or "unable to parse" in message
        or "validation failed" in message
    )


def _path_looks_like_config(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in SENSITIVE_NAMES:
        return True
    lower = path.lower().replace("\\", "/")
    return any(hint in lower for hint in CONFIG_PATH_HINTS)


def _extension(path: str) -> str:
    return PurePosixPath(path).suffix.lower().lstrip(".")[:32]


def _text_match_content(item: dict[str, Any]) -> str:
    fragments = []
    for match in item.get("text_matches") or []:
        if isinstance(match, dict) and match.get("fragment"):
            fragments.append(str(match["fragment"]))
    return "\n".join(fragments)[:MAX_FRAGMENT_CHARS]


def _has_text_matches(item: dict[str, Any]) -> bool:
    matches = item.get("text_matches") or []
    return any(
        isinstance(match, dict) and match.get("fragment") for match in matches
    )


def _find_keyword_lines(
    content: str, keyword: str, *, limit: int = MAX_MATCH_LINES
) -> tuple[list[int], int]:
    """Return (line numbers, match count). Prefer _find_keyword_hits for snippets."""
    lines, total, _snippets = _find_keyword_hits(content, keyword, limit=limit)
    return lines, total


def _find_keyword_hits(
    content: str, keyword: str, *, limit: int = MAX_MATCH_LINES
) -> tuple[list[int], int, list[dict[str, Any]]]:
    """Return (line numbers, match count, snippets with line + text)."""
    needle = (keyword or "").casefold()
    if not content or not needle:
        return [], 0, []
    lines: list[int] = []
    snippets: list[dict[str, Any]] = []
    total = 0
    start = 0
    line_no = 1
    length = len(content)
    while start <= length:
        end = content.find("\n", start)
        if end < 0:
            end = length
        line = content[start:end]
        if line.endswith("\r"):
            line = line[:-1]
        count = line.casefold().count(needle)
        if count:
            total += count
            if len(lines) < limit:
                lines.append(line_no)
                snippets.append(
                    {
                        "line": line_no,
                        "text": line.strip()[:500],
                    }
                )
        if end == length:
            break
        start = end + 1
        line_no += 1
    return lines, total, snippets


def _fragment_keyword_hits(
    fragments: str, keyword: str, *, limit: int = MAX_MATCH_LINES
) -> list[dict[str, Any]]:
    """Build keyword snippets from search fragments when full file was not fetched."""
    needle = (keyword or "").casefold()
    if not fragments or not needle:
        return []
    snippets: list[dict[str, Any]] = []
    for part in fragments.splitlines():
        text = part.strip()
        if not text or needle not in text.casefold():
            continue
        snippets.append({"line": None, "text": text[:500]})
        if len(snippets) >= limit:
            break
    return snippets


def _with_line_anchor(url: str, line: int | None) -> str:
    if not url or not line:
        return url
    return f"{url.split('#', 1)[0]}#L{int(line)}"


def _should_fetch_content(
    *,
    is_text_file: bool,
    has_fragments: bool,
    path: str,
    content_fetches: int,
    fetch_limit: int,
    force_fetch: bool = False,
) -> bool:
    """Fetch full file when secret/config inspection needs the body."""
    if content_fetches >= fetch_limit:
        return False
    name = PurePosixPath(path).name.lower()
    ext = _extension(path)
    # Secret / filename lanes: always pull body while budget remains.
    if force_fetch:
        return True
    # Known secret filenames and config-looking paths.
    if name in SENSITIVE_NAMES or _path_looks_like_config(path):
        return True
    # Secret-bearing extensions (not blanket .json — that burns budget on corpora).
    if ext in ALWAYS_FETCH_EXTENSIONS:
        return True
    if ext == "json" and (
        name in SENSITIVE_NAMES or "config" in name or "secret" in name
    ):
        return True
    # Other high-value code: fetch when fragments are missing.
    if ext in HIGH_VALUE_EXTENSIONS and not has_fragments:
        return True
    # Generic non-.txt with no fragment: shared budget.
    if not is_text_file and not has_fragments:
        return content_fetches < max(1, fetch_limit // 2)
    return False


def _score(path: str, alerts: list[SecretAlert], keyword_matches: int) -> int:
    ext = _extension(path)
    value = 100 if ext != "txt" else 0
    if ext in HIGH_VALUE_EXTENSIONS:
        value += 30
    if PurePosixPath(path).name.lower() in SENSITIVE_NAMES:
        value += 40
    value += min(keyword_matches, 20)
    value += max((SEVERITY_SCORE.get(alert.severity, 0) for alert in alerts), default=0)
    return value


def _path_is_sensitive(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in SENSITIVE_NAMES:
        return True
    ext = _extension(path)
    return ext in {"env", "pem", "key", "p12", "pfx"}


def _item_priority_key(item: dict[str, Any]) -> tuple:
    path = str(item.get("path") or "")
    ext = _extension(path)
    return (
        not _path_is_sensitive(path),
        ext == "txt",
        ext not in HIGH_VALUE_EXTENSIONS,
    )


def _search_lane_pages(
    client: GitHubClient,
    query: str,
    *,
    remaining: int,
    max_pages: int = 10,
) -> Iterator[tuple[list[dict[str, Any]], bool]]:
    """Yield bounded GitHub result pages so callers can persist incrementally."""
    received = 0
    page = 1
    # GitHub code search hard-stops around page 10 (≈1000 hits per query).
    page_cap = max(1, min(int(max_pages), 10))
    while received < remaining and page <= page_cap:
        payload = client.search_code(
            query,
            page=page,
            # Keep page size stable: GitHub calculates offsets from page*per_page.
            per_page=100,
        )
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        valid_rows = [
            row for row in rows if isinstance(row, dict)
        ][: remaining - received]
        if not valid_rows:
            break
        total = int(payload.get("total_count") or 0)
        received += len(valid_rows)
        hard_cap = min(total, page_cap * 100, 1000)
        yield valid_rows, total > 1000 or total > remaining or hard_cap < total
        if len(rows) < 100 or received >= hard_cap:
            break
        page += 1


def _persist_leak_alerts(
    scan: GitHubScan, findings: list[GitHubFinding]
) -> None:
    alert_findings = [finding for finding in findings if finding.alert_types]
    if not alert_findings:
        return
    urls = [finding.html_url.split("#", 1)[0] for finding in alert_findings]
    existing = {
        str(url).split("#", 1)[0]
        for url in DataLeak.objects.filter(source_url__in=urls).values_list(
            "source_url", flat=True
        )
    }
    leaks: list[DataLeak] = []
    for finding in alert_findings:
        base_url = finding.html_url.split("#", 1)[0]
        if base_url in existing or finding.html_url in existing:
            continue
        leak_type = (
            DataLeak.LeakType.API_KEY
            if any(
                kind in {"api-key", "github-token", "aws-access-key", "private-key"}
                for kind in finding.alert_types
            )
            else DataLeak.LeakType.CREDENTIALS
        )
        leaks.append(
            DataLeak(
                title=f"GitHub exposure: {finding.repository}/{finding.file_path}"[
                    :512
                ],
                description=(
                    "Potential configuration or credential exposure detected by "
                    "GitHub Scanner. Evidence is stored as found in the repository."
                ),
                leak_type=leak_type,
                severity=finding.severity,
                status=DataLeak.Status.NEW,
                source=DataLeak.Source.GITHUB,
                source_url=base_url,
                affected_organization=finding.owner[:255],
                metadata={
                    "github_scan_id": scan.id,
                    "repository": finding.repository,
                    "file_path": finding.file_path,
                    "match_lines": finding.match_lines[:MAX_MATCH_LINES],
                    "alert_types": finding.alert_types,
                    "evidence": (finding.evidence or "")[:500],
                },
                created_by=scan.created_by,
            )
        )
        existing.add(base_url)
    created = DataLeak.objects.bulk_create(leaks, batch_size=100)
    match_leaks_against_rules(created)


def _drop_single_txt_only_repos(scan: GitHubScan) -> int:
    """Remove repos that only contributed one .txt hit (0 non-.txt · 1 .txt)."""
    from django.db.models import Count, Q

    weak = list(
        scan.findings.values("repository")
        .annotate(
            file_count=Count("id"),
            non_text_count=Count("id", filter=Q(is_text_file=False)),
            text_count=Count("id", filter=Q(is_text_file=True)),
        )
        .filter(non_text_count=0, text_count=1, file_count=1)
        .values_list("repository", flat=True)
    )
    if not weak:
        return 0
    deleted, _ = scan.findings.filter(repository__in=weak).delete()
    return int(deleted)


def _recount_scan_stats(scan: GitHubScan) -> None:
    from django.db.models import Count, Q

    aggregates = scan.findings.aggregate(
        file_count=Count("id"),
        alert_count=Count("id", filter=~Q(alert_types=[])),
        critical_count=Count(
            "id", filter=Q(severity=GitHubFinding.Severity.CRITICAL)
        ),
        non_text_count=Count("id", filter=Q(is_text_file=False)),
        repository_count=Count("repository", distinct=True),
    )
    scan.file_count = int(aggregates["file_count"] or 0)
    scan.alert_count = int(aggregates["alert_count"] or 0)
    scan.critical_count = int(aggregates["critical_count"] or 0)
    scan.non_text_count = int(aggregates["non_text_count"] or 0)
    scan.repository_count = int(aggregates["repository_count"] or 0)


def run_github_scan(scan: GitHubScan) -> GitHubScan:
    started = time.monotonic()
    scan.status = GitHubScan.Status.RUNNING
    scan.started_at = timezone.now()
    scan.error_message = ""
    scan.save(update_fields=["status", "started_at", "error_message", "updated_at"])

    max_cap = max(
        1,
        min(int(getattr(settings, "GITHUB_SCAN_MAX_RESULTS", 1500) or 1500), 1500),
    )
    max_results = max(1, min(int(scan.max_results or max_cap), max_cap))
    max_file_bytes = max(
        4096,
        min(int(getattr(settings, "GITHUB_MAX_FILE_BYTES", 512_000) or 512_000), 1_000_000),
    )
    fetch_limit = max(
        0,
        min(int(getattr(settings, "GITHUB_CONTENT_FETCH_LIMIT", 120) or 120), max_results),
    )
    stream_batch_size = max(
        1,
        min(int(getattr(settings, "GITHUB_STREAM_BATCH_SIZE", 3) or 3), 50),
    )
    # Bounded dedupe set — only (repo, path) keys, never file bodies.
    seen: set[tuple[str, str]] = set()
    repositories: set[str] = set()
    coverage_limited = False
    content_fetches = 0

    def save_progress(client: GitHubClient) -> None:
        scan.repository_count = len(repositories)
        scan.api_requests = client.request_count
        # Prefer search remaining while scanning — that is the scarce budget.
        scan.rate_limit_remaining = (
            client.search_rate_limit_remaining
            if isinstance(client.search_rate_limit_remaining, int)
            else client.rate_limit_remaining
        )
        scan.coverage_limited = coverage_limited
        scan.duration_ms = int((time.monotonic() - started) * 1000)
        scan.save(
            update_fields=[
                "repository_count",
                "file_count",
                "alert_count",
                "critical_count",
                "non_text_count",
                "api_requests",
                "rate_limit_remaining",
                "coverage_limited",
                "duration_ms",
                "updated_at",
            ]
        )

    try:
        with GitHubClient() as client:
            phrase = _quoted_keyword(scan.keyword)
            keyword_folded = scan.keyword.casefold()
            # 1) Secret co-occurrence (password/DB/config) — fills Alerts early.
            # 2) Non-.txt sweep for progressive repo list.
            # 3) Sensitive filenames, then .txt remainder.
            lanes = _build_search_lanes(phrase)
            non_txt_query = f"{phrase} in:file -extension:txt"
            txt_query = f"{phrase} in:file extension:txt"

            def process_rows(
                rows: list[dict[str, Any]], *, force_fetch: bool = False
            ) -> int:
                nonlocal content_fetches
                candidates: list[dict[str, Any]] = []
                for item in rows:
                    repo = (
                        item.get("repository")
                        if isinstance(item.get("repository"), dict)
                        else {}
                    )
                    key = (
                        str(repo.get("full_name") or ""),
                        str(item.get("path") or ""),
                    )
                    if (
                        not all(key)
                        or key in seen
                        or scan.file_count + len(candidates) >= max_results
                    ):
                        continue
                    seen.add(key)
                    candidates.append(item)
                # Within each page: sensitive paths first, then high-value, then .txt.
                candidates.sort(key=_item_priority_key)

                processed = 0
                for offset in range(0, len(candidates), stream_batch_size):
                    findings: list[GitHubFinding] = []
                    batch = candidates[offset : offset + stream_batch_size]
                    for item in batch:
                        repo = (
                            item.get("repository")
                            if isinstance(item.get("repository"), dict)
                            else {}
                        )
                        path = str(item.get("path") or "")[:1024]
                        ext = _extension(path)
                        is_text_file = ext == "txt"
                        fragments = _text_match_content(item)
                        content = fragments
                        fetched_full = False
                        if _should_fetch_content(
                            is_text_file=is_text_file,
                            has_fragments=bool(fragments),
                            path=path,
                            content_fetches=content_fetches,
                            fetch_limit=fetch_limit,
                            force_fetch=force_fetch,
                        ) and item.get("url"):
                            content_fetches += 1
                            try:
                                fetched = client.fetch_text_content(
                                    str(item["url"]), max_bytes=max_file_bytes
                                )
                            except GitHubAPIError as exc:
                                logger.info(
                                    "GitHub content fetch skipped for %s: %s",
                                    path,
                                    exc,
                                )
                            else:
                                if fetched is not None:
                                    content = fetched
                                    fetched_full = True

                        match_lines: list[int] = []
                        match_snippets: list[dict[str, Any]] = []
                        if fetched_full and content:
                            match_lines, keyword_matches, match_snippets = (
                                _find_keyword_hits(content, scan.keyword)
                            )
                            if keyword_matches <= 0:
                                keyword_matches = max(
                                    1, content.casefold().count(keyword_folded)
                                )
                        else:
                            keyword_matches = max(
                                1, (content or "").casefold().count(keyword_folded)
                            )
                            match_snippets = _fragment_keyword_hits(
                                content or "", scan.keyword
                            )

                        # Secret scan on fetched bodies and/or search fragments.
                        alerts = detect_secrets(content or "")
                        alert_line_numbers = [
                            alert.line_number
                            for alert in alerts
                            if alert.line_number
                        ]
                        # Caption lines = keyword hits first, then secret-only lines.
                        display_lines = list(match_lines)
                        for line_no in alert_line_numbers:
                            if line_no not in display_lines:
                                display_lines.append(line_no)
                        display_lines = sorted(display_lines)[:MAX_MATCH_LINES]
                        severity = max(
                            (alert.severity for alert in alerts),
                            key=lambda level: SEVERITY_SCORE.get(level, 0),
                            default="info",
                        )
                        html_url = _with_line_anchor(
                            str(item.get("html_url") or "")[:2048],
                            (match_lines[0] if match_lines else None)
                            or (display_lines[0] if display_lines else None),
                        )
                        findings.append(
                            GitHubFinding(
                                scan=scan,
                                repository=str(repo.get("full_name") or "")[:512],
                                owner=str(
                                    (repo.get("owner") or {}).get("login") or ""
                                )[:255],
                                file_path=path,
                                extension=ext,
                                html_url=html_url,
                                repository_url=str(repo.get("html_url") or "")[
                                    :2048
                                ],
                                is_text_file=is_text_file,
                                keyword_matches=keyword_matches,
                                severity=severity,
                                alert_types=list(
                                    dict.fromkeys(alert.kind for alert in alerts)
                                ),
                                evidence="\n".join(
                                    dict.fromkeys(alert.evidence for alert in alerts)
                                )[:5000],
                                match_lines=display_lines,
                                match_snippets=match_snippets[:MAX_MATCH_LINES],
                                score=_score(path, alerts, keyword_matches),
                                metadata={
                                    "sha": str(item.get("sha") or "")[:64],
                                    "api_url": str(item.get("url") or "")[:2048],
                                    "content_fetched": fetched_full,
                                },
                            )
                        )
                        # Drop large bodies promptly; do not retain across items.
                        content = ""
                        fragments = ""

                    snapshot = (
                        scan.file_count,
                        scan.alert_count,
                        scan.critical_count,
                        scan.non_text_count,
                        set(repositories),
                    )
                    try:
                        with transaction.atomic():
                            created = GitHubFinding.objects.bulk_create(
                                findings, batch_size=stream_batch_size
                            )
                            _persist_leak_alerts(scan, created)
                            repositories.update(
                                finding.repository for finding in created
                            )
                            scan.file_count += len(created)
                            scan.alert_count += sum(
                                bool(finding.alert_types) for finding in created
                            )
                            scan.critical_count += sum(
                                finding.severity
                                == GitHubFinding.Severity.CRITICAL
                                for finding in created
                            )
                            scan.non_text_count += sum(
                                not finding.is_text_file for finding in created
                            )
                            save_progress(client)
                    except Exception:
                        (
                            scan.file_count,
                            scan.alert_count,
                            scan.critical_count,
                            scan.non_text_count,
                            previous_repositories,
                        ) = snapshot
                        repositories.clear()
                        repositories.update(previous_repositories)
                        raise
                    processed += len(created)
                    findings.clear()
                candidates.clear()
                return processed

            rate_limited = False
            for _lane_index, (query, max_pages, lane_kind) in enumerate(lanes):
                if scan.file_count >= max_results or rate_limited:
                    break
                # No per-lane percentage cap — non-.txt / sensitive may use full budget.
                lane_remaining = max_results - scan.file_count
                if lane_remaining <= 0:
                    continue
                # Skip .txt lane when search budget is already tight after waiting.
                search_remaining = client.search_rate_limit_remaining
                if (
                    query == txt_query
                    and isinstance(search_remaining, int)
                    and search_remaining < 3
                ):
                    coverage_limited = True
                    break
                force_fetch = lane_kind in {"secret", "filename"}
                try:
                    for rows, limited in _search_lane_pages(
                        client,
                        query,
                        remaining=lane_remaining,
                        max_pages=max_pages,
                    ):
                        coverage_limited = coverage_limited or limited
                        process_rows(rows, force_fetch=force_fetch)
                        # Heartbeat even on empty pages so the UI shows live progress.
                        save_progress(client)
                        rows.clear()
                    # Lane finished (including zero-hit filename probes).
                    save_progress(client)
                except GitHubRateLimitError as exc:
                    rate_limited = True
                    coverage_limited = True
                    scan.error_message = str(exc)[:2000]
                    save_progress(client)
                    break
                except GitHubAPIError as exc:
                    # Invalid query syntax (422 parse) — skip lane; waiting will not help.
                    if _is_query_parse_error(exc):
                        logger.warning(
                            "GitHub scan %s skipping unparseable query %r: %s",
                            scan.pk,
                            query,
                            exc,
                        )
                        if query == non_txt_query:
                            # Some GitHub search versions reject negative extension qualifiers.
                            try:
                                for rows, limited in _search_lane_pages(
                                    client,
                                    f"{phrase} in:file",
                                    remaining=lane_remaining,
                                    max_pages=10,
                                ):
                                    coverage_limited = coverage_limited or limited
                                    process_rows(rows, force_fetch=False)
                                    rows.clear()
                            except GitHubRateLimitError as rate_exc:
                                rate_limited = True
                                coverage_limited = True
                                scan.error_message = str(rate_exc)[:2000]
                                save_progress(client)
                                break
                            except GitHubAPIError as fallback_exc:
                                logger.warning(
                                    "GitHub scan %s non-.txt fallback failed: %s",
                                    scan.pk,
                                    fallback_exc,
                                )
                                coverage_limited = True
                        else:
                            coverage_limited = True
                        continue
                    raise

            scan.api_requests = client.request_count
            scan.rate_limit_remaining = (
                client.search_rate_limit_remaining
                if isinstance(client.search_rate_limit_remaining, int)
                else client.rate_limit_remaining
            )
            scan.coverage_limited = coverage_limited
            # Drop low-signal repos (only a single .txt hit) before finalizing.
            if _drop_single_txt_only_repos(scan):
                _recount_scan_stats(scan)
            if rate_limited and scan.file_count:
                scan.status = GitHubScan.Status.PARTIAL
            else:
                scan.status = (
                    GitHubScan.Status.PARTIAL
                    if coverage_limited
                    else GitHubScan.Status.COMPLETED
                )
    except GitHubRateLimitError as exc:
        logger.warning("GitHub scan %s rate-limited: %s", scan.pk, exc)
        if scan.file_count:
            scan.status = GitHubScan.Status.PARTIAL
            scan.coverage_limited = True
        else:
            scan.status = GitHubScan.Status.FAILED
        scan.error_message = str(exc)[:2000]
    except Exception as exc:  # noqa: BLE001
        logger.exception("GitHub scan %s failed", scan.pk)
        scan.status = GitHubScan.Status.FAILED
        scan.error_message = str(exc)[:2000]

    scan.duration_ms = int((time.monotonic() - started) * 1000)
    scan.completed_at = timezone.now()
    scan.save(
        update_fields=[
            "status",
            "repository_count",
            "file_count",
            "alert_count",
            "critical_count",
            "non_text_count",
            "api_requests",
            "rate_limit_remaining",
            "coverage_limited",
            "duration_ms",
            "error_message",
            "completed_at",
            "updated_at",
        ]
    )
    return scan
