"""Outbound URL safety checks (SSRF hardening for feed fetch / worker egress)."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from django.conf import settings


class UnsafeURLError(ValueError):
    """Raised when a URL must not be fetched from worker/backend context."""


_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.google",
    }
)

# Tor v3 onion hostnames are 56 base32 chars + ".onion".
_ONION_HOST_RE = re.compile(r"^[a-z2-7]{56}\.onion$", re.IGNORECASE)


def is_onion_hostname(host: str | None) -> bool:
    name = (host or "").strip().lower().rstrip(".")
    return bool(_ONION_HOST_RE.match(name))


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_onion_http_url(url: str, *, allow_http: bool = True) -> str:
    """
    Validate .onion URLs for Tor-only fetches.

    Skips DNS resolution (onion names are not in public DNS). Requires TOR path.
    """
    raw = (url or "").strip()
    if not raw or len(raw) > 2048:
        raise UnsafeURLError("URL is empty or too long")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    allowed = {"https", "http"} if allow_http else {"https"}
    if scheme not in allowed:
        raise UnsafeURLError(f"URL scheme must be one of {sorted(allowed)}")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL must not contain userinfo")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not is_onion_hostname(host):
        raise UnsafeURLError("Onion URL hostname must be a valid .onion address")
    return raw


def validate_public_http_url(url: str, *, allow_http: bool = True) -> str:
    """
    Validate analyst-supplied URLs before server-side fetch.

    - scheme http/https only (http allowed for many CERT RSS feeds)
    - no userinfo (user:pass@host)
    - hostname required; blocks localhost / metadata names / .onion
    - resolves DNS and rejects private/link-local/reserved addresses
    """
    raw = (url or "").strip()
    if not raw or len(raw) > 2048:
        raise UnsafeURLError("URL is empty or too long")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    allowed = {"https", "http"} if allow_http else {"https"}
    if scheme not in allowed:
        raise UnsafeURLError(f"URL scheme must be one of {sorted(allowed)}")

    if parsed.username or parsed.password:
        raise UnsafeURLError("URL must not contain userinfo")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL hostname is required")

    if is_onion_hostname(host):
        raise UnsafeURLError("Onion URLs require Tor fetch path (via_tor=True)")

    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise UnsafeURLError("Hostname is not allowed")

    # Literal IP in hostname
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise UnsafeURLError("IP address is not publicly routable")
    except ValueError:
        # Hostname — resolve A/AAAA
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"DNS resolution failed: {exc}") from exc
        if not infos:
            raise UnsafeURLError("DNS resolution returned no addresses")
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _is_blocked_ip(ip):
                raise UnsafeURLError(
                    f"Hostname resolves to a non-public address ({addr})"
                )

    return raw


def validate_fetch_http_url(
    url: str, *, via_tor: bool = False, allow_http: bool = True
) -> str:
    """Route URL validation for clearnet vs Tor-gated fetches."""
    host = (urlparse(url).hostname or "").strip().lower().rstrip(".")
    if is_onion_hostname(host) or via_tor:
        if not bool(getattr(settings, "TOR_ENABLED", False)):
            raise UnsafeURLError("Tor is disabled (set TOR_ENABLED=true)")
        if is_onion_hostname(host):
            return validate_onion_http_url(url, allow_http=allow_http)
        # Clearnet URL forced through Tor still needs public SSRF checks.
        return validate_public_http_url(url, allow_http=allow_http)
    return validate_public_http_url(url, allow_http=allow_http)
