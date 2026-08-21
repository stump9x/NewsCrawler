#!/usr/bin/env python3
"""OpenAI-compatible Cerebras proxy with multi-key rotation for Open Notebook.

Open Notebook only accepts a single OPENAI_COMPATIBLE_API_KEY. This sidecar
exposes /v1/chat/completions (+ /v1/models) and rotates CEREBRAS_API_KEY(S)
on 429/402/401 so Notebook can burn the full key pool before falling through.

Never logs raw API keys. Stdlib only (runs on python:3.12-alpine).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = (os.environ.get("CEREBRAS_BASE_URL") or "https://api.cerebras.ai/v1").rstrip(
    "/"
)
LISTEN_HOST = os.environ.get("CEREBRAS_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("CEREBRAS_PROXY_PORT", "8088") or 8088)
COOLDOWN_SEC = float(os.environ.get("CEREBRAS_KEY_COOLDOWN_SEC", "120") or 120)
MIN_INTERVAL = float(os.environ.get("CEREBRAS_MIN_INTERVAL_SEC", "0.5") or 0.5)
MAX_KEY_ATTEMPTS = max(1, int(os.environ.get("CEREBRAS_MAX_KEY_ATTEMPTS", "4") or 4))
TIMEOUT = float(os.environ.get("CEREBRAS_TIMEOUT_SEC", "60") or 60)
DEFAULT_MODEL = (
    os.environ.get("CEREBRAS_MODEL")
    or os.environ.get("NOTEBOOK_CEREBRAS_CHAT_MODEL")
    or "gpt-oss-120b"
).strip() or "gpt-oss-120b"

_LOCK = threading.Lock()
_RR = 0
_COOL_UNTIL: dict[str, float] = {}
_LAST_CALL = 0.0


def _fp(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _parse_keys() -> list[str]:
    chunks: list[str] = []
    primary = (os.environ.get("CEREBRAS_API_KEY") or "").strip()
    if primary:
        chunks.append(primary)
    multi = os.environ.get("CEREBRAS_API_KEYS") or ""
    for part in multi.replace(";", ",").replace("\n", ",").split(","):
        token = part.strip()
        if token:
            chunks.append(token)
    seen: set[str] = set()
    out: list[str] = []
    for key in chunks:
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _ready_keys(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    ready = []
    for key in _parse_keys():
        until = _COOL_UNTIL.get(_fp(key), 0.0)
        if until <= now:
            ready.append(key)
    return ready


def _mark_cool(key: str, seconds: float | None = None) -> None:
    ttl = max(20.0, float(seconds if seconds is not None else COOLDOWN_SEC))
    with _LOCK:
        _COOL_UNTIL[_fp(key)] = time.time() + ttl


def _acquire() -> str | None:
    global _RR
    ready = _ready_keys()
    if not ready:
        return None
    with _LOCK:
        idx = _RR % len(ready)
        _RR += 1
        return ready[idx]


def _pace() -> None:
    global _LAST_CALL
    if MIN_INTERVAL <= 0:
        return
    with _LOCK:
        wait = MIN_INTERVAL - (time.time() - _LAST_CALL)
        if wait > 0:
            time.sleep(min(wait, 5.0))
        _LAST_CALL = time.time()


def _upstream(
    path: str, body: bytes | None, api_key: str, method: str = "POST"
) -> tuple[int, dict[str, str], bytes]:
    url = f"{UPSTREAM}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "NewsCrawler-CerebrasProxy/1.0",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers.items()), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, raw


def _is_rate_or_quota(status: int, raw: bytes) -> bool:
    if status in {429, 402, 503}:
        return True
    text = raw.decode("utf-8", errors="ignore").casefold()
    return any(
        t in text
        for t in (
            "rate limit",
            "rate_limit",
            "too many",
            "quota",
            "payment required",
            "payment_required",
            "free tier",
        )
    )


def _cooldown_from(status: int, headers: dict[str, str], raw: bytes) -> float:
    if status == 402:
        return 3600.0
    retry = (headers.get("Retry-After") or headers.get("retry-after") or "").strip()
    if retry:
        try:
            return max(20.0, float(retry))
        except ValueError:
            pass
    text = raw.decode("utf-8", errors="ignore").casefold()
    if "payment" in text or "quota" in text or "daily" in text:
        return 3600.0
    return COOLDOWN_SEC


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Avoid logging Authorization / bodies.
        print(f"[cerebras-proxy] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/health", "/healthz"}:
            keys = _parse_keys()
            ready = _ready_keys()
            self._json(
                200,
                {
                    "ok": True,
                    "keys": len(keys),
                    "ready": len(ready),
                    "upstream": UPSTREAM,
                    "default_model": DEFAULT_MODEL,
                },
            )
            return
        if path in {"/v1/models", "/models"}:
            key = _acquire() or ( _parse_keys()[0] if _parse_keys() else None)
            if not key:
                self._json(503, {"error": {"message": "no cerebras keys", "type": "proxy_error"}})
                return
            status, _hdrs, raw = _upstream("/models", None, key, method="GET")
            if status >= 400 and _is_rate_or_quota(status, raw):
                _mark_cool(key, _cooldown_from(status, _hdrs, raw))
            self._send(status, raw or b'{"data":[]}')
            return
        self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/v1/chat/completions", "/chat/completions"}:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "invalid json", "type": "invalid_request_error"}})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": {"message": "body must be object", "type": "invalid_request_error"}})
            return
        if not (payload.get("model") or "").strip():
            payload["model"] = DEFAULT_MODEL
        body = json.dumps(payload).encode("utf-8")

        keys = _parse_keys()
        if not keys:
            self._json(
                503,
                {
                    "error": {
                        "message": "Cerebras API keys not configured",
                        "type": "proxy_error",
                    }
                },
            )
            return

        errors: list[str] = []
        attempted: set[str] = set()
        attempts = min(MAX_KEY_ATTEMPTS, max(1, len(keys)))
        for _ in range(attempts):
            key = _acquire()
            if not key:
                break
            if key in attempted:
                remaining = [k for k in _ready_keys() if k not in attempted]
                if not remaining:
                    break
                key = remaining[0]
            attempted.add(key)
            _pace()
            status, hdrs, raw = _upstream("/chat/completions", body, key, method="POST")
            fp = _fp(key)
            if status < 400:
                # Ensure empty content is visible as failure-ish for callers.
                self._send(status, raw)
                return
            if status in {401, 403} or _is_rate_or_quota(status, raw):
                cool = _cooldown_from(status, hdrs, raw)
                _mark_cool(key, cool)
                errors.append(f"{fp}:HTTP{status}")
                time.sleep(0.2)
                continue
            # Non-retriable model/input error — pass through once.
            self._send(status, raw)
            return

        msg = "Cerebras keys exhausted: " + "; ".join(errors[:8])
        self._json(
            429 if errors else 503,
            {"error": {"message": msg, "type": "rate_limit_error", "code": "429"}},
        )


def main() -> None:
    keys = _parse_keys()
    print(
        f"[cerebras-proxy] listen={LISTEN_HOST}:{LISTEN_PORT} "
        f"keys={len(keys)} upstream={UPSTREAM} model={DEFAULT_MODEL}",
        flush=True,
    )
    if not keys:
        print("[cerebras-proxy] WARN: no CEREBRAS_API_KEY(S) configured", flush=True)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
