"""Expiring DRF token authentication (short-lived API credentials)."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication


class ExpiringTokenAuthentication(TokenAuthentication):
    """
    TokenAuthentication with TTL + light sliding renewal.

    Default TTL: AUTH_TOKEN_TTL_HOURS (24).
    Expired / missing tokens raise stable machine codes for the SPA.
    """

    keyword = "Token"

    def authenticate_credentials(self, key):
        model = self.get_model()
        try:
            token = model.objects.select_related("user").get(key=key)
        except model.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed(
                {
                    "detail": "Phiên đăng nhập không còn hiệu lực. Vui lòng đăng nhập lại.",
                    "code": "session_invalid",
                }
            ) from exc

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(
                {
                    "detail": "Tài khoản đã bị vô hiệu hóa.",
                    "code": "user_inactive",
                }
            )

        ttl_hours = int(getattr(settings, "AUTH_TOKEN_TTL_HOURS", 24) or 24)
        if ttl_hours > 0:
            age = timezone.now() - token.created
            if age > timedelta(hours=ttl_hours):
                token.delete()
                raise exceptions.AuthenticationFailed(
                    {
                        "detail": "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
                        "code": "session_expired",
                    }
                )
            # Sliding session: renew after 25% of TTL so active users stay signed in.
            renew_after = timedelta(hours=max(1.0, ttl_hours * 0.25))
            if age > renew_after:
                model.objects.filter(pk=token.pk).update(created=timezone.now())
                token.refresh_from_db(fields=["created"])

        return (token.user, token)
