"""Secret hashing / encryption helpers for compromised credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac

from django.conf import settings


def _pepper() -> bytes:
    raw = (getattr(settings, "CREDENTIAL_PEPPER", "") or "").strip()
    if not raw:
        raw = str(settings.SECRET_KEY)
    return raw.encode("utf-8")


def password_fingerprint(password: str) -> str:
    """HMAC-SHA256 fingerprint (not reversible; peppered)."""
    if not password:
        return ""
    return hmac.new(_pepper(), password.encode("utf-8"), hashlib.sha256).hexdigest()


def _fernet():
    from cryptography.fernet import Fernet

    configured = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        return Fernet(configured.encode("utf-8"))
    # Derive a stable Fernet key from SECRET_KEY (dev-friendly; set FIELD_ENCRYPTION_KEY in prod)
    digest = hashlib.sha256(str(settings.SECRET_KEY).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith("enc:"):
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"enc:{token}"


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith("enc:"):
        return stored  # legacy plaintext until re-saved
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(stored[4:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
