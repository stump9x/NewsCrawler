"""Backfill Vietnamese Wire titles via rules + Ollama."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.integrations.ai.translate import translate_threats


class Command(BaseCommand):
    help = "Translate pending Wire titles (rules first, then Google Translate)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Max threats to process in this run (default 50).",
        )
        parser.add_argument(
            "--ids",
            type=str,
            default="",
            help="Comma-separated threat IDs to translate.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-translate even when a previous title_vi exists.",
        )

    def handle(self, *args, **options):
        limit = max(1, int(options["limit"] or 50))
        ids_raw = str(options.get("ids") or "").strip()
        threat_ids = None
        if ids_raw:
            threat_ids = [int(part) for part in ids_raw.split(",") if part.strip().isdigit()]
        stats = translate_threats(
            threat_ids, limit=limit, force=bool(options.get("force"))
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Title translate · "
                + " ".join(f"{key}={value}" for key, value in stats.items())
            )
        )
