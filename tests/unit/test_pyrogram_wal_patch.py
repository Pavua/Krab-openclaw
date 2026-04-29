# -*- coding: utf-8 -*-
"""
Unit-тесты для ``src/bootstrap/pyrogram_patch.py``.

Проверяем:
1. Патч применяется идемпотентно и помечает FileStorage.open как обёрнутый.
2. После вызова patched ``FileStorage.open`` на реальном sqlite-файле
   PRAGMA ``journal_mode`` == ``wal`` и ``busy_timeout`` == 5000.
3. ``update_usernames`` глотает ``sqlite3.OperationalError: database is locked``
   без падения, но реальные ошибки пробрасывает.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.bootstrap import pyrogram_patch


@pytest.fixture(autouse=True)
def _reset_patch_flag():
    """Сбрасываем флаг до каждого теста, чтобы apply_* реально отработал."""
    pyrogram_patch._reset_for_tests()
    yield
    # После тестов оставляем патч применённым — чтобы остальной suite не
    # терял hardening между тестами.
    pyrogram_patch.apply_pyrogram_sqlite_hardening()


def test_apply_is_idempotent():
    assert pyrogram_patch.apply_pyrogram_sqlite_hardening() is True
    assert pyrogram_patch.is_patch_applied() is True
    # Повторный вызов не дублирует wrapping и не падает.
    assert pyrogram_patch.apply_pyrogram_sqlite_hardening() is True
    assert pyrogram_patch.is_patch_applied() is True


def test_pragmas_applied_on_open(tmp_path: Path):
    """После FileStorage.open() sqlite-соединение должно быть в WAL-режиме."""
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("test_session", tmp_path)

    async def _run():
        await storage.open()
        try:
            journal_mode = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = storage.conn.execute("PRAGMA busy_timeout").fetchone()[0]
            return journal_mode, busy_timeout
        finally:
            await storage.close()

    journal_mode, busy_timeout = asyncio.run(_run())
    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 5000


def test_update_usernames_swallows_locked_error():
    """
    ``database is locked`` должен логироваться как warning и НЕ подниматься,
    реальные OperationalError (например, malformed schema) — пробрасываться.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.sqlite_storage import SQLiteStorage

    # Минимальный инстанс: переопределяем open/close, чтобы не трогать файл.
    class _Dummy(SQLiteStorage):
        async def open(self):
            pass

    dummy = _Dummy("x")

    # Симулируем lock через conn, у которого любой вызов бросает OperationalError.
    class _LockedConn:
        def execute(self, *a, **kw):
            raise sqlite3.OperationalError("database is locked")

        def executemany(self, *a, **kw):
            raise sqlite3.OperationalError("database is locked")

        def executescript(self, *a, **kw):
            raise sqlite3.OperationalError("database is locked")

    dummy.conn = _LockedConn()
    # Safe-wrapper должен проглотить locked и вернуть None.
    result = asyncio.run(dummy.update_usernames([(1, "alice")]))
    assert result is None

    # Не-locked OperationalError (schema malformed) — должен пробрасываться.
    class _MalformedConn:
        def execute(self, *a, **kw):
            raise sqlite3.OperationalError("no such table: usernames_malformed")

        def executemany(self, *a, **kw):
            raise sqlite3.OperationalError("no such table: usernames_malformed")

        def executescript(self, *a, **kw):
            raise sqlite3.OperationalError("no such table: usernames_malformed")

    dummy.conn = _MalformedConn()
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(dummy.update_usernames([(1, "bob")]))
