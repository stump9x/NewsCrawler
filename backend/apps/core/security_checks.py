"""Startup security guards (fail closed in non-debug deployments)."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_INSECURE_SECRET_MARKERS = (
    "insecure-dev-only-change-me",
    "change-me",
    "django-insecure",
)


def assert_secure_settings() -> None:
    """Raise when production-like settings keep placeholder secrets."""
    if getattr(settings, "DEBUG", True):
        return

    secret = str(getattr(settings, "SECRET_KEY", "") or "")
    lowered = secret.lower()
    if len(secret) < 32 or any(m in lowered for m in _INSECURE_SECRET_MARKERS):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is missing, too short, or still a placeholder. "
            "Refuse to start with DEBUG=False."
        )

    # Postgres password is only checked when not using SQLite
    engine = (
        settings.DATABASES.get("default", {}).get("ENGINE", "")
        if getattr(settings, "DATABASES", None)
        else ""
    )
    if "sqlite" not in engine:
        pwd = str(settings.DATABASES["default"].get("PASSWORD") or "")
        if not pwd or pwd in {"change-me-db-password", "password", "postgres"}:
            raise ImproperlyConfigured(
                "POSTGRES_PASSWORD is missing or still a placeholder under DEBUG=False."
            )

    redis_pwd = str(getattr(settings, "REDIS_PASSWORD", "") or "")
    broker = str(getattr(settings, "CELERY_BROKER_URL", "") or "")
    if not redis_pwd and "@" not in broker.split("://", 1)[-1]:
        raise ImproperlyConfigured(
            "REDIS_PASSWORD (or password embedded in CELERY_BROKER_URL) is required when DEBUG=False."
        )
