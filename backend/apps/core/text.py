"""Shared Vietnamese display-language normalization."""

from __future__ import annotations

import re


_FORMAL_US_NAME_RE = re.compile(r"\bHoa\s+K[ỳì]\b", flags=re.IGNORECASE)


def prefer_my_for_united_states(value: str) -> str:
    """Use the project's concise Vietnamese name for the United States."""
    return _FORMAL_US_NAME_RE.sub("Mỹ", value or "")
