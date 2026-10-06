"""Interactive account login. Secrets are entered only in the user's terminal."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.workers.telegram_account import INVITE_HASH, account_directory, write_private_json


class Command(BaseCommand):
    help = "Connect the Telegram account already joined to the configured VLC group."

    def handle(self, *args, **options):
        if not sys.stdin.isatty():
            raise CommandError("Use an interactive terminal: docker compose exec backend python manage.py telegram_login")
        root = account_directory()
        config_path = root / "config.json"
        existing = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        self.stdout.write("Telegram API credentials: https://my.telegram.org/apps")
        api_id = existing.get("api_id") or input("API ID: ").strip()
        api_hash = decrypt_secret(existing.get("api_hash", "")) or getpass("API Hash (hidden): ").strip()
        if not str(api_id).isdigit() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise CommandError("API ID must be numeric and API Hash must contain 32 hexadecimal characters.")
        try:
            config = asyncio.run(self._login(int(api_id), api_hash, existing))
        except KeyboardInterrupt:
            raise CommandError("Login cancelled; no source was enabled.") from None
        except CommandError:
            raise
        except Exception as error:
            raise CommandError(f"Telegram login failed ({type(error).__name__}). Retry in this terminal.") from None
        write_private_json(config_path, config)
        from apps.workers.tasks import ingest_telegram_group
        result = ingest_telegram_group.delay()
        self.stdout.write(self.style.SUCCESS(f"Connected group: {config['group_title']}"))
        self.stdout.write(f"Initial scan queued: {result.id}. Further scans run every two minutes.")

    async def _login(self, api_id: int, api_hash: str, existing: dict) -> dict:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
        from telethon.sessions import StringSession
        from telethon.tl.functions.messages import CheckChatInviteRequest
        from telethon.tl.types import ChatInviteAlready, Channel

        session = decrypt_secret(existing.get("session", ""))
        client = TelegramClient(StringSession(session), api_id, api_hash, receive_updates=False)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                phone = getpass("Telegram phone number, e.g. +84... (hidden): ").strip()
                if not re.fullmatch(r"\+\d{7,15}", phone):
                    raise CommandError("Use the international phone-number format, starting with +.")
                sent = await client.send_code_request(phone)
                for attempt in range(3):
                    code = getpass("Telegram login code (hidden): ").replace(" ", "").strip()
                    try:
                        await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
                        break
                    except SessionPasswordNeededError:
                        await client.sign_in(password=getpass("Telegram 2FA password (hidden): "))
                        break
                    except PhoneCodeInvalidError:
                        if attempt == 2:
                            raise
                        self.stdout.write("Invalid code; try again.")
            me = await client.get_me()
            if getattr(me, "bot", False):
                raise CommandError("This source requires a Telegram user account.")
            invite = await client(CheckChatInviteRequest(INVITE_HASH))
            if not isinstance(invite, ChatInviteAlready):
                raise CommandError("This account has not joined the specified group. Join it in Telegram and retry.")
            group = invite.chat
            if isinstance(group, Channel) and getattr(group, "access_hash", None) is None:
                raise CommandError("Telegram did not return access to this group. Open it in Telegram and retry.")
            return {
                "enabled": True, "api_id": api_id,
                "api_hash": encrypt_secret(api_hash),
                "session": encrypt_secret(client.session.save()),
                "group_kind": "channel" if isinstance(group, Channel) else "chat",
                "group_id": group.id,
                "access_hash": getattr(group, "access_hash", None),
                "group_title": group.title,
            }
        finally:
            await client.disconnect()
