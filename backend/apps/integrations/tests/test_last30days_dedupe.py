from django.test import TestCase

from apps.integrations.last30days.service import _canonical_url, persist_findings
from apps.integrations.models import Last30DaysResearch


class Last30DaysDedupeTests(TestCase):
    def test_canonical_url_drops_tracking_variants(self):
        self.assertEqual(
            _canonical_url("https://www.example.com/story/?utm_source=x&ref=feed&id=9"),
            "https://example.com/story?id=9",
        )

    def test_same_headline_from_different_platforms_is_kept_once(self):
        research = Last30DaysResearch.objects.create(
            topic="South China Sea", lookback_days=30, status="completed"
        )
        payload = {
            "items_by_source": {
                "x": [{
                    "title": "China expands maritime patrols in South China Sea",
                    "url": "https://x.com/a/status/1?utm_source=share",
                }],
                "reddit": [{
                    "title": "China expands maritime patrols in South China Sea",
                    "url": "https://www.reddit.com/r/news/comments/1/story/?ref=feed",
                }],
            }
        }
        self.assertEqual(persist_findings(research, payload), 1)
        self.assertEqual(research.findings.count(), 1)
