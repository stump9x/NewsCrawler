"""Conservatively remove single-country, locally-contained weapon stories."""

from __future__ import annotations

from pathlib import Path

from django.core import serializers
from django.core.management.base import BaseCommand

from apps.intel.management.commands.purge_irrelevant_wire import (
    threat_as_relevance_item,
)
from apps.intel.filters import ThreatFilter
from apps.intel.models import Threat
from apps.workers.services import is_local_single_country_weapon_news


class Command(BaseCommand):
    help = (
        "Find or delete weapon stories that name exactly one country and have "
        "no detected regional, cross-border, alliance, transfer, or conflict impact."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually delete matches; without this flag the command is a dry run.",
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--sample", type=int, default=30)
        parser.add_argument(
            "--export",
            default="",
            help="Write matched Threat rows, including tag IDs, as a JSON fixture before deletion.",
        )

    def handle(self, *args, **options):
        execute = bool(options["execute"])
        limit = max(0, int(options["limit"] or 0))
        sample_n = max(0, int(options["sample"] or 0))
        export_path = str(options["export"] or "").strip()

        # Operate on exactly the rows currently eligible for Trạm tin tức,
        # not hidden/untranslated wire_relevant rows that users cannot see.
        queryset = ThreatFilter(
            {"wire_feed": "true"}, queryset=Threat.objects.all()
        ).qs.only(
            "id", "title", "title_vi", "summary", "source", "source_url", "raw_payload"
        )
        scanned = 0
        match_ids: list[int] = []
        samples: list[str] = []
        for threat in queryset.iterator(chunk_size=500):
            scanned += 1
            if not is_local_single_country_weapon_news(
                threat_as_relevance_item(threat)
            ):
                continue
            match_ids.append(threat.id)
            if len(samples) < sample_n:
                samples.append(f"#{threat.id} {threat.title[:150]}")
            if limit and len(match_ids) >= limit:
                break

        if export_path and match_ids:
            target = Path(export_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            rows = Threat.objects.filter(id__in=match_ids).prefetch_related("tags")
            target.write_text(
                serializers.serialize("json", rows, indent=2),
                encoding="utf-8",
            )
            self.stdout.write(f"backup={target} rows={len(match_ids)}")

        deleted = 0
        if execute:
            for index in range(0, len(match_ids), 250):
                chunk = match_ids[index : index + 250]
                count, _ = Threat.objects.filter(id__in=chunk).delete()
                # Cascaded M2M rows are included in count; report Threat rows only.
                deleted += len(chunk)

        action = "deleted" if execute else "would_delete"
        self.stdout.write(
            self.style.SUCCESS(
                f"Local weapon Wire purge · scanned={scanned} {action}={deleted if execute else len(match_ids)}"
            )
        )
        for line in samples:
            self.stdout.write(f"  - {line}")
