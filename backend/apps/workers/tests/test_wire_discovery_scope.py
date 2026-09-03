from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.integrations.web_reader.exa_wire import discover_exa_wire_news, _wire_queries
from apps.integrations.web_reader.x_wire import _is_important_post


@override_settings(EXA_WIRE_QUERY_COUNT=2, EXA_WIRE_QUERIES="", EXA_WIRE_MAX_AGE_DAYS=30)
class DiscoveryScopeTests(SimpleTestCase):
    @patch("apps.integrations.web_reader.exa_wire.is_wire_relevant", side_effect=lambda row: row["title"] != "noise")
    @patch("apps.integrations.web_reader.exa_wire.search_exa")
    @patch("apps.integrations.web_reader.exa_wire._wire_enabled", return_value=True)
    def test_noise_does_not_consume_budget_or_starve_second_query(self, enabled, search, relevant):
        now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
        def hits(query, **kwargs):
            index = search.call_count
            return [{"title": "noise" if i == 0 else f"story {index}-{i}",
                     "url": f"https://example.com/{index}/{i}", "published": now.isoformat()}
                    for i in range(5)]
        search.side_effect = hits
        rows, stats = discover_exa_wire_news(limit=4, now=now)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["title"] for row in rows], ["story 1-1", "story 1-2", "story 2-1", "story 2-2"])

    @override_settings(EXA_WIRE_QUERIES="custom one|custom two")
    def test_custom_queries_are_preserved(self):
        self.assertEqual(_wire_queries(), ["custom one", "custom two"])

    @patch("apps.core.wire_filter_policy.get_wire_filter_prompt", return_value="")
    def test_x_uses_the_same_strategic_policy_scope(self, prompt):
        self.assertTrue(_is_important_post("Trung Quốc phê duyệt dự án điện hạt nhân", ""))
        self.assertFalse(_is_important_post("China launches new AI smartphone", ""))
