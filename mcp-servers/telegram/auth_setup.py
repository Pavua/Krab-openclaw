#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Одноразовый скрипт авторизации Telegram MCP сессии.

Запустить один раз:
    python mcp-servers/telegram/auth_setup.py

После успешного входа сессия сохраняется в ~/.krab_mcp_sessions/krab_mcp.session
и MCP сервер будет стартовать без интерактивного ввода.
"""
import asyncio
import sys
from pathlib import Path

# Bootstrap путей
_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DIR.parents[1]
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Загружаем .env
_env_path = _PROJECT_ROOT / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path, override=False)

from telegram_bridge import TelegramBridge


async def main() -> None:
    print("=" * 60)
    print("Авторизация Telegram MCP сессии (Краб проект)")
    print("=" * 60)
    print()

    bridge = TelegramBridge()
    try:
        await bridge.start()
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        sys.exit(1)

    # Проверяем что всё работает
    try:
        me = await bridge.client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or str(me.id)
        print(f"\n✅ Авторизован как: {name}")
        print(f"   Username: @{me.username}" if me.username else "   (нет username)")
        print(f"   ID: {me.id}")
    except Exception as e:
        print(f"\n⚠️  Вошли, но не удалось получить профиль: {e}")

    await bridge.stop()
    print("\n✅ Сессия сохранена. Теперь MCP сервер будет стартовать без авторизации.")
    print("   Файл сессии: ~/.krab_mcp_sessions/krab_mcp.session")


if __name__ == "__main__":
    asyncio.run(main())
