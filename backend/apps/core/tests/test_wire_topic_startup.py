from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import WireFilterPrompt, WireTopicRollout
from apps.core.wire_filter_policy import (
    DEFAULT_WIRE_FILTER_PROMPT, LEGACY_WIRE_FILTER_PROMPT, clear_wire_filter_prompt_cache,
)
from apps.core.wire_topics import POLICY_VERSION
from apps.intel.models import Threat


class WireTopicStartupTests(TestCase):
    def setUp(self):
        self.policy = WireFilterPrompt.objects.create(
            singleton_key="default", prompt=LEGACY_WIRE_FILTER_PROMPT,
        )
        self.noise = Threat.objects.create(
            title="Vietnam military families birthday celebration", source=Threat.Source.NEWS,
        )
        clear_wire_filter_prompt_cache()

    def tearDown(self):
        clear_wire_filter_prompt_cache()

    def test_startup_upgrades_once_and_preserves_subsequent_prompt_edits(self):
        with TemporaryDirectory() as directory:
            call_command("prepare_wire_topics", backup_dir=directory, stdout=StringIO())
            self.noise.refresh_from_db()
            self.policy.refresh_from_db()
            self.assertFalse(self.noise.wire_relevant)
            self.assertEqual(self.policy.prompt, DEFAULT_WIRE_FILTER_PROMPT)
            rollout = WireTopicRollout.objects.get(version=POLICY_VERSION)
            self.assertIsNotNone(rollout.completed_at)
            self.assertTrue(Path(rollout.backup_path).is_file())
            self.assertEqual(Threat.objects.count(), 1)

            self.policy.prompt = "GIỮ: chính sách riêng sau triển khai"
            self.policy.save()
            call_command("prepare_wire_topics", backup_dir=directory, stdout=StringIO())
            self.policy.refresh_from_db()
            self.assertEqual(self.policy.prompt, "GIỮ: chính sách riêng sau triển khai")
            self.assertEqual(len(list(Path(directory).glob("*.jsonl"))), 1)

    def test_failed_upgrade_rolls_back_and_next_startup_can_retry(self):
        def fail_midway(*args, **kwargs):
            Threat.objects.filter(pk=self.noise.pk).update(wire_relevant=False)
            WireFilterPrompt.objects.filter(pk=self.policy.pk).update(prompt="partial")
            raise RuntimeError("interrupted upgrade")

        with TemporaryDirectory() as directory:
            with patch("apps.core.management.commands.prepare_wire_topics.call_command", side_effect=fail_midway):
                with self.assertRaisesMessage(RuntimeError, "interrupted upgrade"):
                    call_command("prepare_wire_topics", backup_dir=directory, stdout=StringIO())
            self.noise.refresh_from_db()
            self.policy.refresh_from_db()
            self.assertTrue(self.noise.wire_relevant)
            self.assertEqual(self.policy.prompt, LEGACY_WIRE_FILTER_PROMPT)
            self.assertFalse(WireTopicRollout.objects.exists())
            call_command("prepare_wire_topics", backup_dir=directory, stdout=StringIO())
            self.assertTrue(WireTopicRollout.objects.filter(completed_at__isnull=False).exists())

    def test_backup_failure_does_not_modify_news_or_mark_success(self):
        with TemporaryDirectory() as directory:
            invalid = Path(directory) / "file-not-directory"
            invalid.write_text("occupied", encoding="utf-8")
            with self.assertRaises(OSError):
                call_command("prepare_wire_topics", backup_dir=str(invalid), stdout=StringIO())
        self.noise.refresh_from_db()
        self.assertTrue(self.noise.wire_relevant)
        self.assertFalse(WireTopicRollout.objects.exists())
