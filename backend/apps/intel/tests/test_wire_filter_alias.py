from django.test import TestCase

from apps.intel.filters import ThreatFilter
from apps.intel.models import Tag, Threat


class WireTopicFilterAliasTests(TestCase):
    def test_south_china_sea_filter_matches_both_subtopics(self):
        first = Threat.objects.create(title="Scarborough patrol")
        second = Threat.objects.create(title="South China Sea regulation")
        other = Threat.objects.create(title="Taiwan exercise")
        first.tags.add(Tag.objects.create(name="Field activity", slug="wire-topic-1a"))
        second.tags.add(Tag.objects.create(name="Sovereignty", slug="wire-topic-1b"))
        other.tags.add(Tag.objects.create(name="Exercise", slug="wire-topic-4a"))

        result = ThreatFilter(
            {"tag": "wire-topic-south-china-sea"},
            queryset=Threat.objects.all(),
        ).qs

        self.assertCountEqual(result, [first, second])
