"""Backfill cover images (og:image) for Wire items missing one."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.workers.image_enrich import backfill_wire_images


class Command(BaseCommand):
    help = (
        "Fetch social-preview images (og:image/twitter:image) for wire items "
        "whose RSS feed shipped no image. Reads only the article <head>."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=80,
            help="Max article pages to fetch this run (default: 80).",
        )
        parser.add_argument(
            "--retry-errors",
            action="store_true",
            help="Re-attempt items previously marked as fetch errors.",
        )

    def handle(self, *args, **options):
        stats = backfill_wire_images(
            limit=options["limit"], retry_errors=options["retry_errors"]
        )
        self.stdout.write(self.style.SUCCESS(f"image backfill: {stats}"))
