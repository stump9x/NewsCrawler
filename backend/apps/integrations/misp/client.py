from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class MISPError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def misp_configured() -> bool:
    return bool(
        (getattr(settings, "MISP_URL", "") or "").strip()
        and (getattr(settings, "MISP_API_KEY", "") or "").strip()
    )


def _client() -> httpx.Client:
    base = (getattr(settings, "MISP_URL", "") or "").rstrip("/")
    key = getattr(settings, "MISP_API_KEY", "") or ""
    verify = getattr(settings, "MISP_VERIFY_SSL", True)
    if not base or not key:
        raise MISPError("MISP_URL and MISP_API_KEY must be set in environment")
    return httpx.Client(
        base_url=base,
        headers={
            "Authorization": key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        verify=verify,
        timeout=45.0,
    )


def search_attributes(limit: int = 50) -> list[dict[str, Any]]:
    with _client() as client:
        response = client.post(
            "/attributes/restSearch",
            json={"returnFormat": "json", "limit": limit, "published": True},
        )
    data = _json(response)
    if response.status_code >= 400:
        raise MISPError("MISP attribute search failed", response.status_code, data)
    response_block = data.get("response") if isinstance(data, dict) else None
    if isinstance(response_block, dict):
        attrs = response_block.get("Attribute") or []
    elif isinstance(response_block, list):
        attrs = response_block
    else:
        attrs = data.get("Attribute") if isinstance(data, dict) else []
    return attrs if isinstance(attrs, list) else []


def create_event(info: str, attributes: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "Event": {
            "info": info[:255],
            "distribution": 0,
            "threat_level_id": 2,
            "analysis": 1,
            "Attribute": attributes,
        }
    }
    with _client() as client:
        response = client.post("/events/add", json=payload)
    data = _json(response)
    if response.status_code >= 400:
        raise MISPError("MISP event create failed", response.status_code, data)
    return data if isinstance(data, dict) else {"raw": data}


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:500]}
