"""Subprocess runner for the vendored last30days CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from .paths import LAST30DAYS_SCRIPT, vendor_root

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = ("reddit", "x", "polymarket")


@dataclass(frozen=True)
class Last30DaysRunResult:
    ok: bool
    payload: dict[str, Any]
    stderr: str
    returncode: int
    duration_ms: int


def default_sources() -> list[str]:
    raw = getattr(settings, "LAST30DAYS_SOURCES", "") or ""
    if isinstance(raw, (list, tuple)):
        tokens = [str(x).strip().lower() for x in raw]
    else:
        tokens = [t.strip().lower() for t in str(raw).split(",")]
    # GitHub / Hacker News dropped from defaults (noisy / out of scope).
    sources = [t for t in tokens if t and t not in {"github", "hackernews"}]
    return sources or list(DEFAULT_SOURCES)


def resolve_web_backend() -> str:
    configured = (getattr(settings, "LAST30DAYS_WEB_BACKEND", "") or "").strip().lower()
    if configured in {
        "auto",
        "brave",
        "exa",
        "serper",
        "parallel",
        "wigolo",
        "keyless",
        "none",
    }:
        if configured != "auto":
            return configured
    # auto / empty: prefer Wigolo sidecar, then Exa, else none (collectors still run).
    try:
        from apps.integrations.web_reader.wigolo import wigolo_configured

        if wigolo_configured():
            return "wigolo"
    except Exception:  # noqa: BLE001
        pass
    if getattr(settings, "EXA_API_KEY", ""):
        return "exa"
    return "none"


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    # Prefer Django settings over ambient env for known keys.
    exa = getattr(settings, "EXA_API_KEY", "") or ""
    if exa:
        env["EXA_API_KEY"] = exa
    for key in (
        "BRAVE_API_KEY",
        "SERPER_API_KEY",
        "PARALLEL_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "PERPLEXITY_API_KEY",
        "XAI_API_KEY",
        "SCRAPECREATORS_API_KEY",
        "GITHUB_TOKEN",
    ):
        val = getattr(settings, key, None)
        if val:
            env[key] = str(val)
    wigolo_url = getattr(settings, "WIGOLO_URL", "") or ""
    if wigolo_url:
        env["WIGOLO_URL"] = str(wigolo_url).rstrip("/")
    wigolo_token = getattr(settings, "WIGOLO_API_TOKEN", "") or ""
    if wigolo_token:
        env["WIGOLO_API_TOKEN"] = str(wigolo_token)
    # Map NewsCrawler X cookies → last30days bird_x names.
    auth = getattr(settings, "X_AUTH_TOKEN", "") or getattr(settings, "AUTH_TOKEN", "") or ""
    ct0 = getattr(settings, "X_CT0", "") or getattr(settings, "CT0", "") or ""
    if auth:
        env["AUTH_TOKEN"] = str(auth)
        env["X_AUTH_TOKEN"] = str(auth)
    if ct0:
        env["CT0"] = str(ct0)
        env["X_CT0"] = str(ct0)
    reddit_cookie = getattr(settings, "REDDIT_COOKIE", "") or ""
    if reddit_cookie:
        env["REDDIT_COOKIE"] = str(reddit_cookie)
    # Avoid interactive cookie/browser probes in headless workers.
    env.setdefault("LAST30DAYS_NO_BROWSER_COOKIES", "1")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def build_command(
    *,
    topic: str,
    days: int,
    depth: str,
    sources: list[str] | None = None,
    max_results: int | None = None,
) -> list[str]:
    src = sources or default_sources()
    cmd = [
        sys.executable,
        str(LAST30DAYS_SCRIPT),
        topic,
        "--search",
        ",".join(src),
        "--days",
        str(max(1, min(int(days), 90))),
        "--emit",
        "json",
        "--json-profile",
        "raw",
        "--web-backend",
        resolve_web_backend(),
        "--no-browser-cookies",
    ]
    if depth == "quick":
        cmd.append("--quick")
    elif depth == "deep":
        cmd.append("--deep")
    if max_results:
        cmd.extend(["--max-results", str(int(max_results))])
    return cmd


def run_cli(
    *,
    topic: str,
    days: int = 30,
    depth: str = "quick",
    sources: list[str] | None = None,
    max_results: int | None = None,
    timeout_sec: int | None = None,
) -> Last30DaysRunResult:
    if not LAST30DAYS_SCRIPT.is_file():
        raise FileNotFoundError(f"last30days script missing: {LAST30DAYS_SCRIPT}")

    timeout = int(
        timeout_sec
        if timeout_sec is not None
        else getattr(settings, "LAST30DAYS_TIMEOUT_SEC", 300) or 300
    )
    timeout = max(60, min(timeout, 900))
    cmd = build_command(
        topic=topic,
        days=days,
        depth=depth,
        sources=sources,
        max_results=max_results,
    )
    logger.info("last30days cli: %s", " ".join(cmd))

    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(vendor_root() / "scripts"),
            env=build_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return Last30DaysRunResult(
            ok=False,
            payload={},
            stderr=stderr or f"Timed out after {timeout}s",
            returncode=124,
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    payload: dict[str, Any] = {}
    if stdout:
        # CLI may print non-JSON banners; take the last JSON object.
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            brace = stdout.rfind("\n{")
            if brace < 0 and stdout.startswith("{"):
                brace = 0
            elif brace >= 0:
                brace += 1
            if brace >= 0:
                try:
                    payload = json.loads(stdout[brace:])
                except json.JSONDecodeError:
                    payload = {}

    ok = proc.returncode == 0 and bool(payload)
    if not ok and not stderr:
        stderr = f"last30days exited {proc.returncode} without parseable JSON"
    return Last30DaysRunResult(
        ok=ok,
        payload=payload,
        stderr=stderr,
        returncode=proc.returncode,
        duration_ms=duration_ms,
    )
