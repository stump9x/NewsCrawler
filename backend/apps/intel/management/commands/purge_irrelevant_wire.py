"""Delete Wire threats that no longer pass the current relevance filter."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.intel.models import Threat
from apps.workers.services import is_wire_relevant


def threat_as_relevance_item(threat: Threat) -> dict[str, Any]:
    """Rebuild the ingest-shaped dict used by ``is_wire_relevant``."""
    payload = threat.raw_payload if isinstance(threat.raw_payload, dict) else {}
    if threat.source == Threat.Source.RANSOMWARE:
        default_category = "ransomware"
    elif threat.source == Threat.Source.CERT:
        default_category = "cert"
    else:
        default_category = "news"
    return {
        "title": threat.title,
        "title_vi": getattr(threat, "title_vi", None) or payload.get("title_vi") or "",
        "summary": threat.summary
        or payload.get("summary")
        or payload.get("description")
        or "",
        "description": payload.get("description") or "",
        "content": payload.get("content") or "",
        "category": payload.get("category") or default_category,
        "country": payload.get("country") or "",
        "country_code": payload.get("country_code") or "",
        "feed": payload.get("feed") or "",
        "feed_url": payload.get("feed_url") or "",
        "link": threat.source_url or payload.get("link") or payload.get("url") or "",
        "url": threat.source_url or payload.get("url") or payload.get("link") or "",
        "source_url": threat.source_url or "",
        "discovery": payload.get("discovery") or "",
        "forum_claim": payload.get("forum_claim"),
        "alleged_claim": payload.get("alleged_claim"),
    }


class Command(BaseCommand):
    help = (
        "Delete Wire threats that fail the current Indo-Pacific / priority-country "
        "relevance rules (e.g. UK/Estonia-only, Canada-only)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts and sample titles without deleting.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max deletions (0 = no limit).",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=15,
            help="How many sample titles to print.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = max(0, int(options["limit"] or 0))
        sample_n = max(0, int(options["sample"] or 0))

        qs = Threat.objects.filter(wire_relevant=True).only(
            "id",
            "title",
            "summary",
            "source",
            "raw_payload",
        )
        scanned = 0
        delete_ids: list[int] = []
        samples: list[str] = []

        for threat in qs.iterator(chunk_size=500):
            scanned += 1
            if is_wire_relevant(threat_as_relevance_item(threat)):
                continue
            delete_ids.append(threat.id)
            if len(samples) < sample_n:
                samples.append(f"#{threat.id} {threat.title[:120]}")
            if limit and len(delete_ids) >= limit:
                break

        deleted = 0
        if delete_ids and not dry_run:
            # Chunk deletes to avoid huge IN clauses.
            for i in range(0, len(delete_ids), 500):
                chunk = delete_ids[i : i + 500]
                Threat.objects.filter(id__in=chunk).delete()
                deleted += len(chunk)
        elif dry_run:
            deleted = len(delete_ids)

        action = "would_delete" if dry_run else "deleted"
        self.stdout.write(
            self.style.SUCCESS(
                f"Irrelevant Wire purge · scanned={scanned} {action}={deleted}"
            )
        )
        for line in samples:
            self.stdout.write(f"  - {line}")
