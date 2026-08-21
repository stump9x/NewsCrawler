from django.test import TestCase

from apps.workers.services import is_wire_relevant


class WireNoiseFilterTests(TestCase):
    def assert_noise(self, title):
        self.assertFalse(
            is_wire_relevant(
                {
                    "category": "news",
                    "title": title,
                    "summary": "",
                    "feed": "defense-news",
                    "country": "Mỹ",
                    "country_code": "US",
                }
            ),
            title,
        )

    def test_examples_are_rejected_even_when_feed_is_defense(self):
        for title in (
            "Michigan primary election will determine midterm route",
            "Washington National Guard supports statewide wildfire response",
            "ALS risk varies by military branch, study finds",
            "DC National Guard mission ends: Narcan doses and lost children",
            "3 rebels killed in clash in Abra",
            "CPO is key to Indonesia exports: minister",
            "USS Virginia built to counter the Soviet Navy",
            "Yesterday's decisions, tomorrow's mission: meet Dr. Brian Medley",
            "How the F4U Corsair got its distinctive look",
            "A last look at Old Salt: former USS Nimitz sailors say goodbye",
        ):
            self.assert_noise(title)

    def test_substantive_current_defense_story_is_not_blanket_rejected(self):
        self.assertTrue(
            is_wire_relevant(
                {
                    "category": "news",
                    "title": "Mỹ and Japan sign regional air-defense missile agreement",
                    "summary": "The two governments will transfer and deploy an integrated system.",
                    "feed": "defense-news",
                    "country": "Mỹ; Nhật Bản",
                    "country_code": "US;JP",
                }
            )
        )
