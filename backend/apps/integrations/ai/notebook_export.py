"""Push AI briefing sources into a fresh Open Notebook notebook."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


class NotebookExportError(Exception):
    pass


def notebook_base_url() -> str:
    return (
        getattr(settings, "NOTEBOOK_INTERNAL_URL", "") or "http://notebook-gateway:80"
    ).rstrip("/")


def notebook_public_url() -> str:
    """Browser-facing NewsCrawler origin for Notebook deep-links (:3000/notebook-ai)."""
    explicit = (
        getattr(settings, "NOTEBOOK_PUBLIC_URL", "")
        or getattr(settings, "FRONTEND_URL", "")
        or ""
    ).strip()
    if explicit:
        # Legacy subdomain URLs → strip to app origin (SPA hosts Notebook UI)
        cleaned = explicit.rstrip("/")
        try:
            parsed = urlparse(cleaned)
            host = parsed.hostname or ""
            if host.startswith("notebook."):
                rest = host[len("notebook.") :]
                if rest.endswith(".sslip.io") or rest.endswith(".nip.io"):
                    rest = rest[: -len(".sslip.io")] if rest.endswith(".sslip.io") else rest[: -len(".nip.io")]
                port = parsed.port
                scheme = parsed.scheme or "http"
                port_part = f":{port}" if port and port not in (80, 443) else ""
                return f"{scheme}://{rest}{port_part}"
            if host == "notebook.localhost":
                port = parsed.port
                scheme = parsed.scheme or "http"
                port_part = f":{port}" if port and port not in (80, 443) else ""
                return f"{scheme}://localhost{port_part}"
        except Exception:  # noqa: BLE001
            pass
        return cleaned

    return (getattr(settings, "FRONTEND_URL", "") or "http://127.0.0.1:3000").rstrip(
        "/"
    )


def extract_briefing_sources(briefing) -> list[dict[str, str]]:
    """Collect unique https sources from raw_response (+ URLs in content)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(title: str, url: str, kind: str = "web") -> None:
        u = (url or "").strip().rstrip(".,;:)")
        if not u.startswith("http"):
            return
        key = u.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "title": (title or u)[:300],
                "url": u[:2048],
                "kind": kind or "web",
            }
        )

    raw = briefing.raw_response if isinstance(briefing.raw_response, dict) else {}
    buckets: list[Any] = []
    for key in ("sources",):
        buckets.append(raw.get(key))
    pipe = raw.get("pipeline") if isinstance(raw.get("pipeline"), dict) else {}
    buckets.append(pipe.get("sources"))
    ck = raw.get("checkpoint") if isinstance(raw.get("checkpoint"), dict) else {}
    buckets.append(ck.get("sources"))

    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            add(
                str(item.get("title") or ""),
                str(item.get("url") or ""),
                str(item.get("kind") or "web"),
            )

    # Fallback: harvest links from the report body.
    for m in _URL_RE.finditer(briefing.content or ""):
        add("", m.group(0), "content")

    return out


def _notebook_name(briefing) -> str:
    title = " ".join(str(briefing.title or "Báo cáo OSINT").split())[:80]
    tz_name = getattr(settings, "TIME_ZONE", "Asia/Ho_Chi_Minh") or "Asia/Ho_Chi_Minh"
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        now = datetime.utcnow()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    return f"{title} · {stamp}"


def export_briefing_to_notebook(briefing) -> dict[str, Any]:
    """
    Create a NEW Open Notebook notebook and queue link sources into it.

    Always creates a fresh notebook so repeated exports do not pile into one.
    """
    sources = extract_briefing_sources(briefing)
    if not sources and not (briefing.content or "").strip():
        raise NotebookExportError("Báo cáo không có nguồn URL để thêm vào Notebook")

    base = notebook_base_url()
    name = _notebook_name(briefing)
    focus = ""
    raw = briefing.raw_response if isinstance(briefing.raw_response, dict) else {}
    if isinstance(raw, dict):
        focus = str(raw.get("focus") or (raw.get("pipeline") or {}).get("focus") or "")
    description = (
        f"Tự động từ NewsCrawler báo cáo #{briefing.pk}. "
        f"Chủ đề: {focus or briefing.title}. "
        f"{len(sources)} nguồn link."
    )[:500]

    timeout = float(getattr(settings, "NOTEBOOK_EXPORT_TIMEOUT_SEC", 60) or 60)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        try:
            nb_resp = client.post(
                f"{base}/api/notebooks",
                json={"name": name, "description": description},
            )
        except httpx.HTTPError as exc:
            raise NotebookExportError(f"Không kết nối được Notebook AI: {exc}") from exc
        if nb_resp.status_code >= 400:
            raise NotebookExportError(
                f"Tạo notebook thất bại HTTP {nb_resp.status_code}: "
                f"{nb_resp.text[:200]}"
            )
        notebook = nb_resp.json() if nb_resp.content else {}
        notebook_id = str(notebook.get("id") or "")
        if not notebook_id:
            raise NotebookExportError("Notebook AI không trả về id")

        added: list[dict[str, str]] = []
        errors: list[str] = []

        # Include the briefing itself as a text source for chat context.
        report_body = (briefing.content or "").strip()
        if report_body:
            try:
                tr = client.post(
                    f"{base}/api/sources/json",
                    json={
                        "type": "text",
                        "title": f"Báo cáo NewsCrawler: {briefing.title}"[:200],
                        "content": report_body[:120_000],
                        "notebooks": [notebook_id],
                        "embed": True,
                        "async_processing": True,
                    },
                )
                if tr.status_code < 400:
                    data = tr.json() if tr.content else {}
                    added.append(
                        {
                            "title": "Báo cáo chi tiết (text)",
                            "url": "",
                            "source_id": str(data.get("id") or ""),
                            "kind": "report",
                        }
                    )
                else:
                    errors.append(f"text report HTTP {tr.status_code}")
            except httpx.HTTPError as exc:
                errors.append(f"text report: {exc}")

        for src in sources:
            url = src["url"]
            title = src.get("title") or url
            # Skip non-http(s) / junk
            try:
                host = urlparse(url).hostname or ""
            except Exception:  # noqa: BLE001
                host = ""
            if not host:
                continue
            try:
                resp = client.post(
                    f"{base}/api/sources/json",
                    json={
                        "type": "link",
                        "url": url,
                        "title": title[:200],
                        "notebooks": [notebook_id],
                        "embed": True,
                        "async_processing": True,
                    },
                )
            except httpx.HTTPError as exc:
                errors.append(f"{title[:40]}: {exc}")
                continue
            if resp.status_code >= 400:
                errors.append(f"{title[:40]}: HTTP {resp.status_code}")
                continue
            data = resp.json() if resp.content else {}
            added.append(
                {
                    "title": title[:200],
                    "url": url,
                    "source_id": str(data.get("id") or ""),
                    "kind": src.get("kind") or "web",
                }
            )

    if not added:
        raise NotebookExportError(
            "Không thêm được nguồn nào vào Notebook: "
            + ("; ".join(errors[:3]) or "unknown")
        )

    public = notebook_public_url()
    # Same-origin SPA route (not Open Notebook Next.js /notebooks/...)
    open_url = f"{public}/notebook-ai?notebook={notebook_id}"
    meta = {
        "notebook_id": notebook_id,
        "notebook_name": name,
        "open_url": open_url,
        "sources_added": len(added),
        "sources_errors": errors[:10],
        "exported_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        raw = dict(briefing.raw_response or {})
        exports = list(raw.get("notebook_exports") or [])
        exports.append(meta)
        raw["notebook_exports"] = exports[-10:]
        raw["last_notebook_export"] = meta
        briefing.raw_response = raw
        briefing.save(update_fields=["raw_response", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist notebook export meta: %s", exc)

    return {
        "ok": True,
        "notebook_id": notebook_id,
        "notebook_name": name,
        "open_url": open_url,
        "sources_queued": len(added),
        "sources_total": len(sources),
        "added": added[:40],
        "errors": errors[:10],
    }


def extract_last30days_sources(research) -> list[dict[str, str]]:
    """Collect unique https URLs from last30days findings (lookback-filtered)."""
    from apps.integrations.last30days.service import findings_within_lookback_q

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(title: str, url: str, kind: str = "web") -> None:
        u = (url or "").strip().rstrip(".,;:)")
        if not u.startswith("http"):
            return
        key = u.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "title": (title or u)[:300],
                "url": u[:2048],
                "kind": kind or "web",
            }
        )

    findings = (
        research.findings.filter(findings_within_lookback_q(research))
        .exclude(url="")
        .order_by("-id")
    )
    for finding in findings.iterator(chunk_size=100):
        title = (getattr(finding, "title_vi", None) or finding.title or "").strip()
        source = (finding.source or "web").strip() or "web"
        add(title, finding.url or "", source)

    return out


def _last30days_notebook_name(research) -> str:
    title = " ".join(str(research.topic or "Xu hướng").split())[:80]
    tz_name = getattr(settings, "TIME_ZONE", "Asia/Ho_Chi_Minh") or "Asia/Ho_Chi_Minh"
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        now = datetime.utcnow()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    return f"Xu hướng · {title} · {stamp}"


def export_last30days_to_notebook(research) -> dict[str, Any]:
    """
    Create a NEW Open Notebook notebook and queue all research finding URLs.

    Always creates a fresh notebook (same pattern as briefing → Notebook AI).
    """
    sources = extract_last30days_sources(research)
    brief = (research.brief_markdown or "").strip()
    if not sources and not brief:
        raise NotebookExportError(
            "Nghiên cứu không có nguồn URL để thêm vào Notebook"
        )

    base = notebook_base_url()
    name = _last30days_notebook_name(research)
    description = (
        f"Tự động từ NewsCrawler Xu hướng #{research.pk}. "
        f"Chủ đề: {research.topic}. "
        f"{len(sources)} nguồn link."
    )[:500]

    timeout = float(getattr(settings, "NOTEBOOK_EXPORT_TIMEOUT_SEC", 60) or 60)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        try:
            nb_resp = client.post(
                f"{base}/api/notebooks",
                json={"name": name, "description": description},
            )
        except httpx.HTTPError as exc:
            raise NotebookExportError(f"Không kết nối được Notebook AI: {exc}") from exc
        if nb_resp.status_code >= 400:
            raise NotebookExportError(
                f"Tạo notebook thất bại HTTP {nb_resp.status_code}: "
                f"{nb_resp.text[:200]}"
            )
        notebook = nb_resp.json() if nb_resp.content else {}
        notebook_id = str(notebook.get("id") or "")
        if not notebook_id:
            raise NotebookExportError("Notebook AI không trả về id")

        added: list[dict[str, str]] = []
        errors: list[str] = []

        if brief:
            try:
                tr = client.post(
                    f"{base}/api/sources/json",
                    json={
                        "type": "text",
                        "title": f"Brief Xu hướng: {research.topic}"[:200],
                        "content": brief[:120_000],
                        "notebooks": [notebook_id],
                        "embed": True,
                        "async_processing": True,
                    },
                )
                if tr.status_code < 400:
                    data = tr.json() if tr.content else {}
                    added.append(
                        {
                            "title": "Brief Xu hướng (text)",
                            "url": "",
                            "source_id": str(data.get("id") or ""),
                            "kind": "brief",
                        }
                    )
                else:
                    errors.append(f"text brief HTTP {tr.status_code}")
            except httpx.HTTPError as exc:
                errors.append(f"text brief: {exc}")

        for src in sources:
            url = src["url"]
            title = src.get("title") or url
            try:
                host = urlparse(url).hostname or ""
            except Exception:  # noqa: BLE001
                host = ""
            if not host:
                continue
            try:
                resp = client.post(
                    f"{base}/api/sources/json",
                    json={
                        "type": "link",
                        "url": url,
                        "title": title[:200],
                        "notebooks": [notebook_id],
                        "embed": True,
                        "async_processing": True,
                    },
                )
            except httpx.HTTPError as exc:
                errors.append(f"{title[:40]}: {exc}")
                continue
            if resp.status_code >= 400:
                errors.append(f"{title[:40]}: HTTP {resp.status_code}")
                continue
            data = resp.json() if resp.content else {}
            added.append(
                {
                    "title": title[:200],
                    "url": url,
                    "source_id": str(data.get("id") or ""),
                    "kind": src.get("kind") or "web",
                }
            )

    if not added:
        raise NotebookExportError(
            "Không thêm được nguồn nào vào Notebook: "
            + ("; ".join(errors[:3]) or "unknown")
        )

    public = notebook_public_url()
    open_url = f"{public}/notebook-ai?notebook={notebook_id}"
    meta = {
        "notebook_id": notebook_id,
        "notebook_name": name,
        "open_url": open_url,
        "sources_added": len(added),
        "sources_errors": errors[:10],
        "exported_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        raw = dict(research.raw_report or {})
        exports = list(raw.get("notebook_exports") or [])
        exports.append(meta)
        raw["notebook_exports"] = exports[-10:]
        raw["last_notebook_export"] = meta
        research.raw_report = raw
        research.save(update_fields=["raw_report", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist last30days notebook export meta: %s", exc)

    return {
        "ok": True,
        "notebook_id": notebook_id,
        "notebook_name": name,
        "open_url": open_url,
        "sources_queued": len(added),
        "sources_total": len(sources),
        "added": added[:40],
        "errors": errors[:10],
    }
