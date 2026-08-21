"""Backfill site-* tags on existing RSS, Tor, sitemap, and Searx Wire items."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.intel.models import Tag, Threat
from apps.workers.services import website_tag_slug


class Command(BaseCommand):
    help = "Add a site-* source tag to existing Wire threats."

    def handle(self, *args, **options):
        scanned = 0
        tagged = 0
        skipped = 0
        queryset = Threat.objects.prefetch_related("tags").order_by("id")

        for threat in queryset.iterator(chunk_size=500):
            scanned += 1
            if any(tag.slug.startswith("site-") for tag in threat.tags.all()):
                skipped += 1
                continue
            payload = (
                dict(threat.raw_payload)
                if isinstance(threat.raw_payload, dict)
                else {}
            )
            if threat.source == Threat.Source.CVE_FEED:
                item = {"feed": "cve-circl-lu"}
            elif threat.source == Threat.Source.RANSOMWARE:
                item = {"feed": "ransomware-live"}
            elif threat.source == Threat.Source.OSINT:
                item = {"feed": "osint"}
            else:
                item = {
                    **payload,
                    "link": threat.source_url,
                    "url": threat.source_url,
                }
            slug = website_tag_slug(item)
            if not slug:
                skipped += 1
                continue
            tag, _ = Tag.objects.get_or_create(
                slug=slug,
                defaults={"name": slug.replace("-", " ").title()},
            )
            threat.tags.add(tag)
            tagged += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Website tag backfill complete · scanned={scanned} "
                f"tagged={tagged} skipped={skipped}"
            )
        )
