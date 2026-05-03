# -*- coding: utf-8 -*-
"""
Unit-тесты Wave 16-F: corrupt flag механизм + invalidation connection после
malformed swallow в write-path Pyrogram storage.

Сценарий, который покрываем:
- _safe_write_method глотает "database disk image is malformed"
  → ставит storage._corrupt_flag = True
- Следующий READ call (get_peer_by_id и др.) видит _corrupt_flag
  и поднимает sqlite3.DatabaseError("storage marked corrupt")
  вместо того, чтобы взорваться с unexpected crash в pyrogram event loop
- runtime.py поймает этот DatabaseError → trigger recovery cycle
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from src.bootstrap import pyrogram_patch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_patch_flag():
    """Сбрасываем глобальный флаг перед каждым тестом."""
    pyrogram_patch._reset_for_tests()
    yield
    # Восстанавливаем патч после теста, чтобы остальные тесты в сессии не пострадали.
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
    """
    Mock connection где каждый метод поднимает exc_cls(message).

    Поддерживает context manager protocol (`with self.conn:`) — Pyrogram
    использует его в update_state / update_peers. При __exit__ не поднимаем,
    так как exception уже propagated через execute/executemany внутри блока.
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

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            # Не подавляем исключение — пусть propagates.
            return False

    return _Conn()


# ---------------------------------------------------------------------------
# Тест 1: write swallow ставит corrupt flag
# ---------------------------------------------------------------------------


def test_write_swallow_sets_corrupt_flag():
    """После swallow malformed в write-методе _corrupt_flag=True."""
    dummy = _make_dummy_storage()
    assert not pyrogram_patch.is_storage_corrupt(dummy)

    dummy.conn = _conn_raising("database disk image is malformed", exc_cls=sqlite3.DatabaseError)
    asyncio.run(dummy.update_usernames([(1, "alice")]))  # swallowed

    assert pyrogram_patch.is_storage_corrupt(dummy), (
        "_corrupt_flag должен быть True после malformed swallow"
    )


# ---------------------------------------------------------------------------
# Тест 2: read метод поднимает DatabaseError при _corrupt_flag=True
# ---------------------------------------------------------------------------


def test_read_method_raises_when_flag_set():
    """get_peer_by_id поднимает DatabaseError("storage marked corrupt") когда флаг set."""
    dummy = _make_dummy_storage()
    dummy._corrupt_flag = True

    with pytest.raises(sqlite3.DatabaseError, match="storage marked corrupt"):
        asyncio.run(dummy.get_peer_by_id(12345))


# ---------------------------------------------------------------------------
# Тест 3: corrupt flag persists until reset
# ---------------------------------------------------------------------------


def test_corrupt_flag_persists_until_reset():
    """_corrupt_flag остаётся True до явного clear_storage_corrupt_flag."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed", exc_cls=sqlite3.DatabaseError)
    asyncio.run(dummy.update_peers([]))  # swallow → set flag

    # Флаг должен держаться
    assert pyrogram_patch.is_storage_corrupt(dummy)

    # После clear — должен сброситься
    pyrogram_patch.clear_storage_corrupt_flag(dummy)
    assert not pyrogram_patch.is_storage_corrupt(dummy)


# ---------------------------------------------------------------------------
# Тест 4: preflight read sample через is_storage_corrupt
# ---------------------------------------------------------------------------


def test_preflight_detects_corrupt_via_read_sample():
    """
    Имитируем preflight read sample check: после malformed swallow
    is_storage_corrupt возвращает True → preflight должен детектировать corrupt
    без повторного PRAGMA integrity_check.
    """
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed", exc_cls=sqlite3.DatabaseError)
    asyncio.run(dummy.update_state([(1, 2, 3, 4)]))  # swallow → set flag

    # Имитируем logic preflight: проверяем флаг storage
    is_corrupt = pyrogram_patch.is_storage_corrupt(dummy)
    assert is_corrupt, "preflight должен детектировать corruption через is_storage_corrupt"


# ---------------------------------------------------------------------------
# Тест 5: recovery clears corrupt flag after success
# ---------------------------------------------------------------------------


def test_recovery_clears_corrupt_flag_after_success():
    """После успешного recovery clear_storage_corrupt_flag сбрасывает флаг."""
    dummy = _make_dummy_storage()
    dummy._corrupt_flag = True
    assert pyrogram_patch.is_storage_corrupt(dummy)

    # Имитируем recovery: создали новый storage, сбросили флаг
    pyrogram_patch.clear_storage_corrupt_flag(dummy)
    assert not pyrogram_patch.is_storage_corrupt(dummy)

    # READ методы теперь должны пройти без DatabaseError (или поднять KeyError от отсутствующего peer)
    dummy._corrupt_flag = False
    # Убеждаемся что is_storage_corrupt теперь False
    assert not pyrogram_patch.is_storage_corrupt(dummy)


# ---------------------------------------------------------------------------
# Тест 6: двойной corrupt — locked swallow НЕ ставит corrupt flag
# ---------------------------------------------------------------------------


def test_locked_swallow_does_not_set_corrupt_flag():
    """
    "database is locked" swallow НЕ должен ставить _corrupt_flag.
    Только malformed ставит флаг. Locked — transient, не corruption.
    """
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database is locked")
    asyncio.run(dummy.update_usernames([(1, "bob")]))  # swallow locked

    assert not pyrogram_patch.is_storage_corrupt(dummy), (
        "locked swallow не должен ставить corrupt flag — это transient, не corruption"
    )


# ---------------------------------------------------------------------------
# Тест 7: все wrapped read методы защищены через _corrupt_flag
# ---------------------------------------------------------------------------


def test_unwrapped_read_methods_protected_via_corrupt_flag():
    """get_peer_by_username и get_peer_by_phone_number тоже reject при corrupt flag."""
    dummy = _make_dummy_storage()
    dummy._corrupt_flag = True

    with pytest.raises(sqlite3.DatabaseError, match="storage marked corrupt"):
        asyncio.run(dummy.get_peer_by_username("alice"))

    with pytest.raises(sqlite3.DatabaseError, match="storage marked corrupt"):
        asyncio.run(dummy.get_peer_by_phone_number("+79991234567"))


# ---------------------------------------------------------------------------
# Тест 8: storage recreate resets flag (новый объект = чистый флаг)
# ---------------------------------------------------------------------------


def test_storage_recreate_resets_flag():
    """Новый экземпляр SQLiteStorage не имеет corrupt flag по умолчанию."""
    pyrogram_patch.apply_pyrogram_sqlite_hardening()
    from pyrogram.storage.sqlite_storage import SQLiteStorage

    class _Fresh(SQLiteStorage):
        async def open(self):
            pass

    fresh = _Fresh("fresh")
    assert not pyrogram_patch.is_storage_corrupt(fresh), (
        "новый storage должен быть без corrupt flag"
    )


# ---------------------------------------------------------------------------
# Тест 9: is_corrupt_marker_error правильно классифицирует
# ---------------------------------------------------------------------------


def test_is_corrupt_marker_error_classification():
    """is_corrupt_marker_error True только для маркированных DatabaseError."""
    our_exc = sqlite3.DatabaseError(
        "storage marked corrupt — connection invalidated after malformed write"
    )
    other_exc = sqlite3.DatabaseError("database disk image is malformed")
    op_exc = sqlite3.OperationalError("database is locked")

    assert pyrogram_patch.is_corrupt_marker_error(our_exc), "должен вернуть True для нашего маркера"
    assert not pyrogram_patch.is_corrupt_marker_error(other_exc), (
        "должен вернуть False для other malformed"
    )
    assert not pyrogram_patch.is_corrupt_marker_error(op_exc), (
        "должен вернуть False для OperationalError"
    )


# ---------------------------------------------------------------------------
# Тест 10: update_state malformed тоже ставит corrupt flag
# ---------------------------------------------------------------------------


def test_update_state_malformed_sets_corrupt_flag():
    """update_state тоже wrapped — malformed swallow ставит _corrupt_flag."""
    dummy = _make_dummy_storage()
    dummy.conn = _conn_raising("database disk image is malformed", exc_cls=sqlite3.DatabaseError)
    asyncio.run(dummy.update_state([(1, 2, 3, 4)]))

    assert pyrogram_patch.is_storage_corrupt(dummy), (
        "update_state malformed должен поставить corrupt flag"
    )
