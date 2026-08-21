"""Seed default military/defence PDF scan keywords (aligned with Wire topics).

Only exact **two-word** phrases — Google phrase dorks like
``\"cyber warfare\" filetype:pdf``. Longer / single-token rows are purged.
"""

from django.core.management.base import BaseCommand

from apps.intel.models import DocumentScanKeyword, DeletedDocumentScanKeyword


# (name, keyword, priority, notes) — keyword MUST be exactly two whitespace-separated tokens.
DEFAULT_KEYWORDS = (
    # —— Geography / theatres ——
    ("Taiwan Strait", "Taiwan Strait", 98, ""),
    ("Taiwan Defense", "Taiwan defense", 96, ""),
    ("South China", "South China", 95, "South China Sea titles"),
    ("Philippine Sea", "Philippine Sea", 94, "West Philippine Sea / WPS"),
    ("Scarborough Shoal", "Scarborough Shoal", 93, ""),
    ("Thomas Shoal", "Thomas Shoal", 92, "Second Thomas / Ayungin"),
    ("Island Chain", "island chain", 80, ""),
    # —— China forces (full words) ——
    ("Chinese Navy", "Chinese navy", 94, ""),
    ("Chinese Military", "Chinese military", 93, ""),
    ("Rocket Force", "rocket force", 90, ""),
    ("Coast Guard", "coast guard", 88, ""),
    ("Military Power", "military power", 89, ""),
    ("Amphibious Assault", "amphibious assault", 82, ""),
    # —— Cyber / EW ——
    ("Cyber Warfare", "cyber warfare", 97, ""),
    ("Cyber Operations", "cyber operations", 95, ""),
    ("Cyber Command", "cyber command", 90, ""),
    ("Cyber Defense", "cyber defense", 91, ""),
    ("Information Warfare", "information warfare", 94, ""),
    ("Electronic Warfare", "electronic warfare", 92, ""),
    ("Cognitive Warfare", "cognitive warfare", 84, ""),
    ("Philippine Cyber", "Philippine cyber", 90, ""),
    # —— Maritime / undersea ——
    ("Maritime Security", "maritime security", 91, ""),
    ("Naval Warfare", "naval warfare", 86, ""),
    ("Undersea Warfare", "undersea warfare", 85, ""),
    ("Submarine Warfare", "submarine warfare", 84, ""),
    ("Carrier Strike", "carrier strike", 80, ""),
    ("Mine Warfare", "mine warfare", 74, ""),
    ("Sea Denial", "sea denial", 76, ""),
    ("Maritime Patrol", "maritime patrol", 83, ""),
    # —— Allies / PH ——
    ("Philippines Defense", "Philippines defense", 92, ""),
    ("Philippine Navy", "Philippine navy", 90, ""),
    ("Armed Forces", "armed forces", 78, "Prefer with PH/China context in SERP"),
    ("Japan Defense", "Japan defense", 86, ""),
    ("Korea Defense", "Korea defense", 82, ""),
    ("Australia Defence", "Australia defence", 84, ""),
    ("India Navy", "India navy", 78, ""),
    ("Vietnam Maritime", "Vietnam maritime", 80, ""),
    # —— Force posture / exercises ——
    ("Force Posture", "force posture", 88, ""),
    ("Security Cooperation", "security cooperation", 85, ""),
    ("Joint Exercise", "joint exercise", 86, ""),
    ("Balikatan Exercise", "Balikatan exercise", 87, ""),
    ("Forward Presence", "forward presence", 78, ""),
    ("Defense Cooperation", "defense cooperation", 84, ""),
    # —— Missiles / air / space ——
    ("Hypersonic Missile", "hypersonic missile", 90, ""),
    ("Ballistic Missile", "ballistic missile", 88, ""),
    ("Missile Defense", "missile defense", 87, ""),
    ("Air Defense", "air defense", 84, ""),
    ("Unmanned Aircraft", "unmanned aircraft", 80, ""),
    ("Space Warfare", "space warfare", 79, ""),
    ("Counterspace Operations", "counterspace operations", 78, ""),  # 2 words? counterspace + operations = 2
    # —— Strategy / nuclear / publishers ——
    ("Nuclear Deterrence", "nuclear deterrence", 93, ""),
    ("Extended Deterrence", "extended deterrence", 88, ""),
    ("Defense Strategy", "defense strategy", 90, ""),
    ("Defense Policy", "defense policy", 84, ""),
    ("Military Doctrine", "military doctrine", 80, ""),
    ("Defense Primer", "Defense Primer", 96, "CRS primer series"),
    ("Naval College", "Naval College", 75, ""),
    ("War Gaming", "war gaming", 82, ""),
    ("Indo-Pacific Defense", "Indo-Pacific defense", 94, ""),
    ("Indo-Pacific Strategy", "Indo-Pacific strategy", 92, ""),
    ("National Defense", "national defense", 86, ""),
    ("Pentagon Report", "Pentagon report", 88, ""),
    ("Critical Infrastructure", "critical infrastructure", 81, ""),
)

# Must be exactly two tokens (whitespace-split).
assert all(
    len(k.split()) == 2 for _, k, _, _ in DEFAULT_KEYWORDS
), "DEFAULT_KEYWORDS must be exactly two words each"


class Command(BaseCommand):
    help = (
        "Seed DocumentScanKeyword rows (exactly two-word phrases). "
        "With --deactivate-missing, delete every keyword not in the seed list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="Delete keywords not present in the two-word default seed list.",
        )

    def handle(self, *args, **options):
        created = 0
        updated = 0
        skipped_deleted = 0
        skipped_bad = 0
        seen_ids: list[int] = []
        user_deleted = {
            (row.keyword, row.filetypes or "pdf")
            for row in DeletedDocumentScanKeyword.objects.all().only(
                "keyword", "filetypes"
            )
        }
        for name, keyword, priority, notes in DEFAULT_KEYWORDS:
            if (keyword, "pdf") in user_deleted:
                skipped_deleted += 1
                continue
            words = [w for w in keyword.split() if w]
            if len(words) != 2:
                skipped_bad += 1
                self.stdout.write(
                    self.style.WARNING(f"skip non-two-word keyword: {keyword!r}")
                )
                continue
            obj, was_created = DocumentScanKeyword.objects.update_or_create(
                keyword=keyword,
                filetypes="pdf",
                defaults={
                    "name": name,
                    "is_active": True,
                    "priority": priority,
                    "notes": notes,
                },
            )
            seen_ids.append(obj.id)
            if was_created:
                created += 1
            else:
                updated += 1

        # Always purge phrases that are not exactly two words.
        non_two = []
        for row in DocumentScanKeyword.objects.only("id", "keyword").iterator():
            if len((row.keyword or "").split()) != 2:
                non_two.append(row.id)
        deleted_non_two = 0
        if non_two:
            deleted_non_two, _ = DocumentScanKeyword.objects.filter(id__in=non_two).delete()

        deleted_missing = 0
        if options.get("deactivate_missing"):
            qs = DocumentScanKeyword.objects.exclude(id__in=seen_ids)
            deleted_missing, _ = qs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                "document scan keywords: "
                f"created={created} updated={updated} "
                f"skipped_user_deleted={skipped_deleted} skipped_bad={skipped_bad} "
                f"deleted_non_two_word={deleted_non_two} deleted_extra={deleted_missing}"
            )
        )
