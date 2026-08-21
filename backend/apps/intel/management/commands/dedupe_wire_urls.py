"""Normalize Wire source URLs and delete duplicate article links."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.intel.models import Threat
from apps.intel.wire_urls import dedupe_wire_threats_by_url


class Command(BaseCommand):
    help = (
        "Normalize Threat source_url values for Wire/NEWS and delete "
        "duplicates that share the same normalized URL."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without rewriting or deleting.",
        )
        parser.add_argument(
            "--include-wire-sources",
            action="store_true",
            help=(
                "Also dedupe cert/ransomware/x (same normalize key). "
                "Default is news only."
            ),
        )

    def handle(self, *args, **options):
        sources = (Threat.Source.NEWS,)
        if options["include_wire_sources"]:
            sources = (
                Threat.Source.NEWS,
                Threat.Source.CERT,
                Threat.Source.RANSOMWARE,
                Threat.Source.X,
            )
        result = dedupe_wire_threats_by_url(
            dry_run=bool(options["dry_run"]),
            sources=sources,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "duplicate_groups={duplicate_groups} rows_deleted={rows_deleted} "
                "urls_normalized={urls_normalized} remaining_with_url={remaining_with_url} "
                "dry_run={dry_run}".format(**result)
            )
        )
        self.stdout.write(f"keep_rule: {result['keep_rule']}")
        self.stdout.write(f"sources: {', '.join(result['sources'])}")
