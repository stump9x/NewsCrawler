from django.test import SimpleTestCase

from apps.integrations.ai.translate import (
    accept_google_translation,
    translation_length_implausible,
)


class WireTitleTranslationTests(SimpleTestCase):
    def test_truncated_vietnamese_title_is_rejected(self):
        source = "How Japan plans to strengthen maritime security cooperation"
        self.assertTrue(translation_length_implausible(source, "Cách Nhật Bản dự"))
        self.assertTrue(accept_google_translation(source, "Nhật Bản lên kế hoạch tăng cường hợp tác an ninh hàng hải"))

    def test_mixed_english_title_is_rejected(self):
        source = "China announces a new naval exercise"
        self.assertFalse(accept_google_translation(source, "Trung Quốc announces tập trận hải quân mới"))
