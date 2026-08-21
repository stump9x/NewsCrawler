from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.models import UserLoginAudit, WireFilterPrompt, WireFilterPromptRevision
from apps.core.wire_filter_policy import (
    apply_user_wire_policy,
    get_wire_filter_prompt_record,
)
from apps.intel.models import Threat


class WireFilterPromptApiTests(APITestCase):
    own_url = "/api/v1/auth/wire-filter-prompt/"
    reference_url = "/api/v1/auth/wire-filter-prompt/admin-reference/"
    admin_list_url = "/api/v1/auth/wire-filter-prompts/"

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="policy-admin",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        self.user_a = User.objects.create_user(
            username="policy-a",
            password="test-password",
            is_staff=True,
        )
        self.user_b = User.objects.create_user(
            username="policy-b",
            password="test-password",
            is_staff=True,
        )
        get_wire_filter_prompt_record()

    def test_user_can_edit_only_own_policy_and_read_admin_reference(self):
        self.client.force_authenticate(self.user_a)
        initial = self.client.get(self.own_url)
        self.assertEqual(initial.status_code, status.HTTP_200_OK)
        self.assertEqual(initial.data["owner_id"], self.user_a.pk)

        prompt = "NHIỆM VỤ\nGIỮ: diễn tập chung\nLOẠI: hoạt động cộng đồng"
        updated = self.client.patch(self.own_url, {"prompt": prompt}, format="json")
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(updated.data["prompt"], prompt)
        self.assertEqual(
            WireFilterPrompt.objects.get(owner=self.user_a).updated_by,
            self.user_a,
        )
        self.assertTrue(
            WireFilterPromptRevision.objects.filter(owner=self.user_a).exists()
        )

        reference = self.client.get(self.reference_url)
        self.assertEqual(reference.status_code, status.HTTP_200_OK)
        self.assertTrue(reference.data["read_only"])
        self.assertIsNone(reference.data["owner_id"])

        self.client.force_authenticate(self.user_b)
        other = self.client.get(self.own_url)
        self.assertEqual(other.status_code, status.HTTP_200_OK)
        self.assertNotEqual(other.data["prompt"], prompt)
        self.assertEqual(
            self.client.get(self.admin_list_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/auth/wire-filter-prompts/{self.user_a.pk}/"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_view_all_user_policies_but_users_remain_isolated(self):
        WireFilterPrompt.objects.create(
            singleton_key=f"user-{self.user_a.pk}",
            owner=self.user_a,
            prompt="GIỮ: tác chiến điện tử\nLOẠI: tin giải trí",
            updated_by=self.user_a,
        )
        self.client.force_authenticate(self.admin)
        listing = self.client.get(self.admin_list_url)
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        usernames = {row["owner_username"] for row in listing.data["results"]}
        self.assertEqual(usernames, {"policy-a", "policy-b"})

        detail = self.client.get(
            f"/api/v1/auth/wire-filter-prompts/{self.user_a.pk}/"
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["owner_username"], "policy-a")
        self.assertIn("tác chiến điện tử", detail.data["prompt"])

        inherited = self.client.get(
            f"/api/v1/auth/wire-filter-prompts/{self.user_b.pk}/"
        )
        self.assertEqual(inherited.status_code, status.HTTP_200_OK)
        self.assertTrue(inherited.data["inherited_from_admin"])

    def test_admin_edits_system_policy_and_user_reset_copies_it(self):
        admin_prompt = "GIỮ: chiến lược quốc gia\nLOẠI: lễ kỷ niệm"
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            self.own_url, {"prompt": admin_prompt}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_wire_filter_prompt_record().prompt, admin_prompt)

        self.client.force_authenticate(self.user_a)
        self.client.get(self.own_url)
        reset = self.client.post(self.own_url, {}, format="json")
        self.assertEqual(reset.status_code, status.HTTP_200_OK)
        self.assertEqual(reset.data["prompt"], admin_prompt)

    def test_personal_policy_filters_and_prioritizes_only_its_owner(self):
        policy_a = WireFilterPrompt.objects.create(
            singleton_key=f"user-{self.user_a.pk}",
            owner=self.user_a,
            prompt="GIỮ: tên lửa ưu tiên\nLOẠI: loại riêng",
            updated_by=self.user_a,
        )
        WireFilterPrompt.objects.create(
            singleton_key=f"user-{self.user_b.pk}",
            owner=self.user_b,
            prompt="GIỮ: nội dung khác\nLOẠI: không liên quan",
            updated_by=self.user_b,
        )
        excluded = Threat.objects.create(
            title="Story A",
            title_vi="Tin loại riêng",
            title_vi_status=Threat.TitleViStatus.OK,
            source=Threat.Source.NEWS,
        )
        prioritized = Threat.objects.create(
            title="Story B",
            title_vi="Tin tên lửa ưu tiên",
            title_vi_status=Threat.TitleViStatus.OK,
            source=Threat.Source.NEWS,
        )
        normal = Threat.objects.create(
            title="Story C",
            title_vi="Tin bình thường",
            title_vi_status=Threat.TitleViStatus.OK,
            source=Threat.Source.NEWS,
        )
        base = Threat.objects.filter(pk__in=[excluded.pk, prioritized.pk, normal.pk]).order_by(
            "-published_at", "-id"
        )

        ids_a = list(apply_user_wire_policy(base, self.user_a).values_list("pk", flat=True))
        ids_b = list(apply_user_wire_policy(base, self.user_b).values_list("pk", flat=True))
        self.assertNotIn(excluded.pk, ids_a)
        self.assertEqual(ids_a[0], prioritized.pk)
        self.assertIn(excluded.pk, ids_b)
        self.assertEqual(WireFilterPrompt.objects.get(pk=policy_a.pk).owner, self.user_a)

    def test_admin_can_view_login_and_policy_audit_history(self):
        self.client.force_authenticate(user=None)
        success = self.client.post(
            "/api/v1/auth/login/",
            {"username": self.user_a.username, "password": "test-password"},
            format="json",
        )
        self.assertEqual(success.status_code, status.HTTP_200_OK)
        failure = self.client.post(
            "/api/v1/auth/login/",
            {"username": self.user_b.username, "password": "wrong-password"},
            format="json",
        )
        self.assertEqual(failure.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(UserLoginAudit.objects.count(), 2)

        self.client.force_authenticate(self.user_a)
        denied = self.client.get("/api/v1/auth/account-audit/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        changed = self.client.patch(
            self.own_url,
            {"prompt": "GIỮ: diễn tập hải quân\nLOẠI: thể thao quân đội"},
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.admin)
        audit = self.client.get("/api/v1/auth/account-audit/?limit=20")
        self.assertEqual(audit.status_code, status.HTTP_200_OK)
        event_types = {row["event_type"] for row in audit.data["login_events"]}
        self.assertEqual(event_types, {"login_success", "login_failure"})
        account_names = {row["username"] for row in audit.data["accounts"]}
        self.assertIn(self.admin.username, account_names)
        self.assertTrue(
            any(
                row["owner_username"] == self.user_a.username
                for row in audit.data["policy_changes"]
            )
        )
