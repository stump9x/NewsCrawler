from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.intel.feed_cleanup import cleanup_feed_sources


class Command(BaseCommand):
    help = (
        "Deactivate duplicate normalized RSS URLs; optionally purge "
        "(delete) feeds with last_status=error."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without writing",
        )
        parser.add_argument(
            "--purge-errors",
            action="store_true",
            help="Delete all feeds with last_status=error",
        )

    def handle(self, *args, **options):
        result = cleanup_feed_sources(
            dry_run=options["dry_run"],
            purge_errors=options["purge_errors"],
        )
        prefix = "DRY-RUN · " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Feed cleanup complete · "
                f"errors_deleted={result['errors_deleted']} "
                f"duplicates_deactivated={result['duplicates_deactivated']} "
                f"active={result['active']} total={result['total']}"
            )
        )
