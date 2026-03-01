#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Интерактивный relogin Telegram-сессии для Krab.

Зачем:
- После удаления/протухания *.session Pyrogram может требовать повторную авторизацию.
- Этот скрипт запускает чистый вход и сразу сохраняет новую сессию.
"""

from __future__ import annotations

from pathlib import Path

from pyrogram import Client

from src.config import config


def purge_session_files(session_name: str) -> list[str]:
    """Удаляет старые файлы сессии перед новым входом."""
    base = Path.cwd()
    removed: list[str] = []
    for suffix in (".session", ".session-journal", ".session-shm", ".session-wal"):
        target = base / f"{session_name}{suffix}"
        if target.exists():
            target.unlink()
            removed.append(str(target))
    return removed


def main() -> int:
    session_name = str(config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
    removed = purge_session_files(session_name)
    if removed:
        print(f"Удалены старые сессии: {len(removed)}")

    print("Запускаю интерактивный вход Telegram...")
    app = Client(
        session_name,
        api_id=config.TELEGRAM_API_ID,
        api_hash=config.TELEGRAM_API_HASH,
    )
    app.start()
    me = app.get_me()
    app.stop()

    print(f"✅ Логин выполнен: @{me.username or 'unknown'} (id={me.id})")
    print("Теперь можно запускать Krab через new start_krab.command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
