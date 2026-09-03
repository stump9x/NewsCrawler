from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import WireFilterPrompt
from apps.core.wire_filter_policy import (
    DEFAULT_WIRE_FILTER_PROMPT, LEGACY_WIRE_FILTER_PROMPT,
    annotate_favorite_recommendations, clear_wire_filter_prompt_cache,
)
from apps.intel.models import Tag, Threat, ThreatFavorite
from apps.workers.services import is_wire_relevant, _classify_rss_item


class TopicRecommendationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("reader")
        clear_wire_filter_prompt_cache()

    def tearDown(self):
        clear_wire_filter_prompt_cache()

    def story(self, title, *slugs, relevant=True):
        obj = Threat.objects.create(title=title, source=Threat.Source.NEWS, wire_relevant=relevant)
        for slug in slugs:
            tag, _ = Tag.objects.get_or_create(slug=slug, defaults={"name": slug})
            obj.tags.add(tag)
        return obj

    def scores(self):
        return dict(annotate_favorite_recommendations(Threat.objects.all(), self.user).values_list("id", "personal_interest_score"))

    def test_same_favorite_must_share_specific_topic_and_country(self):
        favorite = self.story("favorite", "wire-topic-1a", "geo-china", "maritime", "site-example-com")
        ThreatFavorite.objects.create(user=self.user, threat=favorite)
        correct = self.story("new patrol", "wire-topic-1a", "geo-china")
        same_source_geo = self.story("different issue", "geo-china", "maritime", "site-example-com")
        different_topic = self.story("new Chinese policy", "wire-topic-2c", "geo-china")
        different_country = self.story("new patrol elsewhere", "wire-topic-1a", "geo-japan")
        hidden = self.story("hidden", "wire-topic-1a", "geo-china", relevant=False)
        scores = self.scores()
        self.assertEqual(scores[correct.id], 3)
        for obj in (favorite, same_source_geo, different_topic, different_country, hidden):
            self.assertEqual(scores[obj.id], 0)

    def test_cannot_combine_country_and_topic_from_different_favorites(self):
        for obj in (self.story("A", "wire-topic-1a", "geo-china"), self.story("B", "wire-topic-2c", "geo-japan")):
            ThreatFavorite.objects.create(user=self.user, threat=obj)
        candidate = self.story("C", "wire-topic-1a", "geo-japan")
        self.assertEqual(self.scores()[candidate.id], 0)

    def test_retired_topic_does_not_recommend_even_before_reclassification(self):
        favorite = self.story("old visit", "wire-topic-6", "vietnam")
        candidate = self.story("new visit", "wire-topic-6", "vietnam")
        ThreatFavorite.objects.create(user=self.user, threat=favorite)
        self.assertEqual(self.scores()[candidate.id], 0)

    def test_fresh_source_lead_restores_hidden_article_without_duplicate(self):
        from apps.workers.services import ingest_rss_items
        article = self.story("Japan announces defense changes", "wire-topic-6", "geo-china", relevant=False)
        ThreatFavorite.objects.create(user=self.user, threat=article)
        item = {"title": article.title, "link": "https://example.com/defense",
                "published": timezone.now().isoformat(),
                "summary": "Japan publishes its defense white paper and national defense strategy."}
        with patch("apps.intel.retention.trim_wire_overflow", return_value=0):
            stats = ingest_rss_items([item])
            second = ingest_rss_items([item])
        article.refresh_from_db()
        self.assertTrue(article.wire_relevant)
        self.assertIn("defense white paper", article.summary)
        self.assertTrue(article.tags.filter(slug="wire-topic-3a").exists())
        self.assertFalse(article.tags.filter(slug__in=["wire-topic-6", "geo-china"]).exists())
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(second["skipped_existing"], 1)
        self.assertEqual(Threat.objects.count(), 1)
        self.assertEqual(ThreatFavorite.objects.get().threat_id, article.pk)

    def test_existing_labels_and_retired_topics_are_migrated_and_restorable(self):
        from apps.core.wire_topics import TOPIC_LABELS
        active = Tag.objects.create(slug="wire-topic-1a", name="1A. Biển Đông: hoạt động thực địa")
        retired = self.story("Dư luận quốc tế đánh giá chuyến thăm Việt Nam của tàu sân bay Mỹ", "wire-topic-6", "vietnam")
        Tag.objects.create(slug="wire-topic-7", name="7. Dư luận")
        ThreatFavorite.objects.create(user=self.user, threat=retired)
        with TemporaryDirectory() as temp:
            backup = str(Path(temp) / "topics.jsonl")
            call_command("reclassify_wire_topics", apply=True, update_prompt=True, backup=backup, stdout=StringIO())
            active.refresh_from_db()
            retired.refresh_from_db()
            self.assertEqual(active.name, TOPIC_LABELS[active.slug])
            self.assertFalse(Tag.objects.filter(slug__in=["wire-topic-6", "wire-topic-7"]).exists())
            self.assertFalse(retired.wire_relevant)
            self.assertEqual(ThreatFavorite.objects.count(), 1)
            call_command("reclassify_wire_topics", apply=True, restore=backup, stdout=StringIO())
            active.refresh_from_db()
            retired.refresh_from_db()
            self.assertEqual(active.name, "1A. Biển Đông: hoạt động thực địa")
            self.assertTrue(retired.tags.filter(slug="wire-topic-6").exists())
            self.assertEqual(Tag.objects.get(slug="wire-topic-7").name, "7. Dư luận")
            self.assertTrue(retired.wire_relevant)

    def test_disabled_recommendations_and_other_users_are_respected(self):
        other = get_user_model().objects.create_user("other")
        favorite = self.story("A", "wire-topic-1a", "geo-china")
        candidate = self.story("B", "wire-topic-1a", "geo-china")
        ThreatFavorite.objects.create(user=other, threat=favorite)
        self.assertEqual(self.scores()[candidate.id], 0)
        ThreatFavorite.objects.create(user=self.user, threat=favorite)
        WireFilterPrompt.objects.create(singleton_key=f"user-{self.user.id}", owner=self.user, prompt=DEFAULT_WIRE_FILTER_PROMPT, favorite_recommendations_enabled=False)
        self.assertEqual(self.scores()[candidate.id], 0)

    def test_keep_directive_cannot_bypass_topic_gates_and_exclude_wins(self):
        self.assertFalse(is_wire_relevant({"title": "China AI smartphone sale"}, prompt="GIỮ: smartphone"))
        title = "Trung Quốc phê duyệt dự án điện hạt nhân"
        self.assertTrue(is_wire_relevant({"title": title}, prompt="GIỮ: điện hạt nhân"))
        self.assertFalse(is_wire_relevant({"title": title}, prompt="GIỮ: điện hạt nhân\nLOẠI: điện hạt nhân"))

    def test_publisher_geography_does_not_supply_recommendation_tags(self):
        _, _, tags, _, _ = _classify_rss_item({
            "title": "Nhật Bản công bố Sách trắng Quốc phòng",
            "feed": "china-news", "feed_url": "https://www.mod.gov.cn/",
            "country": "Trung Quốc", "country_code": "CN",
        })
        self.assertIn("geo-japan", tags)
        self.assertNotIn("geo-china", tags)

    def test_editorial_examples_also_pass_the_ingest_policy(self):
        from apps.workers.tests.test_wire_topics import EXAMPLES, LEADS
        for titles in EXAMPLES.values():
            for title in titles:
                lead = next((text for prefix, text in LEADS.items() if title.startswith(prefix)), "")
                with self.subTest(title=title):
                    self.assertTrue(is_wire_relevant({"title": title, "summary": lead}, prompt=DEFAULT_WIRE_FILTER_PROMPT))

    def test_reclassification_is_reversible_without_deleting_favorites(self):
        good = self.story("Trung Quốc phê duyệt dự án điện hạt nhân")
        noise = self.story("Vietnam military families birthday celebration", "geo-china")
        ThreatFavorite.objects.create(user=self.user, threat=noise)
        admin = WireFilterPrompt.objects.create(singleton_key="default", prompt=LEGACY_WIRE_FILTER_PROMPT)
        private = WireFilterPrompt.objects.create(singleton_key=f"user-{self.user.id}", owner=self.user, prompt="GIỮ: chính sách riêng\nLOẠI: ví dụ riêng")
        with TemporaryDirectory() as temp:
            backup = Path(temp) / "scope.jsonl"
            call_command("reclassify_wire_topics", update_prompt=True, stdout=StringIO())
            noise.refresh_from_db()
            self.assertTrue(noise.wire_relevant)
            admin.refresh_from_db()
            self.assertEqual(admin.prompt, LEGACY_WIRE_FILTER_PROMPT)
            call_command("reclassify_wire_topics", apply=True, update_prompt=True, backup=str(backup), stdout=StringIO())
            good.refresh_from_db()
            noise.refresh_from_db()
            admin.refresh_from_db()
            private.refresh_from_db()
            self.assertTrue(good.wire_relevant)
            self.assertTrue(good.tags.filter(slug="wire-topic-2c").exists())
            self.assertFalse(noise.wire_relevant)
            self.assertFalse(noise.tags.filter(slug="geo-china").exists())
            self.assertEqual(ThreatFavorite.objects.count(), 1)
            self.assertEqual(Threat.objects.count(), 2)
            self.assertEqual(admin.prompt, DEFAULT_WIRE_FILTER_PROMPT)
            self.assertIn("chính sách riêng", private.prompt)
            self.assertEqual(admin.revisions.count(), 2)
            call_command("reclassify_wire_topics", apply=True, restore=str(backup), stdout=StringIO())
            good.refresh_from_db()
            noise.refresh_from_db()
            admin.refresh_from_db()
            self.assertTrue(noise.wire_relevant)
            self.assertTrue(noise.tags.filter(slug="geo-china").exists())
            self.assertNotIn("wire_scope", good.raw_payload)
            self.assertFalse(good.tags.filter(slug__startswith="wire-topic-").exists())
            self.assertEqual(admin.prompt, LEGACY_WIRE_FILTER_PROMPT)
