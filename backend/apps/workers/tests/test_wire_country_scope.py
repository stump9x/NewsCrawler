from django.test import SimpleTestCase

from apps.workers.services import (
    is_irrelevant_country_mix,
    is_non_priority_country_only,
    is_wire_relevant,
)


class WireCountryScopeTests(SimpleTestCase):
    def test_unrelated_countries_are_rejected(self):
        item = {"title": "UK and Iraq announce a new military exercise"}
        self.assertTrue(is_non_priority_country_only(item))
        self.assertFalse(is_wire_relevant(item, prompt=""))

    def test_us_with_unrelated_partner_is_rejected(self):
        item = {"title": "US and UK expand a joint defence programme"}
        self.assertTrue(is_irrelevant_country_mix(item))

    def test_us_china_story_is_kept(self):
        item = {"title": "US and China hold talks on South China Sea safety"}
        self.assertFalse(is_irrelevant_country_mix(item))

    def test_us_china_story_with_extra_country_is_kept(self):
        item = {"title": "US, China and UK discuss South China Sea operations"}
        self.assertFalse(is_irrelevant_country_mix(item))

    def test_cyber_exception_is_kept(self):
        item = {"title": "US and UK launch a major cyber warfare response"}
        self.assertFalse(is_irrelevant_country_mix(item))
