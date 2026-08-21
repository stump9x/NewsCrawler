"""Delete scanned documents outside the publish-date retention window."""

from django.core.management.base import BaseCommand

from apps.integrations.searx.document_scan import purge_stale_scanned_documents


class Command(BaseCommand):
    help = (
        "Purge ScannedDocument rows older than ~1 month (DOCUMENT_SCAN_MAX_AGE_DAYS). "
        "Also removes soft-accepted undated hits and blocks clearly stale URLs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age-days",
            type=int,
            default=None,
            help="Override DOCUMENT_SCAN_MAX_AGE_DAYS for this run.",
        )

    def handle(self, *args, **options):
        result = purge_stale_scanned_documents(max_age_days=options.get("max_age_days"))
        self.stdout.write(
            self.style.SUCCESS(
                f"purged scanned documents deleted={result['deleted']} "
                f"stale={result.get('deleted_stale', 0)} "
                f"off_topic={result.get('deleted_off_topic', 0)} "
                f"max_age_days={result['max_age_days']} cut={result['cut']}"
            )
        )
