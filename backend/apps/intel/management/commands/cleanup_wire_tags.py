"""Remove noisy generic tags from The Wire."""

from django.core.management.base import BaseCommand

from apps.intel.models import Tag


class Command(BaseCommand):
    help = 'Delete the generic "news" and "rss" tags.'

    def handle(self, *args, **options):
        queryset = Tag.objects.filter(slug__in=("news", "rss"))
        tag_count = queryset.count()
        queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Removed generic Wire tags · tags={tag_count}")
        )
