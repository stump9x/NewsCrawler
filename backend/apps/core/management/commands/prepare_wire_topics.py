"""Apply the current editorial rollout once before the API becomes healthy."""
from pathlib import Path
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.models import WireTopicRollout
from apps.core.wire_filter_policy import clear_wire_filter_prompt_cache
from apps.core.wire_topics import POLICY_VERSION


class Command(BaseCommand):
    help = "Automatically back up and apply a Wire topic upgrade once per database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backup-dir", default=str(Path(__file__).resolve().parents[4] / ".wire-backups"),
        )

    def handle(self, *args, **options):
        try:
            # A DB marker survives container recreation, but correctly disappears
            # if the database itself is restored to a pre-upgrade backup. The
            # unique key and row lock serialize concurrent backend startups.
            with transaction.atomic():
                rollout, _ = WireTopicRollout.objects.select_for_update().get_or_create(
                    version=POLICY_VERSION,
                )
                if rollout.completed_at is not None:
                    self.stdout.write(f"Wire topics {POLICY_VERSION}: already prepared.")
                    return

                directory = Path(options["backup_dir"]).resolve()
                directory.mkdir(parents=True, exist_ok=True)
                stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
                backup = directory / f"wire-{stamp}-{uuid4().hex[:8]}.jsonl"
                self.stdout.write(f"Preparing Wire topics; backup: {backup}")
                call_command(
                    "reclassify_wire_topics", apply=True, update_prompt=True,
                    backup=str(backup), sample=3, stdout=self.stdout,
                )
                rollout.backup_path = str(backup)
                rollout.completed_at = timezone.now()
                rollout.save(update_fields=["backup_path", "completed_at"])
                # Any failure rolls back both article/policy changes and the
                # marker. The next startup retries with a new backup file.
                self.stdout.write(self.style.SUCCESS(f"Wire topics {POLICY_VERSION}: ready."))
        finally:
            clear_wire_filter_prompt_cache()
