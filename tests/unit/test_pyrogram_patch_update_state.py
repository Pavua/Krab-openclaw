# -*- coding: utf-8 -*-
"""
Unit-тесты для retry-layer над ``SQLiteStorage.update_state`` + ``remove_state``
(Wave 14-J). Эти методы вызываются на КАЖДОМ Telegram event'е (PING,
MESSAGE, etc.) — без graceful swallow один corrupted call валит сессию.

Покрываем:
- swallow malformed (write-mode)
- swallow locked (write + table locked)
- propagate other errors
- update_state read-mode возвращает [] на swallow (вместо None)
- идемпотентность apply
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
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    class _Dummy(SQLiteStorage):
        async def open(self):
            pass

    return _Dummy("x")


def _conn_raising(message: str, exc_cls=sqlite3.OperationalError):
    class _Conn:
        def execute(self, *a, **kw):
            raise exc_cls(message)

        def executemany(self, *a, **kw):
            raise exc_cls(message)

        def executescript(self, *a, **kw):
            raise exc_cls(message)

        def commit(self, *a, **kw):
            raise exc_cls(message)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Conn()


# --- update_state ---------------------------------------------------------


def test_update_state_swallows_malformed_write():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed")
    # write-mode (передан tuple)
    result = asyncio.run(dummy.update_state((1, 100, 0, 0, 0)))
    assert result == []  # fallback=list — пустой список


def test_update_state_swallows_locked_write():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database is locked")
    result = asyncio.run(dummy.update_state((1, 100, 0, 0, 0)))
    assert result == []


def test_update_state_swallows_table_locked():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database table is locked")
    result = asyncio.run(dummy.update_state((1, 100, 0, 0, 0)))
    assert result == []


def test_update_state_swallows_malformed_read_returns_empty_list():
    """Read-mode (без аргументов) — fallback=[] (Telegram resync на next event)."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed")
    result = asyncio.run(dummy.update_state())
    assert result == []


def test_update_state_propagates_other_errors():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("no such table: update_state")
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(dummy.update_state((1, 100, 0, 0, 0)))


def test_update_state_swallows_database_error():
    """sqlite3.DatabaseError тоже должен swallow'иться (malformed обычно DBError)."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed", exc_cls=sqlite3.DatabaseError)
    result = asyncio.run(dummy.update_state((1, 100, 0, 0, 0)))
    assert result == []


# --- remove_state ---------------------------------------------------------


def test_remove_state_swallows_malformed():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed")
    result = asyncio.run(dummy.remove_state(42))
    assert result is None


def test_remove_state_swallows_locked():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database is locked")
    result = asyncio.run(dummy.remove_state(42))
    assert result is None


def test_remove_state_propagates_other_errors():
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("no such table: update_state")
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(dummy.remove_state(42))


# --- idempotency / regression --------------------------------------------


def test_apply_is_idempotent_for_update_state():
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    wrapped_first = SQLiteStorage.update_state
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    assert SQLiteStorage.update_state is wrapped_first


def test_factory_preserves_method_name():
    """Wrapped method должен сохранить __name__ для inspect.stack-based pyrogram кода."""
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    assert SQLiteStorage.update_state.__name__ == "update_state"
    assert SQLiteStorage.remove_state.__name__ == "remove_state"
    assert SQLiteStorage.update_peers.__name__ == "update_peers"
    assert SQLiteStorage.update_usernames.__name__ == "update_usernames"


def test_pragmas_include_temp_store_and_cache():
    """Wave 14-J PRAGMA additions: temp_store=MEMORY + cache_size=-65536."""
    import inspect

    src = inspect.getsource(pyrogram_patch._execute_pragmas)
    assert "temp_store=MEMORY" in src
    assert "cache_size=-65536" in src
