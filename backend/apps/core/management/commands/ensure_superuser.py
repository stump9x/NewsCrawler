"""Create the bootstrap superuser from environment variables (once).

Password is set only when the account is created. Later UI password changes
must stick across backend restarts — do not re-sync from DJANGO_SUPERUSER_PASSWORD
unless DJANGO_SUPERUSER_FORCE_PASSWORD_SYNC=1.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Ensure DJANGO_SUPERUSER_* exists. Password is applied on create only "
        "(or when DJANGO_SUPERUSER_FORCE_PASSWORD_SYNC=1)."
    )

    def handle(self, *args, **options):
        username = (getattr(settings, "DJANGO_SUPERUSER_USERNAME", "") or "").strip()
        email = (getattr(settings, "DJANGO_SUPERUSER_EMAIL", "") or "").strip()
        password = getattr(settings, "DJANGO_SUPERUSER_PASSWORD", "") or ""
        force_sync = bool(
            getattr(settings, "DJANGO_SUPERUSER_FORCE_PASSWORD_SYNC", False)
        )
        if not username or not password:
            self.stdout.write(
                "ensure_superuser skipped (set DJANGO_SUPERUSER_USERNAME and "
                "DJANGO_SUPERUSER_PASSWORD)"
            )
            return

        User = get_user_model()
        user = User.objects.filter(username__iexact=username).first()
        created = False
        if user is None:
            user = User.objects.create_superuser(
                username=username,
                email=email or f"{username}@localhost",
                password=password,
            )
            created = True
        else:
            changed = False
            if force_sync and not user.check_password(password):
                user.set_password(password)
                changed = True
            if email and user.email != email:
                user.email = email
                changed = True
            if not user.is_staff or not user.is_superuser or not user.is_active:
                user.is_staff = True
                user.is_superuser = True
                user.is_active = True
                changed = True
            if user.username != username:
                user.username = username
                changed = True
            if changed:
                user.save()

        if created:
            action = "created"
        elif force_sync:
            action = "synced (force password)"
        else:
            action = "ensured (password unchanged)"
        self.stdout.write(self.style.SUCCESS(f"superuser {action}: {user.username}"))
