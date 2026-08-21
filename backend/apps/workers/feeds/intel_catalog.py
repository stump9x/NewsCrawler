"""Defense-news feed catalog helper (legacy cyber-breach catalog removed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("intel_catalog.json")


def load_intel_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    """Load optional extra feed rows for seeding. Empty by design for this project."""
    target = path or CATALOG_PATH
    if not target.exists():
        return []
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("intel catalog must be a JSON list")
    return data
