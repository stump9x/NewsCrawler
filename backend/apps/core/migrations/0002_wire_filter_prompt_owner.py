from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_wire_filter_prompt"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="wirefilterprompt",
            name="owner",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="wire_filter_policy",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="UserLoginAudit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("username", models.CharField(max_length=150)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("login_success", "Đăng nhập thành công"),
                            ("login_failure", "Đăng nhập thất bại"),
                            ("logout", "Đăng xuất"),
                        ],
                        max_length=32,
                    ),
                ),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=512)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="login_audit_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "core_user_login_audit",
                "ordering": ("-occurred_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["-occurred_at"], name="core_login_time_idx"
                    ),
                    models.Index(
                        fields=["user", "-occurred_at"],
                        name="core_login_user_time_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="WireFilterPromptRevision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("owner_username", models.CharField(max_length=150)),
                (
                    "action",
                    models.CharField(
                        choices=[("update", "Cập nhật"), ("reset", "Đặt lại")],
                        max_length=16,
                    ),
                ),
                ("prompt", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="wire_filter_policy_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="wire_filter_policy_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="core.wirefilterprompt",
                    ),
                ),
            ],
            options={
                "db_table": "core_wire_filter_prompt_revision",
                "ordering": ("-created_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["-created_at"], name="core_policy_rev_time_idx"
                    ),
                    models.Index(
                        fields=["owner", "-created_at"],
                        name="core_policy_owner_time_idx",
                    ),
                ],
            },
        ),
    ]
