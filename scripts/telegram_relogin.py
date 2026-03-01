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


def get_session_dirs() -> list[Path]:
    """Каталоги, где могли лежать файлы сессии (новый + legacy)."""
    primary = config.BASE_DIR / "data" / "sessions"
    dirs = [primary, config.BASE_DIR, config.BASE_DIR / "src", Path.cwd()]
    unique: list[Path] = []
    seen: set[str] = set()
    for item in dirs:
        key = str(item.resolve()) if item.exists() else str(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def purge_session_files(session_name: str) -> list[str]:
    """Удаляет старые файлы сессии перед новым входом."""
    removed: list[str] = []
    for base in get_session_dirs():
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
    session_workdir = config.BASE_DIR / "data" / "sessions"
    session_workdir.mkdir(parents=True, exist_ok=True)
    app = Client(
        session_name,
        api_id=config.TELEGRAM_API_ID,
        api_hash=config.TELEGRAM_API_HASH,
        workdir=str(session_workdir),
    )
    app.start()
    me = app.get_me()
    app.stop()

    print(f"✅ Логин выполнен: @{me.username or 'unknown'} (id={me.id})")
    print("Теперь можно запускать Krab через new start_krab.command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
