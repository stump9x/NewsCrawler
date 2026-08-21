"""Auth API — login issues expiring Token; logout deletes it."""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsSuperUser
from .models import UserLoginAudit, WireFilterPrompt, WireFilterPromptRevision
from .wire_filter_policy import (
    DEFAULT_WIRE_FILTER_PROMPT,
    MAX_WIRE_FILTER_PROMPT_CHARS,
    clear_wire_filter_prompt_cache,
    get_user_wire_filter_prompt_record,
    get_wire_filter_prompt_record,
    parse_wire_filter_directives,
)
from .mindmap_policy import (
    DEFAULT_MINDMAP_PROMPT,
    MAX_MINDMAP_PROMPT_CHARS,
    clear_mindmap_prompt_cache,
    get_mindmap_prompt,
    get_mindmap_prompt_record,
    get_user_mindmap_prompt_record,
)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        max_length=128, write_only=True, trim_whitespace=False
    )
    new_password = serializers.CharField(
        max_length=128, write_only=True, trim_whitespace=False, min_length=8
    )
    confirm_password = serializers.CharField(
        max_length=128, write_only=True, trim_whitespace=False
    )

    def validate(self, attrs):
        new_password = attrs["new_password"]
        confirm = attrs["confirm_password"]
        if new_password != confirm:
            raise serializers.ValidationError(
                {
                    "confirm_password": "Mật khẩu xác nhận không khớp.",
                    "code": "password_mismatch",
                }
            )
        if new_password == attrs["current_password"]:
            raise serializers.ValidationError(
                {
                    "new_password": "Mật khẩu mới phải khác mật khẩu hiện tại.",
                    "code": "password_unchanged",
                }
            )
        user = self.context["request"].user
        try:
            password_validation.validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": list(exc.messages), "code": "password_invalid"}
            ) from exc
        return attrs


class OperationalUserCreateSerializer(serializers.Serializer):
    username = serializers.RegexField(
        r"^[\w.@+-]+$",
        min_length=3,
        max_length=150,
        trim_whitespace=True,
        error_messages={"invalid": "Tên đăng nhập chứa ký tự không hợp lệ."},
    )
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    password = serializers.CharField(
        min_length=8, max_length=128, write_only=True, trim_whitespace=False
    )
    confirm_password = serializers.CharField(
        max_length=128, write_only=True, trim_whitespace=False
    )

    def validate_username(self, value):
        User = get_user_model()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Tên đăng nhập đã tồn tại.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Mật khẩu xác nhận không khớp."}
            )
        User = get_user_model()
        candidate = User(username=attrs["username"], email=attrs.get("email", ""))
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        User = get_user_model()
        validated_data.pop("confirm_password")
        password = validated_data.pop("password")
        # Operational users may use every staff-gated NewsCrawler workflow but
        # are never superusers and receive no Django model permissions/groups.
        return User.objects.create_user(
            **validated_data,
            password=password,
            is_active=True,
            is_staff=True,
            is_superuser=False,
        )


class OperationalUserStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class WireFilterPromptSerializer(serializers.Serializer):
    prompt = serializers.CharField(
        max_length=MAX_WIRE_FILTER_PROMPT_CHARS,
        trim_whitespace=True,
    )
    favorite_recommendations_enabled = serializers.BooleanField(required=False)

    def validate_prompt(self, value):
        directives = parse_wire_filter_directives(value)
        if not directives.keep and not directives.exclude:
            raise serializers.ValidationError(
                "Chính sách phải có ít nhất một dòng GIỮ: hoặc LOẠI:."
            )
        return value.strip()

class MindmapPromptSerializer(serializers.Serializer):
    prompt = serializers.CharField(max_length=MAX_MINDMAP_PROMPT_CHARS, trim_whitespace=True, min_length=80)

    def validate_prompt(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Prompt Mindmap không được để trống.")
        return value


def _user_payload(user) -> dict:
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email or "",
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "date_joined": user.date_joined,
        # API-token login does not emit Django's user_logged_in signal;
        # prefer the audited successful login timestamp and fall back to
        # the built-in field for legacy accounts.
        "last_login": getattr(user, "_latest_login_at", None) or user.last_login,
    }


def _authenticate_username_password(request, username: str, password: str):
    """Case-sensitive first, then case-insensitive username match (common web UX)."""
    user = authenticate(request, username=username, password=password)
    if user is not None:
        return user
    User = get_user_model()
    match = User.objects.filter(username__iexact=username).first()
    if match and match.username != username:
        return authenticate(request, username=match.username, password=password)
    return None


def _issue_token(user) -> Token:
    with transaction.atomic():
        Token.objects.filter(user=user).delete()
        return Token.objects.create(user=user)


def _request_ip(request) -> str | None:
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR") or "")
    candidate = forwarded.split(",", 1)[0].strip() or str(
        request.META.get("REMOTE_ADDR") or ""
    ).strip()
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None


def _record_login_audit(request, event_type: str, *, username: str, user=None) -> None:
    UserLoginAudit.objects.create(
        user=user,
        username=(username or getattr(user, "username", ""))[:150],
        event_type=event_type,
        ip_address=_request_ip(request),
        user_agent=str(request.META.get("HTTP_USER_AGENT") or "")[:512],
    )


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"]
        password = serializer.validated_data["password"]
        user = _authenticate_username_password(request, username, password)
        if user is None or not user.is_active:
            audit_user = get_user_model().objects.filter(
                username__iexact=username
            ).first()
            _record_login_audit(
                request,
                UserLoginAudit.Event.LOGIN_FAILURE,
                username=username,
                user=audit_user,
            )
            return Response(
                {
                    "detail": "Tên đăng nhập hoặc mật khẩu không đúng.",
                    "code": "invalid_credentials",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Keep the account list's last-login value correct for token-based
        # authentication as well as for the audit history.
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        token = _issue_token(user)
        _record_login_audit(
            request,
            UserLoginAudit.Event.LOGIN_SUCCESS,
            username=user.username,
            user=user,
        )
        ttl = int(getattr(settings, "AUTH_TOKEN_TTL_HOURS", 24) or 24)
        return Response(
            {
                "token": token.key,
                "username": user.username,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "expires_in_hours": ttl,
            }
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _record_login_audit(
            request,
            UserLoginAudit.Event.LOGOUT,
            username=request.user.username,
            user=request.user,
        )
        Token.objects.filter(user=request.user).delete()
        return Response({"status": "logged_out"})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "username": user.username,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
            }
        )


class ChangePasswordView(APIView):
    """Change password for the authenticated user; rotates API token afterward."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response(
                {
                    "detail": "Mật khẩu hiện tại không đúng.",
                    "code": "wrong_current_password",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        token = _issue_token(user)
        ttl = int(getattr(settings, "AUTH_TOKEN_TTL_HOURS", 24) or 24)
        return Response(
            {
                "status": "password_changed",
                "detail": "Đã đổi mật khẩu thành công.",
                "token": token.key,
                "username": user.username,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "expires_in_hours": ttl,
            }
        )


class OperationalUserListCreateView(APIView):
    """Superuser-only management of operational NewsCrawler accounts."""

    permission_classes = [IsSuperUser]
    throttle_scope = "auth"

    def get(self, request):
        User = get_user_model()
        latest_success = UserLoginAudit.objects.filter(
            user=OuterRef("pk"),
            event_type=UserLoginAudit.Event.LOGIN_SUCCESS,
        ).values("occurred_at")[:1]
        users = User.objects.annotate(
            _latest_login_at=Subquery(latest_success),
        ).order_by("-is_superuser", "username")
        return Response({"results": [_user_payload(user) for user in users]})

    def post(self, request):
        serializer = OperationalUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = serializer.save()
            user.groups.clear()
            user.user_permissions.clear()
        return Response(_user_payload(user), status=status.HTTP_201_CREATED)


class OperationalUserDetailView(APIView):
    """Enable/disable an operational account; no delete endpoint by design."""

    permission_classes = [IsSuperUser]
    throttle_scope = "auth"

    def patch(self, request, user_id: int):
        User = get_user_model()
        target = User.objects.filter(pk=user_id).first()
        if target is None:
            return Response(
                {"detail": "Không tìm thấy tài khoản."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if target.is_superuser:
            return Response(
                {"detail": "Không thể thay đổi tài khoản quản trị tại đây."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = OperationalUserStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target.is_active = serializer.validated_data["is_active"]
        # Reassert the security boundary in case legacy data was modified.
        target.is_staff = True
        target.is_superuser = False
        target.save(update_fields=["is_active", "is_staff", "is_superuser"])
        if not target.is_active:
            Token.objects.filter(user=target).delete()
        return Response(_user_payload(target))


def _wire_filter_prompt_payload(
    record,
    *,
    owner=None,
    fallback_prompt: str = "",
    inherited: bool = False,
) -> dict:
    prompt = record.prompt if record is not None else fallback_prompt
    directives = parse_wire_filter_directives(prompt)
    return {
        "prompt": prompt,
        "default_prompt": DEFAULT_WIRE_FILTER_PROMPT,
        "is_default": prompt.strip() == DEFAULT_WIRE_FILTER_PROMPT.strip(),
        "updated_at": record.updated_at if record is not None else None,
        "updated_by": (
            record.updated_by.username
            if record is not None and record.updated_by
            else ""
        ),
        "keep_count": len(directives.keep),
        "exclude_count": len(directives.exclude),
        "owner_id": owner.pk if owner is not None else None,
        "owner_username": owner.username if owner is not None else "Quản trị viên",
        "inherited_from_admin": inherited,
        "scope": "user" if owner is not None else "system",
        "favorite_recommendations_enabled": bool(getattr(record, "favorite_recommendations_enabled", True)) if record is not None else True,
    }


def _mindmap_prompt_payload(record, *, owner=None, fallback_prompt: str = "", inherited: bool = False) -> dict:
    prompt = ((record.mindmap_prompt if record is not None else "") or fallback_prompt).strip()
    inherited = inherited or (owner is not None and not (record and (record.mindmap_prompt or "").strip()))
    return {
        "prompt": prompt,
        "default_prompt": DEFAULT_MINDMAP_PROMPT,
        "is_default": prompt == DEFAULT_MINDMAP_PROMPT.strip(),
        "updated_at": record.mindmap_updated_at if record is not None and record.mindmap_updated_at else None,
        "updated_by": record.mindmap_updated_by.username if record is not None and record.mindmap_updated_by else "",
        "owner_id": owner.pk if owner is not None else None,
        "owner_username": owner.username if owner is not None else "Quản trị viên",
        "inherited_from_admin": inherited,
        "scope": "user" if owner is not None else "system",
        "policy_type": WireFilterPromptRevision.PolicyType.MINDMAP.value,
    }


def _record_policy_revision(record, actor, action: str, *, policy_type=WireFilterPromptRevision.PolicyType.WIRE_FILTER, prompt: str | None = None) -> None:
    owner = record.owner or actor
    WireFilterPromptRevision.objects.create(
        policy=record,
        policy_type=policy_type,
        owner=owner,
        owner_username=getattr(owner, "username", "Quản trị viên"),
        actor=actor,
        action=action,
        prompt=record.prompt if prompt is None else prompt,
    )


class WireFilterPromptView(APIView):
    """Read and edit the current account's policy within its own scope."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def get(self, request):
        record = get_user_wire_filter_prompt_record(request.user)
        owner = None if request.user.is_superuser else request.user
        return Response(_wire_filter_prompt_payload(record, owner=owner))

    def patch(self, request):
        serializer = WireFilterPromptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            record = get_user_wire_filter_prompt_record(request.user)
            record.prompt = serializer.validated_data["prompt"]
            if "favorite_recommendations_enabled" in serializer.validated_data:
                record.favorite_recommendations_enabled = serializer.validated_data["favorite_recommendations_enabled"]
            record.updated_by = request.user
            record.save(update_fields=["prompt", "favorite_recommendations_enabled", "updated_by", "updated_at"])
            _record_policy_revision(
                record, request.user, WireFilterPromptRevision.Action.UPDATE
            )
        if request.user.is_superuser:
            clear_wire_filter_prompt_cache()
        owner = None if request.user.is_superuser else request.user
        return Response(_wire_filter_prompt_payload(record, owner=owner))

    def post(self, request):
        """Reset admin to code default, or a user to the current admin policy."""
        with transaction.atomic():
            record = get_user_wire_filter_prompt_record(request.user)
            if request.user.is_superuser:
                record.prompt = DEFAULT_WIRE_FILTER_PROMPT
                record.favorite_recommendations_enabled = True
            else:
                admin_record = get_wire_filter_prompt_record()
                record.prompt = admin_record.prompt
                record.favorite_recommendations_enabled = admin_record.favorite_recommendations_enabled
            record.updated_by = request.user
            record.save(update_fields=["prompt", "favorite_recommendations_enabled", "updated_by", "updated_at"])
            _record_policy_revision(
                record, request.user, WireFilterPromptRevision.Action.RESET
            )
        if request.user.is_superuser:
            clear_wire_filter_prompt_cache()
        owner = None if request.user.is_superuser else request.user
        return Response(_wire_filter_prompt_payload(record, owner=owner))


class MindmapPromptView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def get(self, request):
        record = get_user_mindmap_prompt_record(request.user)
        owner = None if request.user.is_superuser else request.user
        fallback = get_mindmap_prompt()
        inherited = owner is not None and not (record and (record.mindmap_prompt or "").strip())
        return Response(_mindmap_prompt_payload(record, owner=owner, fallback_prompt=fallback, inherited=inherited))

    def patch(self, request):
        serializer = MindmapPromptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            record = get_user_mindmap_prompt_record(request.user)
            record.mindmap_prompt = serializer.validated_data["prompt"]
            record.mindmap_updated_by = request.user
            record.save(update_fields=["mindmap_prompt", "mindmap_updated_by", "mindmap_updated_at"])
            _record_policy_revision(record, request.user, WireFilterPromptRevision.Action.UPDATE, policy_type=WireFilterPromptRevision.PolicyType.MINDMAP, prompt=record.mindmap_prompt)
        if request.user.is_superuser:
            clear_mindmap_prompt_cache()
        owner = None if request.user.is_superuser else request.user
        return Response(_mindmap_prompt_payload(record, owner=owner))

    def post(self, request):
        with transaction.atomic():
            record = get_user_mindmap_prompt_record(request.user)
            admin_record = get_mindmap_prompt_record()
            record.mindmap_prompt = DEFAULT_MINDMAP_PROMPT if request.user.is_superuser else admin_record.mindmap_prompt
            record.mindmap_updated_by = request.user
            record.save(update_fields=["mindmap_prompt", "mindmap_updated_by", "mindmap_updated_at"])
            _record_policy_revision(record, request.user, WireFilterPromptRevision.Action.RESET, policy_type=WireFilterPromptRevision.PolicyType.MINDMAP, prompt=record.mindmap_prompt)
        if request.user.is_superuser:
            clear_mindmap_prompt_cache()
        owner = None if request.user.is_superuser else request.user
        return Response(_mindmap_prompt_payload(record, owner=owner))


class MindmapPromptAdminReferenceView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def get(self, request):
        payload = _mindmap_prompt_payload(get_mindmap_prompt_record(), fallback_prompt=get_mindmap_prompt())
        payload["read_only"] = True
        return Response(payload)

class WireFilterPromptAdminReferenceView(APIView):
    """Authenticated, read-only view of the administrator's active policy."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def get(self, request):
        payload = _wire_filter_prompt_payload(get_wire_filter_prompt_record())
        payload["read_only"] = True
        return Response(payload)


def _wire_filter_prompt_summary(user, record, admin_prompt: str, admin_mindmap_prompt: str) -> dict:
    payload = _wire_filter_prompt_payload(
        record,
        owner=user,
        fallback_prompt=admin_prompt,
        inherited=record is None,
    )
    payload.pop("prompt", None)
    payload.pop("default_prompt", None)
    mindmap_has_own = bool(record and (record.mindmap_prompt or "").strip())
    payload.update(
        {
            "is_active": user.is_active,
            "email": user.email or "",
            "has_own_policy": record is not None,
            "mindmap_updated_at": record.mindmap_updated_at if mindmap_has_own else None,
            "mindmap_updated_by": record.mindmap_updated_by.username if mindmap_has_own and record.mindmap_updated_by else "",
            "mindmap_inherited_from_admin": not mindmap_has_own,
            "mindmap_prompt_chars": len(record.mindmap_prompt) if mindmap_has_own else len(admin_mindmap_prompt),
        }
    )
    return payload


class WireFilterPromptAdminListView(APIView):
    """Superuser-only summaries of every operational user's current policy."""

    permission_classes = [IsSuperUser]
    throttle_scope = "auth"

    def get(self, request):
        User = get_user_model()
        users = list(User.objects.filter(is_superuser=False).order_by("username", "pk"))
        records = {
            record.owner_id: record
            for record in WireFilterPrompt.objects.filter(
                owner_id__in=[user.pk for user in users]
            ).select_related("updated_by", "mindmap_updated_by")
        }
        admin_prompt = get_wire_filter_prompt_record().prompt
        admin_mindmap_prompt = get_mindmap_prompt()
        return Response(
            {
                "results": [
                    _wire_filter_prompt_summary(
                        user, records.get(user.pk), admin_prompt, admin_mindmap_prompt
                    )
                    for user in users
                ]
            }
        )


class WireFilterPromptAdminDetailView(APIView):
    """Superuser-only, read-only policy detail for one operational user."""

    permission_classes = [IsSuperUser]
    throttle_scope = "auth"

    def get(self, request, user_id: int):
        User = get_user_model()
        user = User.objects.filter(pk=user_id, is_superuser=False).first()
        if user is None:
            return Response(
                {"detail": "Không tìm thấy tài khoản."},
                status=status.HTTP_404_NOT_FOUND,
            )
        record = WireFilterPrompt.objects.filter(owner=user).select_related(
            "updated_by"
        ).first()
        payload = _wire_filter_prompt_payload(record, owner=user, fallback_prompt=get_wire_filter_prompt_record().prompt, inherited=record is None)
        payload["mindmap"] = _mindmap_prompt_payload(record, owner=user, fallback_prompt=get_mindmap_prompt(), inherited=record is None or not (record.mindmap_prompt or "").strip())
        return Response(payload)


class AccountAuditAdminView(APIView):
    """Superuser-only login and policy-change history across all accounts."""

    permission_classes = [IsSuperUser]
    throttle_scope = "auth"

    def get(self, request):
        try:
            user_id = int(request.query_params.get("user_id") or 0) or None
            page_size = max(1, min(int(request.query_params.get("page_size") or 10), 10))
            login_page = max(1, int(request.query_params.get("login_page") or 1))
            policy_page = max(1, int(request.query_params.get("policy_page") or 1))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Tham số phân trang hoặc user_id không hợp lệ."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        User = get_user_model()
        accounts = list(User.objects.order_by("username", "pk"))
        if user_id is not None and not any(user.pk == user_id for user in accounts):
            return Response(
                {"detail": "Không tìm thấy tài khoản."},
                status=status.HTTP_404_NOT_FOUND,
            )

        login_events = UserLoginAudit.objects.select_related("user").order_by("-occurred_at", "-id")
        policy_changes = WireFilterPromptRevision.objects.select_related(
            "owner", "actor"
        ).order_by("-created_at", "-id")
        if user_id is not None:
            login_events = login_events.filter(user_id=user_id)
            policy_changes = policy_changes.filter(owner_id=user_id)

        # Chỉ giữ cửa sổ 100 bản ghi mới nhất; mỗi lần trả tối đa 10 bản ghi.
        recent_login_events = list(login_events[:100])
        recent_policy_changes = list(policy_changes[:100])
        login_total = len(recent_login_events)
        policy_total = len(recent_policy_changes)
        login_pages = max(1, (login_total + page_size - 1) // page_size)
        policy_pages = max(1, (policy_total + page_size - 1) // page_size)
        login_page = min(login_page, login_pages)
        policy_page = min(policy_page, policy_pages)
        login_start = (login_page - 1) * page_size
        policy_start = (policy_page - 1) * page_size

        login_payload = [
            {
                "id": event.pk,
                "user_id": event.user_id,
                "username": event.username,
                "event_type": event.event_type,
                "event_label": event.get_event_type_display(),
                "ip_address": event.ip_address or "",
                "user_agent": event.user_agent,
                "occurred_at": event.occurred_at,
            }
            for event in recent_login_events[login_start : login_start + page_size]
        ]
        policy_payload = []
        for revision in recent_policy_changes[policy_start : policy_start + page_size]:
            directives = parse_wire_filter_directives(revision.prompt) if revision.policy_type == WireFilterPromptRevision.PolicyType.WIRE_FILTER else None
            policy_payload.append(
                {
                    "id": revision.pk,
                    "owner_id": revision.owner_id,
                    "owner_username": revision.owner_username,
                    "actor_username": revision.actor.username if revision.actor else "",
                    "action": revision.action,
                    "action_label": revision.get_action_display(),
                    "policy_type": revision.policy_type,
                    "policy_type_label": revision.get_policy_type_display(),
                    "prompt": revision.prompt,
                    "keep_count": len(directives.keep) if directives else 0,
                    "exclude_count": len(directives.exclude) if directives else 0,
                    "created_at": revision.created_at,
                }
            )

        return Response(
            {
                "accounts": [
                    {
                        "id": user.pk,
                        "username": user.username,
                        "is_active": user.is_active,
                        "is_superuser": user.is_superuser,
                    }
                    for user in accounts
                ],
                "login_events": login_payload,
                "policy_changes": policy_payload,
                "login_pagination": {
                    "page": login_page,
                    "page_size": page_size,
                    "total": login_total,
                    "total_pages": login_pages,
                    "max_records": 100,
                },
                "policy_pagination": {
                    "page": policy_page,
                    "page_size": page_size,
                    "total": policy_total,
                    "total_pages": policy_pages,
                    "max_records": 100,
                },
            }
        )
