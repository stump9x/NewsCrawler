"""Small GitHub REST client with strict host, size, and rate-limit controls."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings

API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 25.0
# Authenticated code search ≈ 30 req/min — stay just under that ceiling.
SEARCH_MIN_INTERVAL_SEC = 2.05
# Cap a single wait so a Celery task cannot sleep forever.
MAX_RATE_WAIT_SEC = 65.0
MAX_RATE_RETRIES = 2

logger = logging.getLogger(__name__)


class GitHubAPIError(RuntimeError):
    pass


class GitHubRateLimitError(GitHubAPIError):
    pass


def github_configured() -> bool:
    return bool((getattr(settings, "GITHUB_TOKEN", "") or "").strip())


class GitHubClient:
    def __init__(self) -> None:
        token = (getattr(settings, "GITHUB_TOKEN", "") or "").strip()
        if not token:
            raise GitHubAPIError("GITHUB_TOKEN is not configured")
        self.request_count = 0
        self.rate_limit_remaining: int | None = None
        self.search_rate_limit_remaining: int | None = None
        self._search_reset_at: float | None = None
        self._core_reset_at: float | None = None
        self._last_search_at = 0.0
        self._client = httpx.Client(
            base_url=API_BASE,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.text-match+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "NewsCrawler-GitHub-Scanner/1.0",
            },
        )

    def __enter__(self):
        self._client.__enter__()
        return self

    def __exit__(self, *args):
        return self._client.__exit__(*args)

    def _parse_int_header(self, response: httpx.Response, name: str) -> int | None:
        raw = response.headers.get(name)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _update_rate_limits(self, response: httpx.Response, *, search: bool) -> None:
        remaining = self._parse_int_header(response, "x-ratelimit-remaining")
        reset = self._parse_int_header(response, "x-ratelimit-reset")
        resource = (response.headers.get("x-ratelimit-resource") or "").casefold()
        is_search = search or resource == "search"
        if remaining is not None:
            if is_search:
                self.search_rate_limit_remaining = remaining
            else:
                self.rate_limit_remaining = remaining
        if reset is not None:
            reset_at = float(reset)
            if is_search:
                self._search_reset_at = reset_at
            else:
                self._core_reset_at = reset_at

    def _wait_seconds(self, seconds: float, *, reason: str) -> None:
        delay = max(0.0, min(float(seconds), MAX_RATE_WAIT_SEC))
        if delay <= 0:
            return
        logger.info("GitHub client waiting %.1fs (%s)", delay, reason)
        time.sleep(delay)

    def _wait_for_budget(self, *, search: bool) -> None:
        """Sleep until the relevant quota resets when remaining is exhausted."""
        remaining = (
            self.search_rate_limit_remaining if search else self.rate_limit_remaining
        )
        if remaining is None or remaining > 0:
            return
        reset_at = self._search_reset_at if search else self._core_reset_at
        if reset_at is None:
            self._wait_seconds(2.0, reason="rate-limit unknown reset")
            return
        delay = reset_at - time.time() + 0.4
        if delay > MAX_RATE_WAIT_SEC:
            raise GitHubRateLimitError(
                "GitHub search rate budget exhausted"
                if search
                else "GitHub API rate budget exhausted"
            )
        self._wait_seconds(delay, reason="rate-limit reset")

    def _pace_search(self) -> None:
        elapsed = time.monotonic() - self._last_search_at
        if self._last_search_at and elapsed < SEARCH_MIN_INTERVAL_SEC:
            time.sleep(SEARCH_MIN_INTERVAL_SEC - elapsed)

    def _is_rate_limit_response(self, response: httpx.Response) -> bool:
        if response.status_code not in {403, 429}:
            return False
        detail = ""
        try:
            detail = str(response.json().get("message") or "")
        except (ValueError, AttributeError, TypeError):
            detail = ""
        folded = detail.casefold()
        return (
            response.status_code == 429
            or "rate limit" in folded
            or "secondary rate" in folded
            or self._parse_int_header(response, "x-ratelimit-remaining") == 0
        )

    def _retry_after_seconds(self, response: httpx.Response, *, search: bool) -> float:
        retry_after = self._parse_int_header(response, "retry-after")
        if retry_after is not None:
            return float(retry_after) + 0.25
        reset = self._parse_int_header(response, "x-ratelimit-reset")
        if reset is not None:
            return max(0.5, float(reset) - time.time() + 0.4)
        reset_at = self._search_reset_at if search else self._core_reset_at
        if reset_at is not None:
            return max(0.5, reset_at - time.time() + 0.4)
        return 2.0

    def _get(self, url: str, *, search: bool = False, **kwargs) -> httpx.Response:
        parsed = urlparse(url)
        if parsed.scheme and (
            parsed.scheme != "https" or parsed.hostname != "api.github.com"
        ):
            raise GitHubAPIError("Refusing non-GitHub API URL")

        attempts = 0
        while True:
            if search:
                self._wait_for_budget(search=True)
                self._pace_search()
            else:
                self._wait_for_budget(search=False)

            try:
                response = self._client.get(url, **kwargs)
            except httpx.HTTPError as exc:
                raise GitHubAPIError("GitHub API network request failed") from exc

            self.request_count += 1
            if search:
                self._last_search_at = time.monotonic()
            self._update_rate_limits(response, search=search)

            if self._is_rate_limit_response(response):
                attempts += 1
                if attempts > MAX_RATE_RETRIES:
                    raise GitHubRateLimitError(
                        "GitHub search rate budget exhausted"
                        if search
                        else "GitHub API rate limit exhausted"
                    )
                delay = self._retry_after_seconds(response, search=search)
                if delay > MAX_RATE_WAIT_SEC:
                    raise GitHubRateLimitError(
                        "GitHub search rate budget exhausted"
                        if search
                        else "GitHub API rate limit exhausted"
                    )
                self._wait_seconds(delay, reason="rate-limit retry")
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = ""
                try:
                    detail = str(response.json().get("message") or "")
                except (ValueError, AttributeError):
                    pass
                raise GitHubAPIError(
                    f"GitHub API returned HTTP {response.status_code}: {detail[:200]}"
                ) from exc
            return response

    def search_code(
        self, query: str, *, page: int = 1, per_page: int = 100
    ) -> dict[str, Any]:
        response = self._get(
            "/search/code",
            search=True,
            params={
                "q": query[:1000],
                "page": max(1, page),
                "per_page": max(1, min(per_page, 100)),
            },
        )
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def fetch_text_content(self, api_url: str, *, max_bytes: int) -> str | None:
        response = self._get(api_url, search=False)
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        size = int(payload.get("size") or 0)
        if size <= 0 or size > max_bytes or payload.get("encoding") != "base64":
            return None
        encoded = str(payload.get("content") or "").replace("\n", "")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return None
        if len(raw) > max_bytes or b"\x00" in raw[:8192]:
            return None
        return raw.decode("utf-8", errors="replace")
