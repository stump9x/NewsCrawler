"""HTTP client for the Go OSINT microservice."""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings


class OSINTClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _base_url() -> str:
    return (getattr(settings, "OSINT_SERVICE_URL", None) or "http://localhost:8080").rstrip(
        "/"
    )


def list_sites(timeout: float = 15.0) -> dict[str, Any]:
    url = f"{_base_url()}/api/v1/sites"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise OSINTClientError(f"OSINT service unreachable: {exc}") from exc
    if response.status_code >= 400:
        raise OSINTClientError(
            "OSINT sites request failed",
            status_code=response.status_code,
            payload=_safe_json(response),
        )
    return response.json()


def scan_username(
    *,
    username: str,
    sites: list[str] | None = None,
    timeout_seconds: int = 45,
    only_found: bool = False,
    http_timeout: float | None = None,
) -> dict[str, Any]:
    url = f"{_base_url()}/api/v1/scan"
    payload = {
        "username": username,
        "sites": sites or [],
        "timeout_seconds": timeout_seconds,
        "only_found": only_found,
    }
    # Client wait slightly longer than scan budget
    wait = http_timeout if http_timeout is not None else float(timeout_seconds) + 15.0
    try:
        with httpx.Client(timeout=wait) as client:
            response = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise OSINTClientError(f"OSINT scan failed: {exc}") from exc

    data = _safe_json(response)
    if response.status_code >= 400:
        raise OSINTClientError(
            (data or {}).get("message") or "OSINT scan rejected",
            status_code=response.status_code,
            payload=data,
        )
    return data


def health(timeout: float = 5.0) -> dict[str, Any]:
    url = f"{_base_url()}/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise OSINTClientError(f"OSINT health failed: {exc}") from exc


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:500]}
