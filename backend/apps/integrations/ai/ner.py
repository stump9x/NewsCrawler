"""Lightweight entity extraction without shipping heavy ML weights.

Uses regex for IP/domain/hash/CVE. Optional Hugging Face NER can be
wired later via HUGGINGFACE_API_TOKEN + HUGGINGFACE_NER_MODEL.
"""

from __future__ import annotations

import re
from typing import Any

IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)
DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b",
    re.I,
)
MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def extract_entities(text: str) -> dict[str, list[str]]:
    text = text or ""
    found = {
        "ipv4": sorted(set(IPV4_RE.findall(text))),
        "domain": sorted({d.lower() for d in DOMAIN_RE.findall(text)}),
        "md5": sorted({h.lower() for h in MD5_RE.findall(text)}),
        "sha1": sorted({h.lower() for h in SHA1_RE.findall(text)}),
        "sha256": sorted({h.lower() for h in SHA256_RE.findall(text)}),
        "cve": sorted({c.upper() for c in CVE_RE.findall(text)}),
        "email": sorted(set(EMAIL_RE.findall(text))),
    }
    found["domain"] = [
        d
        for d in found["domain"]
        if "." in d and not any(d.endswith(ext) for ext in (".png", ".jpg", ".gif"))
    ]
    return {k: v for k, v in found.items() if v}


def flatten_entities(entities: dict[str, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ioc_type, values in entities.items():
        for value in values:
            rows.append({"ioc_type": ioc_type, "value": value})
    return rows
