from django.urls import path

from .auth_views import (
    AccountAuditAdminView,
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    OperationalUserDetailView,
    OperationalUserListCreateView,
    WireFilterPromptAdminDetailView,
    WireFilterPromptAdminListView,
    WireFilterPromptAdminReferenceView,
    WireFilterPromptView,
    MindmapPromptAdminReferenceView,
    MindmapPromptView,
)
from .views import HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("v1/auth/login/", LoginView.as_view(), name="auth-login"),
    path("v1/auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("v1/auth/me/", MeView.as_view(), name="auth-me"),
    path("v1/auth/users/", OperationalUserListCreateView.as_view(), name="auth-users"),
    path(
        "v1/auth/wire-filter-prompt/",
        WireFilterPromptView.as_view(),
        name="wire-filter-prompt",
    ),
    path(
        "v1/auth/wire-filter-prompt/admin-reference/",
        WireFilterPromptAdminReferenceView.as_view(),
        name="wire-filter-prompt-admin-reference",
    ),
    path(
        "v1/auth/mindmap-prompt/",
        MindmapPromptView.as_view(),
        name="mindmap-prompt",
    ),
    path(
        "v1/auth/mindmap-prompt/admin-reference/",
        MindmapPromptAdminReferenceView.as_view(),
        name="mindmap-prompt-admin-reference",
    ),
    path(
        "v1/auth/wire-filter-prompts/",
        WireFilterPromptAdminListView.as_view(),
        name="wire-filter-prompts-admin-list",
    ),
    path(
        "v1/auth/wire-filter-prompts/<int:user_id>/",
        WireFilterPromptAdminDetailView.as_view(),
        name="wire-filter-prompts-admin-detail",
    ),
    path(
        "v1/auth/account-audit/",
        AccountAuditAdminView.as_view(),
        name="account-audit-admin",
    ),
    path(
        "v1/auth/users/<int:user_id>/",
        OperationalUserDetailView.as_view(),
        name="auth-user-detail",
    ),
    path(
        "v1/auth/change-password/",
        ChangePasswordView.as_view(),
        name="auth-change-password",
    ),
]
