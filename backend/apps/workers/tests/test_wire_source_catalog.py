"""Curated source catalog must remain complete and safe to seed."""
import json
from pathlib import Path
import unittest


CATALOG = Path(__file__).resolve().parents[1] / "feeds" / "rss_sources.json"
EXPECTED = {
    "china-mod-curated", "xinhua-english-china", "xinhua-english-world", "people-cn-curated",
    "global-times-curated", "china-gov-curated", "miit-curated", "mofcom-curated",
    "china-export-control", "guangxi-gov-curated", "yunnan-gov-curated", "hainan-ocean-curated",
    "fangchenggang-gov-curated", "pna-curated", "gma-news-curated", "dfa-ph-curated", "dnd-ph-curated",
    "malaysia-mod-curated", "midas-malaysia-curated", "bernama-curated", "taiwan-mnd-curated",
    "taiwan-mna-curated", "singapore-mindef-curated", "indonesia-kemhan-curated", "tni-ppid-curated",
    "japan-mofa-curated", "ustr-curated", "cbp-curated", "dhs-curated", "bis-curated",
    "federal-register-curated", "reuters-asia-curated", "ap-asia-curated", "jakarta-post-curated",
    "star-malaysia-curated", "cna-curated", "straits-times-curated", "japan-times-curated",
    "nikkei-asia-curated", "kyodo-english-curated", "focus-taiwan-curated",
}


class WireSourceCatalogTests(unittest.TestCase):
    def test_requested_sources_are_curated_and_unique(self):
        rows = json.loads(CATALOG.read_text(encoding="utf-8"))
        by_name = {row["name"]: row for row in rows}
        self.assertTrue(EXPECTED <= set(by_name))
        self.assertEqual(len(rows), len({row["url"] for row in rows}))
        for name in EXPECTED:
            row = by_name[name]
            self.assertEqual(row["category"], "news")
            self.assertTrue(row["url"].startswith("https://"))
            self.assertTrue("wire-topic-v2" in row["notes"])
            self.assertTrue("filtered by title/lead" in row["notes"] or "official-rss" in row["notes"])


if __name__ == "__main__":
    unittest.main()
