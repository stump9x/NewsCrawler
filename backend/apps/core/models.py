from django.conf import settings
from django.db import models


class WireFilterPrompt(models.Model):
    """System policy or one private policy draft owned by an operational user."""

    singleton_key = models.CharField(
        max_length=32,
        unique=True,
        default="default",
        editable=False,
    )
    prompt = models.TextField()
    mindmap_prompt = models.TextField(blank=True, default="")
    favorite_recommendations_enabled = models.BooleanField(default=True)
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="wire_filter_policy",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wire_filter_prompt_updates",
    )
    mindmap_updated_at = models.DateTimeField(null=True, blank=True)
    mindmap_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="mindmap_prompt_updates",
    )

    class Meta:
        db_table = "core_wire_filter_prompt"
        verbose_name = "Chính sách lọc Trạm tin tức"
        verbose_name_plural = "Chính sách lọc Trạm tin tức"


class UserLoginAudit(models.Model):
    class Event(models.TextChoices):
        LOGIN_SUCCESS = "login_success", "Đăng nhập thành công"
        LOGIN_FAILURE = "login_failure", "Đăng nhập thất bại"
        LOGOUT = "logout", "Đăng xuất"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="login_audit_events",
    )
    username = models.CharField(max_length=150)
    event_type = models.CharField(max_length=32, choices=Event.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_user_login_audit"
        ordering = ("-occurred_at", "-id")
        indexes = [
            models.Index(fields=["-occurred_at"], name="core_login_time_idx"),
            models.Index(fields=["user", "-occurred_at"], name="core_login_user_time_idx"),
        ]


class WireFilterPromptRevision(models.Model):
    class Action(models.TextChoices):
        UPDATE = "update", "Cập nhật"
        RESET = "reset", "Đặt lại"

    class PolicyType(models.TextChoices):
        WIRE_FILTER = "wire_filter", "Trạm tin tức"
        MINDMAP = "mindmap", "Mindmap"

    policy = models.ForeignKey(
        WireFilterPrompt,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    policy_type = models.CharField(max_length=16, choices=PolicyType.choices, default=PolicyType.WIRE_FILTER)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wire_filter_policy_revisions",
    )
    owner_username = models.CharField(max_length=150)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wire_filter_policy_changes",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    prompt = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_wire_filter_prompt_revision"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["-created_at"], name="core_policy_rev_time_idx"),
            models.Index(fields=["owner", "-created_at"], name="core_policy_owner_time_idx"),
        ]
