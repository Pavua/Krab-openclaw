# -*- coding: utf-8 -*-
"""
Unit-тесты для retry-layer над ``SQLiteStorage.update_peers``.

Покрывает Session 32 фикс: до этого был обёрнут только update_usernames, но в
логах появлялось "database disk image is malformed" из update_peers (25 раз
перед restart). Теперь оба метода имеют идентичный graceful-retry.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from src.bootstrap import pyrogram_patch


@pytest.fixture(autouse=True)
def _reset_patch_flag():
    pyrogram_patch._reset_for_tests()
    yield
    pyrogram_patch.apply_pyrogram_sqlite_hardening()


def _make_dummy_storage():
    """Минимальный SQLiteStorage без реального файла."""
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    class _Dummy(SQLiteStorage):
        async def open(self):
            pass

    return _Dummy("x")


def _conn_raising(message: str):
    class _Conn:
        def execute(self, *a, **kw):
            raise sqlite3.OperationalError(message)

        def executemany(self, *a, **kw):
            raise sqlite3.OperationalError(message)

        def executescript(self, *a, **kw):
            raise sqlite3.OperationalError(message)

        def commit(self, *a, **kw):
            raise sqlite3.OperationalError(message)

    return _Conn()


def test_update_peers_swallows_malformed_error():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed")
    result = asyncio.run(dummy.update_peers([(1, 0, "type", "u", "p")]))
    assert result is None


def test_update_peers_swallows_locked_error():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database is locked")
    result = asyncio.run(dummy.update_peers([(1, 0, "type", "u", "p")]))
    assert result is None


def test_update_peers_swallows_table_locked_error():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database table is locked")
    result = asyncio.run(dummy.update_peers([(1, 0, "type", "u", "p")]))
    assert result is None


def test_update_peers_propagates_other_errors():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("no such table: peers")
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(dummy.update_peers([(1, 0, "type", "u", "p")]))


def test_update_peers_handles_empty_list():
    """Защита от len(None) — count извлекается через ``peers or []``."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed")
    # передача None не должна валиться на len() в логе
    result = asyncio.run(dummy.update_peers(None))
    assert result is None


def test_apply_is_idempotent_for_update_peers():
    """Повторный apply не должен re-wrap — флаг _PATCH_APPLIED страхует."""
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    wrapped_first = SQLiteStorage.update_peers
    # Повторный вызов — no-op, метод не должен подмениться повторно.
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    assert SQLiteStorage.update_peers is wrapped_first
