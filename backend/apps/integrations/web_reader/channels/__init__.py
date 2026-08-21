"""Channel registry (Agent-Reach-style): primary discovery + enrich backends."""

from __future__ import annotations

from typing import Any, Callable

from django.conf import settings

from apps.integrations.searx.client import searx_configured
from apps.integrations.web_reader.exa import doctor_exa, exa_configured
from apps.integrations.web_reader.reader import doctor_web_reader, web_reader_enabled
from apps.integrations.web_reader.wigolo import doctor_wigolo, wigolo_configured


DoctorFn = Callable[[], dict[str, Any]]


def _doctor_searx() -> dict[str, Any]:
    ok = searx_configured()
    return {
        "id": "searx",
        "label": "SearxNG metasearch",
        "role": "discover",
        "ok": ok,
        "configured": ok,
        "detail": getattr(settings, "SEARXNG_ENGINES", "") or "",
    }


def _doctor_wigolo() -> dict[str, Any]:
    info = doctor_wigolo()
    return {
        "id": "wigolo",
        "label": "Wigolo local search/fetch",
        "role": "discover",
        "ok": bool(info.get("ok")),
        "configured": bool(info.get("configured")),
        "detail": str(info.get("detail") or "Set WIGOLO_URL to enable"),
    }


def _doctor_exa() -> dict[str, Any]:
    info = doctor_exa()
    return {
        "id": "exa",
        "label": "Exa semantic search",
        "role": "discover",
        "ok": bool(info.get("ok")),
        "configured": bool(info.get("configured")),
        "detail": str(info.get("detail") or "Set EXA_API_KEY to enable"),
    }


def _doctor_x() -> dict[str, Any]:
    from apps.integrations.web_reader.channels.x_twitter import doctor_x_twitter

    return doctor_x_twitter()


def _doctor_reddit_search() -> dict[str, Any]:
    from apps.integrations.web_reader.channels.reddit import doctor_reddit_search

    return doctor_reddit_search()


def _doctor_reddit() -> dict[str, Any]:
    from apps.integrations.web_reader.channels.reddit import doctor_reddit

    return doctor_reddit()


def _doctor_paste() -> dict[str, Any]:
    from apps.integrations.web_reader.channels.paste import doctor_paste

    return doctor_paste()


def _doctor_reader() -> dict[str, Any]:
    info = doctor_web_reader()
    return {
        "id": "web_reader",
        "label": "Web reader (Jina/httpx)",
        "role": "enrich",
        "ok": bool(info.get("ok")),
        "configured": bool(info.get("enabled")),
        "detail": f"backend={info.get('backend')}",
    }


_REGISTRY: list[DoctorFn] = [
    _doctor_searx,
    _doctor_wigolo,
    _doctor_exa,
    _doctor_x,
    _doctor_reddit_search,
    _doctor_reddit,
    _doctor_paste,
    _doctor_reader,
]


def channel_doctor() -> dict[str, Any]:
    """Return health for every open-web channel (UI / status API)."""
    channels = [fn() for fn in _REGISTRY]
    return {
        "ok": any(c.get("ok") for c in channels if c.get("role") == "discover"),
        "channels": channels,
        "query_packs": bool(getattr(settings, "SEARX_QUERY_PACKS", True)),
        "enrich": bool(getattr(settings, "SEARX_LEAK_ENRICH", True))
        and web_reader_enabled(),
        "exa_configured": exa_configured(),
        "wigolo_configured": wigolo_configured(),
        "searx_configured": searx_configured(),
    }
