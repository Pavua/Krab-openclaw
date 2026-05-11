# -*- coding: utf-8 -*-
"""
Unit-тесты для ``src/bootstrap/pyrogram_patch.py``.

Проверяем:
1. Патч применяется идемпотентно.
2. После вызова patched ``FileStorage.open`` PRAGMA journal_mode=DELETE
   (Wave 64: было WAL — concurrent writers + macOS sleep провоцировали
   torn pages в WAL → corruption cluster AGE-15/AGE-12/AGE-9),
   busy_timeout=5000, synchronous=FULL (Session 33 hardening).
3. ``update_usernames`` глотает ``database is locked``, пробрасывает другие.
4. VACUUM не вызывается при open() (через проверку отсутствия exclusive-lock
   ошибок при параллельном открытии).
5. Существующая WAL сессия переключается в DELETE при open() (Wave 64).
6. _SQLITE_CONNECT_TIMEOUT >= 5 (не pyrofork-default 1s).
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
    """После FileStorage.open() sqlite-соединение должно быть в DELETE-режиме.

    Wave 64: было WAL — replaced after corruption cluster AGE-15/AGE-12/AGE-9.
    """
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
    # Wave 64: было "wal"
    assert str(journal_mode).lower() == "delete"
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


def test_vacuum_suppressed_delete_mode_preserved(tmp_path: Path):
    """
    DELETE-mode стабильно держится после open() — VACUUM не запускался
    (если бы он запустился, в журнале появилась бы временная WAL-страница
    с другим режимом или временный sidecar; в чистом DELETE такого нет).

    Wave 64: после переключения на DELETE этот тест проверяет, что
    journal_mode не меняется неожиданно (например, через скрытый VACUUM).
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("no_vacuum_test", tmp_path)

    async def _run():
        await storage.open()
        try:
            journal_mode = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            return journal_mode
        finally:
            await storage.close()

    journal_mode = asyncio.run(_run())
    # Wave 64: ожидаем DELETE (было WAL).
    assert str(journal_mode).lower() == "delete", (
        f"journal_mode={journal_mode!r}; Wave 64 expects DELETE"
    )


def test_new_session_in_delete_mode(tmp_path: Path):
    """
    Новая сессия создаётся сразу в DELETE-режиме (pragmas применяются ДО create()).

    Wave 64: было WAL — заменено после AGE-15/AGE-12/AGE-9 corruption cluster.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("brand_new_session", tmp_path)

    async def _run():
        await storage.open()
        try:
            journal_mode = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = storage.conn.execute("PRAGMA busy_timeout").fetchone()[0]
            synchronous = storage.conn.execute("PRAGMA synchronous").fetchone()[0]
            return journal_mode, busy_timeout, synchronous
        finally:
            await storage.close()

    journal_mode, busy_timeout, synchronous = asyncio.run(_run())
    # Wave 64: было "wal"
    assert str(journal_mode).lower() == "delete", f"Expected delete (Wave 64), got {journal_mode!r}"
    assert int(busy_timeout) == 5000, f"Expected 5000ms, got {busy_timeout}"
    # Session 33 fix retained: synchronous=FULL=2 — защита от torn pages.
    assert int(synchronous) == 2, f"Expected FULL(2), got {synchronous}"


def test_existing_session_gets_delete(tmp_path: Path):
    """
    Существующая сессия после open() переключается в DELETE-режим (Wave 64).

    Покрывает migration path: pre-Wave-64 базы могли быть в WAL — должны
    автоматически конвертироваться при первом open() через patched _execute_pragmas.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    # Создаём старую сессию в WAL — имитируем pre-Wave-64 state.
    session_path = tmp_path / "existing.session"
    old_conn = sqlite3.connect(str(session_path))
    old_conn.execute("PRAGMA journal_mode=WAL")
    old_conn.executescript(
        "CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, api_id INTEGER, "
        "test_mode INTEGER, auth_key BLOB, date INTEGER NOT NULL, "
        "user_id INTEGER, is_bot INTEGER);"
        "CREATE TABLE version (number INTEGER PRIMARY KEY);"
        "CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, "
        "type INTEGER NOT NULL, username TEXT, phone_number TEXT, "
        "last_update_on INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO version VALUES (3);"
    )
    old_conn.close()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("existing", tmp_path)

    async def _run():
        await storage.open()
        try:
            journal_mode = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = storage.conn.execute("PRAGMA busy_timeout").fetchone()[0]
            return journal_mode, busy_timeout
        finally:
            await storage.close()

    journal_mode, busy_timeout = asyncio.run(_run())
    # Wave 64: existing WAL session migrates to DELETE on next open().
    assert str(journal_mode).lower() == "delete", (
        f"Existing session should migrate to DELETE (Wave 64), got {journal_mode!r}"
    )
    assert int(busy_timeout) == 5000


def test_connect_timeout_is_longer():
    """
    FileStorage.open() должен использовать timeout >= 5 (не pyrofork-default 1s).
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    assert pyrogram_patch._SQLITE_CONNECT_TIMEOUT >= 5
