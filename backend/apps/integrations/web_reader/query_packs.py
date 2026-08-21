"""Keyword → multi-query packs for open-web leak discovery (Searx / Exa)."""

from __future__ import annotations

from django.conf import settings


def query_packs_enabled() -> bool:
    return bool(getattr(settings, "SEARX_QUERY_PACKS", True))


def build_leak_query_pack(keyword: str, *, max_queries: int | None = None) -> list[str]:
    """
    Expand one Watch Rule keyword into a small set of discovery queries.

    Includes Twitter/X + Reddit site dorks (no cookies) and secret/filetype hints.
    Caps query count to protect Searx/Exa rate budgets.
    """
    kw = " ".join((keyword or "").split()).strip()
    if not kw:
        return []
    cap = max(
        1,
        min(
            int(
                max_queries
                if max_queries is not None
                else getattr(settings, "SEARX_QUERY_PACK_SIZE", 4) or 4
            ),
            8,
        ),
    )
    quoted = kw if (kw.startswith('"') and kw.endswith('"')) else f'"{kw}"'
    pack = [
        quoted,
        f"{quoted} (password OR DATABASE_URL OR SECRET_KEY OR api_key)",
        f"{quoted} (site:reddit.com OR site:x.com OR site:twitter.com)",
        (
            f"{quoted} (filetype:pdf OR filetype:xlsx OR site:pastebin.com"
            f" OR site:stackoverflow.com OR site:npmjs.com)"
        ),
    ]
    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for query in pack:
        if query not in seen:
            seen.add(query)
            out.append(query)
        if len(out) >= cap:
            break
    return out
