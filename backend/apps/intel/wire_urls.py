"""Wire/NEWS article URL normalization and duplicate cleanup."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from apps.intel.models import Threat

# Tracking / analytics params only — keep content-bearing query strings.
_TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "ref_src",
        "ref_url",
        "spm",
        "ncid",
    }
)


def normalize_wire_url(url: str) -> str:
    """Canonical article URL for Wire dedupe.

    - lowercases scheme; maps http→https for matching/storage
    - strips leading www.
    - drops fragment
    - strips trailing slash (except bare ``/``)
    - drops common tracking query params (utm_*, fbclid, gclid, …)
    - keeps other query params (article ids, tags, etc.)
    """
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return text[:2048]
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return text[:2048]

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"

    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")

    query_pairs: list[tuple[str, str]] = []
    if parsed.query:
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            folded = key.casefold()
            if folded.startswith("utm_") or folded in _TRACKING_QUERY_KEYS:
                continue
            query_pairs.append((key, value))

    rebuilt = urlunparse(
        (
            "https",
            netloc,
            path,
            "",
            urlencode(query_pairs) if query_pairs else "",
            "",
        )
    )
    return rebuilt[:2048]


def _title_vi_rank(threat: Threat) -> int:
    status = (threat.title_vi_status or "").strip()
    has_vi = bool((threat.title_vi or "").strip())
    if status in {
        Threat.TitleViStatus.OK,
        Threat.TitleViStatus.RULE,
        Threat.TitleViStatus.SKIPPED,
    } and (has_vi or status == Threat.TitleViStatus.SKIPPED):
        return 3
    if has_vi:
        return 2
    if status == Threat.TitleViStatus.PENDING:
        return 1
    return 0


def _status_rank(threat: Threat) -> int:
    # Prefer actionable / open wire rows over closed or false positives.
    order = {
        Threat.Status.CONFIRMED: 5,
        Threat.Status.TRIAGED: 4,
        Threat.Status.NEW: 3,
        Threat.Status.CLOSED: 1,
        Threat.Status.FALSE_POSITIVE: 0,
    }
    return order.get(threat.status, 2)


def wire_threat_keep_rank(threat: Threat) -> tuple:
    """Higher is better. Tie-break: lowest id (oldest row) via negated id last."""
    published_ts = threat.published_at.timestamp() if threat.published_at else 0.0
    return (
        _title_vi_rank(threat),
        _status_rank(threat),
        1 if threat.wire_relevant else 0,
        int(threat.wire_priority or 0),
        published_ts,
        -int(threat.pk),
    )


def dedupe_wire_threats_by_url(
    *,
    dry_run: bool = False,
    sources: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Normalize Wire ``source_url`` values and delete normalized duplicates.

    Keep rule (documented): prefer good ``title_vi`` / title_vi_status
    (ok/rule/skipped), then better status, wire_relevant, higher wire_priority,
    newest ``published_at``, lowest id as final tie-break.
    """
    source_filter = sources or (Threat.Source.NEWS,)
    qs = (
        Threat.objects.filter(source__in=source_filter)
        .exclude(source_url="")
        .only(
            "id",
            "source_url",
            "title_vi",
            "title_vi_status",
            "status",
            "published_at",
            "wire_relevant",
            "wire_priority",
        )
    )

    by_norm: dict[str, list[Threat]] = {}
    rewrite_ids: list[tuple[int, str]] = []
    for threat in qs.iterator(chunk_size=500):
        raw = threat.source_url or ""
        norm = normalize_wire_url(raw)
        if not norm:
            continue
        if norm != raw:
            rewrite_ids.append((threat.id, norm))
        by_norm.setdefault(norm, []).append(threat)

    delete_ids: list[int] = []
    duplicate_groups = 0
    for norm, group in by_norm.items():
        if len(group) < 2:
            continue
        duplicate_groups += 1
        ranked = sorted(group, key=wire_threat_keep_rank, reverse=True)
        keep = ranked[0]
        for extra in ranked[1:]:
            delete_ids.append(extra.id)
        # Ensure keeper stores the normalized URL.
        if (keep.source_url or "") != norm:
            rewrite_ids.append((keep.id, norm))

    # Drop rewrites for rows we are about to delete.
    delete_set = set(delete_ids)
    rewrite_ids = [(pk, url) for pk, url in rewrite_ids if pk not in delete_set]
    # De-dupe rewrite list (keeper may appear twice).
    rewrite_map = {pk: url for pk, url in rewrite_ids}

    deleted = 0
    rewritten = 0
    if not dry_run:
        if delete_ids:
            deleted, _ = Threat.objects.filter(id__in=delete_ids).delete()
            # `.delete()` returns total objects incl. M2M through rows; count threats.
            deleted = len(delete_ids)
        for pk, url in rewrite_map.items():
            updated = Threat.objects.filter(id=pk).exclude(source_url=url).update(
                source_url=url
            )
            rewritten += updated
    else:
        deleted = len(delete_ids)
        rewritten = len(rewrite_map)

    remaining = (
        Threat.objects.filter(source__in=source_filter)
        .exclude(source_url="")
        .count()
    )
    if dry_run:
        remaining = remaining  # unchanged in DB

    return {
        "sources": list(source_filter),
        "duplicate_groups": duplicate_groups,
        "rows_deleted": deleted,
        "urls_normalized": rewritten if not dry_run else len(rewrite_map),
        "remaining_with_url": remaining if not dry_run else remaining - deleted,
        "dry_run": dry_run,
        "keep_rule": (
            "prefer title_vi ok/rule/skipped (+non-empty title_vi except skipped), "
            "then status confirmed>triaged>new>closed>false_positive, "
            "wire_relevant, higher wire_priority, newest published_at, lowest id"
        ),
    }


def find_threat_by_normalized_url(url: str) -> Threat | None:
    """Exact match on normalized ``source_url`` (after absolutize+normalize)."""
    key = normalize_wire_url(url)
    if not key:
        return None
    return (
        Threat.objects.filter(source_url=key)
        .only("id", "raw_payload", "source_url")
        .first()
    )
