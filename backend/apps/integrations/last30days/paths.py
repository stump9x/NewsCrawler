"""Locate the vendored last30days CLI."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def vendor_root() -> Path:
    # backend/apps/integrations/last30days/paths.py → backend/vendor/last30days
    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / "vendor" / "last30days"


LAST30DAYS_SCRIPT = vendor_root() / "scripts" / "last30days.py"
