# -*- coding: utf-8 -*-
"""
Unit-тесты для malformed-handling в retry-layer ``SQLiteStorage.update_usernames``.

Покрывает Session 33 фикс: до этого _safe_update_usernames глотал только
"database is locked" / "database table is locked", а malformed re-raise.
Это привело к крашу boot path (get_me → fetch_peers → update_usernames)
утром 02.05 (+199 corruption events за 12h из-за infinite restart loop).

Симметричное malformed-handling с _safe_update_peers (Session 32 фикс,
который для update_peers это уже сделал).
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


def _conn_raising(message: str, exc_cls=sqlite3.OperationalError):
    """Mock connection где every method raises ``exc_cls(message)``.

    Default — OperationalError (locked / table is locked / readonly variants).
    Для malformed real Pyrogram чаще бросает DatabaseError — pass exc_cls=DatabaseError.
    """

    class _Conn:
        def execute(self, *a, **kw):
            raise exc_cls(message)

        def executemany(self, *a, **kw):
            raise exc_cls(message)

        def executescript(self, *a, **kw):
            raise exc_cls(message)

        def commit(self, *a, **kw):
            raise exc_cls(message)

    return _Conn()


def test_update_usernames_swallows_malformed_error():
    """Главная регрессия: malformed теперь NOT raise."""
    dummy = _make_dummy_storage()
    # Real Pyrogram throws DatabaseError для disk image malformed (boot path).
    dummy.conn = _conn_raising(
        "database disk image is malformed", exc_cls=sqlite3.DatabaseError
    )
    result = asyncio.run(dummy.update_usernames([(1, "alice")]))
    assert result is None


def test_update_usernames_swallows_locked_error():
    """Сохранили старое поведение: locked всё ещё swallowed."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database is locked")
    result = asyncio.run(dummy.update_usernames([(1, "alice")]))
    assert result is None


def test_update_usernames_swallows_table_locked_error():
    """Tabla locked variant тоже handled."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database table is locked")
    result = asyncio.run(dummy.update_usernames([(1, "alice")]))
    assert result is None


def test_update_usernames_propagates_other_errors():
    """Прочие OperationalError должны re-raise (не глотаем slienkly)."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("attempt to write a readonly database")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        asyncio.run(dummy.update_usernames([(1, "alice")]))


def test_update_usernames_empty_list_resilience():
    """update_usernames(None) не должен падать в нашем guard."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising(
        "database disk image is malformed", exc_cls=sqlite3.DatabaseError
    )
    # None / [] — должен спокойно вернуть None
    result = asyncio.run(dummy.update_usernames(None))
    assert result is None


def test_idempotent_apply_does_not_double_wrap():
    """Повторный apply_pyrogram_sqlite_hardening не должен re-wrap."""
    pyrogram_patch._reset_for_tests()
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    first = SQLiteStorage.update_usernames
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    second = SQLiteStorage.update_usernames
    assert first is second
