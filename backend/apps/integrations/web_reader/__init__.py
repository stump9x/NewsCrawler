"""Open-web page reading helpers (Jina + fallback) for Searx leak enrichment."""

from apps.integrations.web_reader.reader import (
    ReadResult,
    doctor_web_reader,
    read_url,
    web_reader_enabled,
)

__all__ = [
    "ReadResult",
    "doctor_web_reader",
    "read_url",
    "web_reader_enabled",
]
