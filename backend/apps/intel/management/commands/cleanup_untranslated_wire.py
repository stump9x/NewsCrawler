"""Remove unfinished news translations; optional pending backlog trim."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.integrations.ai.translate import (
    trim_pending_translation_backlog,
    unfinished_news_translation_qs,
)
from apps.intel.models import Threat


class Command(BaseCommand):
    help = (
        "Delete unfinished News/RSS title translations. "
        "Use --trim to keep at most TITLE_TRANSLATE_MAX_PENDING instead."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without deleting.",
        )
        parser.add_argument(
            "--trim",
            action="store_true",
            help=(
                "Keep at most TITLE_TRANSLATE_MAX_PENDING unfinished NEWS "
                "rows (delete the rest) instead of deleting all."
            ),
        )
        parser.add_argument(
            "--max-pending",
            type=int,
            default=None,
            help="Override TITLE_TRANSLATE_MAX_PENDING when used with --trim.",
        )

    def handle(self, *args, **options):
        news = unfinished_news_translation_qs()
        news_count = news.count()
        other_count = (
            Threat.objects.exclude(source=Threat.Source.NEWS)
            .filter(Q(title_vi="") | Q(title_vi__isnull=True))
            .exclude(title_vi_status=Threat.TitleViStatus.SKIPPED)
            .count()
        )

        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Untranslated Wire cleanup · would_delete_news={news_count} "
                    f"hidden_non_news={other_count}"
                )
            )
            return

        if options["trim"]:
            trimmed = trim_pending_translation_backlog(
                max_pending=options["max_pending"]
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "Untranslated Wire cleanup · "
                    f"deleted_news={trimmed.get('deleted', 0)} "
                    f"kept={trimmed.get('kept', 0)} "
                    f"before={trimmed.get('before', news_count)} "
                    f"hidden_non_news={other_count}"
                )
            )
            return

        deleted_count = news_count
        if news_count:
            news.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Untranslated Wire cleanup · deleted_news={deleted_count} "
                f"hidden_non_news={other_count}"
            )
        )
