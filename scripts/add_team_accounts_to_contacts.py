#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Добавляет 4 swarm team accounts в контакты @p0lrd,
чтобы обойти Telegram "paid messaging" (403 ALLOW_PAYMENT_REQUIRED_X).

Использование:
    cd /Users/pablito/Antigravity_AGENTS/Краб
    PATH=/opt/homebrew/bin:$PATH venv/bin/python scripts/add_team_accounts_to_contacts.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env", override=False)

from pyrogram import Client  # noqa: E402

# Credentials основного Telegram приложения
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Session p0lrd_desktop (MCP-аккаунт на :8012)
SESSION_PATH = str(Path.home() / ".krab_mcp_sessions" / "p0lrd_desktop_mcp")

TEAM_ACCOUNTS = [
    {
        "user_id": 1861168302,
        "first_name": "Traders Swarm",
        "username": "p0lrdp_AI",
        "phone": "+40724455794",
    },
    {
        "user_id": 5929474128,
        "first_name": "Coders Swarm",
        "username": "p0lrdp_worldwide",
        "phone": "+66959272975",
    },
    {
        "user_id": 6539946601,
        "first_name": "Analysts Swarm",
        "username": "hard2boof",
        "phone": "+6282280748457",
    },
    {
        "user_id": 5920778135,
        "first_name": "Creative Swarm",
        "username": "opiodimeo",
        "phone": "+639355619567",
    },
]


async def add_via_high_level(app: Client, acc: dict) -> bool:
    """Попытка через высокоуровневый API add_contact (pyrofork)."""
    try:
        await app.add_contact(
            user_id=acc["user_id"],
            first_name=acc["first_name"],
            share_phone_number=False,
        )
        return True
    except AttributeError:
        # add_contact не доступен в этой версии
        return False
    except Exception as exc:
        print(f"  [high-level] ошибка: {type(exc).__name__}: {exc}")
        return False


async def add_via_raw_import(app: Client, acc: dict) -> bool:
    """Fallback: raw ImportContacts с фиктивным phone (user_id resolving)."""
    from pyrogram.raw.functions.contacts import ImportContacts
    from pyrogram.raw.types import InputPhoneContact

    contact = InputPhoneContact(
        client_id=acc["user_id"],  # произвольный client_id — для дедупликации
        phone=acc["phone"],
        first_name=acc["first_name"],
        last_name="",
    )
    try:
        result = await app.invoke(ImportContacts(contacts=[contact]))
        imported = getattr(result, "imported", [])
        retry = getattr(result, "retry_contacts", [])
        if retry:
            print(f"  [raw] retry_contacts: {retry}")
        return len(imported) > 0 or len(retry) == 0
    except Exception as exc:
        print(f"  [raw] ошибка: {type(exc).__name__}: {exc}")
        return False


async def main() -> None:
    if not API_ID or not API_HASH:
        print("❌ TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы в .env")
        sys.exit(1)

    print(f"Session: {SESSION_PATH}")
    print(f"API_ID:  {API_ID}")
    print()

    successes = 0
    async with Client(SESSION_PATH, api_id=API_ID, api_hash=API_HASH) as app:
        me = await app.get_me()
        print(f"Logged in as: @{me.username} (id={me.id})\n")

        for acc in TEAM_ACCOUNTS:
            print(f"→ {acc['first_name']} (@{acc['username']}, id={acc['user_id']})")

            ok = await add_via_high_level(app, acc)
            if ok:
                print(f"  ✅ добавлен (high-level add_contact)")
                successes += 1
                continue

            # Fallback на raw ImportContacts
            ok = await add_via_raw_import(app, acc)
            if ok:
                print(f"  ✅ добавлен (raw ImportContacts)")
                successes += 1
            else:
                print(f"  ❌ не добавлен")

    print(f"\nИтого: {successes}/{len(TEAM_ACCOUNTS)} добавлено")
    if successes < len(TEAM_ACCOUNTS):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
