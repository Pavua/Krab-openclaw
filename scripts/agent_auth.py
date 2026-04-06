#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/agent_auth.py
~~~~~~~~~~~~~~~~~~~~~
Интерактивная авторизация Telegram аккаунтов для agent-команд свёрма.

Использование:
    python scripts/agent_auth.py --agent traders
    python scripts/agent_auth.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Добавляем src в path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_CONFIG_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "agent_accounts.json"
_SESSION_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "sessions"


async def auth_agent(agent_id: str, api_id: int, api_hash: str) -> None:
    """Авторизует один agent-аккаунт интерактивно."""
    from pyrogram import Client

    if not _CONFIG_PATH.exists():
        print(f"❌ Конфиг не найден: {_CONFIG_PATH}")
        print("Создайте agent_accounts.json сначала.")
        sys.exit(1)

    data = json.loads(_CONFIG_PATH.read_text())
    if agent_id not in data:
        print(f"❌ Агент '{agent_id}' не найден в конфиге.")
        print(f"Доступные: {list(data.keys())}")
        sys.exit(1)

    cfg = data[agent_id]
    session_name = cfg["session_name"]
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n🔐 Авторизация агента: {agent_id}")
    print(f"   Session: {session_name}")
    print(f"   Dir: {_SESSION_DIR}")

    client = Client(
        session_name,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(_SESSION_DIR),
    )

    await client.start()
    me = await client.get_me()
    print(f"\n✅ Авторизован: {me.first_name} (@{me.username or '?'}) [id={me.id}]")
    await client.stop()
    print(f"   Session сохранён: {_SESSION_DIR / f'{session_name}.session'}")


def list_agents() -> None:
    """Показывает список агентов и статус их сессий."""
    if not _CONFIG_PATH.exists():
        print(f"❌ Конфиг не найден: {_CONFIG_PATH}")
        return

    data = json.loads(_CONFIG_PATH.read_text())
    print(f"\n📋 Agent-аккаунты ({_CONFIG_PATH}):\n")
    for agent_id, cfg in data.items():
        session_name = cfg["session_name"]
        has_session = (_SESSION_DIR / f"{session_name}.session").exists()
        status = "✅" if has_session else "❌"
        role = cfg.get("role", "agent")
        print(f"  {status} {agent_id:12s} session={session_name:20s} role={role}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Авторизация agent-аккаунтов Краба")
    parser.add_argument("--agent", help="ID агента для авторизации (traders/coders/...)")
    parser.add_argument("--list", action="store_true", help="Показать список агентов")
    parser.add_argument("--api-id", type=int, help="Telegram API ID (или env TELEGRAM_API_ID)")
    parser.add_argument("--api-hash", help="Telegram API Hash (или env TELEGRAM_API_HASH)")
    args = parser.parse_args()

    if args.list:
        list_agents()
        return

    if not args.agent:
        parser.print_help()
        return

    api_id = args.api_id or int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = args.api_hash or os.getenv("TELEGRAM_API_HASH", "")

    if not api_id or not api_hash:
        print("❌ Нужны TELEGRAM_API_ID и TELEGRAM_API_HASH (аргументы или env)")
        sys.exit(1)

    asyncio.run(auth_agent(args.agent, api_id, api_hash))


if __name__ == "__main__":
    main()
