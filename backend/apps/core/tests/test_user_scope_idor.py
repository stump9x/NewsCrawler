from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.integrations.models import AIBriefing, GitHubScan
from apps.intel.models import AlertNotification, Tag, WatchRule


class UserScopeIdorTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user("scope-admin", password="pass", is_staff=True, is_superuser=True)
        self.user_a = User.objects.create_user("scope-a", password="pass", is_staff=True)
        self.user_b = User.objects.create_user("scope-b", password="pass", is_staff=True)

    def test_watch_rules_and_notifications_are_isolated(self):
        rule_a = WatchRule.objects.create(name="A", keyword="alpha", created_by=self.user_a)
        rule_b = WatchRule.objects.create(name="B", keyword="bravo", created_by=self.user_b)
        notification_a = AlertNotification.objects.create(title="A", recipient=self.user_a)
        notification_b = AlertNotification.objects.create(title="B", recipient=self.user_b)
        system_notification = AlertNotification.objects.create(title="System")

        self.client.force_authenticate(self.user_a)
        rules = self.client.get("/api/v1/watch-rules/")
        self.assertEqual(rules.status_code, 200)
        self.assertEqual({row["id"] for row in rules.data["results"]}, {rule_a.pk})
        self.assertEqual(self.client.patch(f"/api/v1/watch-rules/{rule_b.pk}/", {"is_active": False}).status_code, 404)

        notifications = self.client.get("/api/v1/notifications/")
        self.assertEqual(notifications.status_code, 200)
        notification_ids = {row["id"] for row in notifications.data["results"]}
        self.assertIn(notification_a.pk, notification_ids)
        self.assertIn(system_notification.pk, notification_ids)
        self.assertNotIn(notification_b.pk, notification_ids)
        self.assertEqual(self.client.patch(f"/api/v1/notifications/{notification_b.pk}/", {"is_read": True}).status_code, 404)

    def test_user_cannot_mutate_global_records_or_read_other_briefings(self):
        briefing_a = AIBriefing.objects.create(title="A", created_by=self.user_a, status=AIBriefing.Status.READY)
        briefing_b = AIBriefing.objects.create(title="B", created_by=self.user_b, status=AIBriefing.Status.READY)
        self.client.force_authenticate(self.user_a)
        tag_create = self.client.post("/api/v1/tags/", {"name": "user-tag"}, format="json")
        self.assertEqual(tag_create.status_code, 403)
        briefings = self.client.get("/api/v1/ai/briefings/")
        self.assertEqual(briefings.status_code, 200)
        self.assertEqual({row["id"] for row in briefings.data["results"]}, {briefing_a.pk})
        self.assertEqual(self.client.get(f"/api/v1/ai/briefings/{briefing_b.pk}/").status_code, 404)

    def test_admin_can_manage_global_records_and_view_all_briefings(self):
        briefing_a = AIBriefing.objects.create(title="A", created_by=self.user_a, status=AIBriefing.Status.READY)
        briefing_b = AIBriefing.objects.create(title="B", created_by=self.user_b, status=AIBriefing.Status.READY)
        self.client.force_authenticate(self.admin)
        tag_create = self.client.post("/api/v1/tags/", {"name": "admin-tag"}, format="json")
        self.assertEqual(tag_create.status_code, 201)
        briefings = self.client.get("/api/v1/ai/briefings/")
        self.assertEqual(briefings.status_code, 200)
        self.assertEqual({row["id"] for row in briefings.data["results"]}, {briefing_a.pk, briefing_b.pk})


    def test_user_cannot_bulk_delete_other_users_scan_or_change_shared_router_state(self):
        scan_b = GitHubScan.objects.create(keyword="bravo", created_by=self.user_b)
        self.client.force_authenticate(self.user_a)
        response = self.client.post(
            "/api/v1/github/scans/bulk-delete/",
            {"ids": [scan_b.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted"], [])
        self.assertTrue(GitHubScan.objects.filter(pk=scan_b.pk).exists())
        self.assertEqual(
            self.client.post(
                "/api/v1/ai/notebook-chat/mark-provider/",
                {"provider": "groq", "seconds": 30},
                format="json",
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/ai/notebook-chat/unload-ollama/",
                {},
                format="json",
            ).status_code,
            403,
        )
