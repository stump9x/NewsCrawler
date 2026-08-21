from django.core.management.base import BaseCommand

from apps.intel.models import Tag, Threat
from apps.workers.geography import detect_geography_tag_slugs


class Command(BaseCommand):
    help = "Add country/region tags to existing Wire items from their content."

    def handle(self, *args, **options):
        detected: dict[int, set[str]] = {}
        all_slugs: set[str] = set()

        threats = Threat.objects.filter(wire_relevant=True).only(
            "id", "title", "title_vi", "summary", "source", "source_url", "raw_payload"
        )
        for threat in threats.iterator(chunk_size=500):
            payload = threat.raw_payload if isinstance(threat.raw_payload, dict) else {}
            content = " ".join(
                str(payload.get(key) or "")
                for key in ("description", "summary", "victim", "activity")
            )
            country_code = ""
            if threat.source == Threat.Source.RANSOMWARE:
                country_code = str(
                    payload.get("country_code") or payload.get("country") or ""
                )
            slugs = set(
                detect_geography_tag_slugs(
                    threat.title,
                    threat.title_vi,
                    threat.summary,
                    content,
                    str(payload.get("country") or ""),
                    country_code=country_code,
                    feed_url=str(payload.get("feed_url") or ""),
                    source_url=str(threat.source_url or payload.get("link") or ""),
                )
            )
            if slugs:
                detected.setdefault(threat.id, set()).update(slugs)
                all_slugs.update(slugs)

        Tag.objects.bulk_create(
            [
                Tag(slug=slug, name=slug.removeprefix("geo-").replace("-", " ").title())
                for slug in sorted(all_slugs)
            ],
            ignore_conflicts=True,
        )
        tags_by_slug = {
            tag.slug: tag for tag in Tag.objects.filter(slug__in=all_slugs)
        }
        through = Threat.tags.through
        links = [
            through(threat_id=threat_id, tag_id=tags_by_slug[slug].id)
            for threat_id, slugs in detected.items()
            for slug in slugs
            if slug in tags_by_slug
        ]
        through.objects.bulk_create(links, ignore_conflicts=True, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Geography retag complete · threats={len(detected)} "
                f"tags={len(all_slugs)} links={len(links)}"
            )
        )
