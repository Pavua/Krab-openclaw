# -*- coding: utf-8 -*-
"""
Wave 64: тесты для journal_mode=DELETE + fullfsync=1 в Pyrogram session.

Контекст: повторяющийся cluster Sentry corruption events (AGE-15/AGE-12/AGE-9).
Root cause: WAL mode + concurrent writers + macOS sleep/wake → torn pages в WAL.

Решение:
1. journal_mode=DELETE для session.db (single-writer Pyrogram, WAL не нужен).
2. fullfsync=1 (F_FULLFSYNC fcntl) на macOS — защита от lazy disk cache writes.
3. _REQUIRED_TABLES += "version" — Pyrofork update() читает её сразу.

Тесты:
1. После _execute_pragmas journal_mode == 'delete' (а не 'wal').
2. После _execute_pragmas fullfsync == 1.
3. Остальные PRAGMA (busy_timeout, synchronous, cache_size, temp_store) preserved.
4. Новая session создаётся сразу в DELETE mode.
5. Существующая WAL session — переключается в DELETE mode при open().
6. WAL sidecar не появляется после write в DELETE mode.
7. checkpoint_session_wal — no-op (graceful) при DELETE mode.
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
    pyrogram_patch.apply_pyrogram_sqlite_hardening()


def test_journal_mode_delete_applied(tmp_path: Path) -> None:
    """
    После _execute_pragmas journal_mode должен быть 'delete', а не 'wal'.

    Single-writer Pyrogram не нуждается в WAL — DELETE rollback journal не
    оставляет SHM mmap, нет torn-page риска при macOS sleep/wake.
    """
    db_path = tmp_path / "test.session"
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        pyrogram_patch._execute_pragmas(conn)
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "delete", (
            f"Expected 'delete' journal_mode, got {journal_mode!r}"
        )
    finally:
        conn.close()


def test_fullfsync_applied(tmp_path: Path) -> None:
    """
    После _execute_pragmas fullfsync должен быть 1 (на macOS).

    F_FULLFSYNC fcntl форсирует physical disk write — без него macOS APFS
    может задержать запись в block cache и потерять данные при sleep/crash.
    Apple SQLite docs прямо рекомендуют для macOS.
    """
    db_path = tmp_path / "test.session"
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        pyrogram_patch._execute_pragmas(conn)
        row = conn.execute("PRAGMA fullfsync").fetchone()
        # На некоторых SQLite билдах PRAGMA fullfsync может вернуть пусто,
        # если build не support F_FULLFSYNC — в таком случае допускаем None.
        # На macOS должно быть 1.
        if row is not None:
            fullfsync = row[0]
            assert int(fullfsync) == 1, f"Expected fullfsync=1, got {fullfsync}"
    finally:
        conn.close()


def test_existing_pragmas_preserved(tmp_path: Path) -> None:
    """
    Wave 64 не должна сломать ранее работавшие PRAGMA:
    - busy_timeout=5000
    - synchronous=FULL (=2)
    - cache_size=-65536 (64MB negative format)
    - temp_store=MEMORY (=2)
    """
    db_path = tmp_path / "test.session"
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        pyrogram_patch._execute_pragmas(conn)

        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        cache = conn.execute("PRAGMA cache_size").fetchone()[0]
        temp = conn.execute("PRAGMA temp_store").fetchone()[0]

        assert int(busy) == 5000, f"busy_timeout: expected 5000, got {busy}"
        assert int(sync) == 2, f"synchronous: expected 2 (FULL), got {sync}"
        assert int(cache) == -65536, f"cache_size: expected -65536, got {cache}"
        assert int(temp) == 2, f"temp_store: expected 2 (MEMORY), got {temp}"
    finally:
        conn.close()


def test_new_session_in_delete_mode(tmp_path: Path) -> None:
    """
    Новая session, открытая через patched FileStorage.open(), должна быть
    в DELETE mode сразу — без WAL fallback.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("brand_new_session_w64", tmp_path)

    async def _run():
        await storage.open()
        try:
            journal_mode = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            return journal_mode
        finally:
            await storage.close()

    journal_mode = asyncio.run(_run())
    assert str(journal_mode).lower() == "delete", f"Expected DELETE mode, got {journal_mode!r}"


def test_existing_wal_session_migrated_to_delete(tmp_path: Path) -> None:
    """
    Существующая session в WAL mode при следующем open() должна перейти
    в DELETE mode. PRAGMA journal_mode=DELETE персистится в header.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    session_path = tmp_path / "existing_wal.session"
    # Создаём session с явным WAL mode (имитируем pre-Wave-64 state).
    old_conn = sqlite3.connect(str(session_path))
    try:
        old_conn.execute("PRAGMA journal_mode=WAL")
        old_conn.executescript(
            "CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, api_id INTEGER, "
            "test_mode INTEGER, auth_key BLOB, date INTEGER NOT NULL, "
            "user_id INTEGER, is_bot INTEGER);"
            "CREATE TABLE version (number INTEGER PRIMARY KEY);"
            "CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, "
            "type INTEGER NOT NULL, username TEXT, phone_number TEXT, "
            "last_update_on INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE usernames (id INTEGER, username TEXT);"
            "INSERT INTO version VALUES (3);"
        )
        old_conn.commit()
    finally:
        old_conn.close()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("existing_wal", tmp_path)

    async def _run():
        await storage.open()
        try:
            journal_mode = storage.conn.execute("PRAGMA journal_mode").fetchone()[0]
            return journal_mode
        finally:
            await storage.close()

    journal_mode = asyncio.run(_run())
    assert str(journal_mode).lower() == "delete", (
        f"Existing WAL session should migrate to DELETE, got {journal_mode!r}"
    )


def test_no_wal_sidecar_after_write(tmp_path: Path) -> None:
    """
    После write в DELETE mode НЕ должны появляться *-wal или *-shm файлы.

    DELETE mode использует rollback journal, который удаляется после commit.
    Никаких persistent sidecar-файлов между транзакциями быть не должно.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("delete_no_sidecar", tmp_path)
    session_path = tmp_path / "delete_no_sidecar.session"

    async def _run():
        await storage.open()
        try:
            # Делаем тестовую запись чтобы потенциально создать sidecar.
            storage.conn.execute("INSERT OR REPLACE INTO version (number) VALUES (3)")
            storage.conn.commit()
        finally:
            await storage.close()

    asyncio.run(_run())

    wal = session_path.with_name(session_path.name + "-wal")
    shm = session_path.with_name(session_path.name + "-shm")
    assert not wal.exists(), "WAL sidecar should not exist in DELETE mode"
    assert not shm.exists(), "SHM sidecar should not exist in DELETE mode"


def test_data_persists_across_open_close(tmp_path: Path) -> None:
    """
    В DELETE mode commit'нутые данные должны сохраняться между сессиями.

    Rollback journal удаляется при commit (не при close) — данные в main DB
    file сохраняются нормально.
    """
    pyrogram_patch.apply_pyrogram_sqlite_hardening()

    from pyrogram.storage.file_storage import FileStorage

    storage = FileStorage("persist_test", tmp_path)

    async def _write():
        await storage.open()
        try:
            # Используем peers table — pyrofork не дёргает её при update(),
            # данные сохраняются между sessions.
            storage.conn.execute(
                "INSERT OR REPLACE INTO peers "
                "(id, access_hash, type, username, phone_number, last_update_on) "
                "VALUES (123, 456, 0, 'test_user', NULL, 0)"
            )
            storage.conn.commit()
        finally:
            await storage.close()

    asyncio.run(_write())

    # Открываем заново и проверяем что данные на месте.
    storage2 = FileStorage("persist_test", tmp_path)

    async def _read():
        await storage2.open()
        try:
            row = storage2.conn.execute(
                "SELECT username FROM peers WHERE id=123 LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        finally:
            await storage2.close()

    persisted = asyncio.run(_read())
    assert persisted == "test_user", f"DELETE mode must preserve data, got {persisted!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
