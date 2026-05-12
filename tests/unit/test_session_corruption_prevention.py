# -*- coding: utf-8 -*-
"""
Integration-level тесты для предотвращения corruption сессий Pyrogram.

Покрытие (src/bootstrap/pyrogram_patch.py):
1. test_session_opens_in_wal_mode        — после patched open journal_mode=WAL
2. test_session_has_busy_timeout         — busy_timeout >= 5000ms
3. test_vacuum_not_called_on_open        — VACUUM не вызывается в patched open
4. test_session_survives_unclean_shutdown — данные выживают после краша без close()
5. test_concurrent_open_with_busy_timeout — concurrent доступ не вызывает "database is locked"

Дополнительно:
6. test_patch_idempotent                 — двойной apply не ломает
7. test_wal_mode_persists_on_reopen      — WAL режим остаётся при повторном открытии
8. test_synchronous_normal_set           — synchronous=NORMAL выставлен
9. test_execute_pragmas_order_busy_timeout_first — порядок PRAGMA корректен
10. test_patched_open_uses_long_timeout  — connect использует timeout=10 (не 1)
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from src.bootstrap import pyrogram_patch as pp

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_patch_flag():
    """Сбрасывает флаг патча между тестами и восстанавливает оригинальные методы."""
    pp._reset_for_tests()
    from pyrogram.storage import file_storage as _fs
    from pyrogram.storage import sqlite_storage as _ss

    orig_open = _fs.FileStorage.open
    orig_update_usernames = _ss.SQLiteStorage.update_usernames
    yield
    _fs.FileStorage.open = orig_open
    _ss.SQLiteStorage.update_usernames = orig_update_usernames
    pp._reset_for_tests()


@pytest.fixture()
def tmp_session_dir(tmp_path):
    """Временная директория для .session файлов."""
    return tmp_path


def _make_file_storage(name: str, workdir: pathlib.Path):
    """Создаёт FileStorage с заданным именем и директорией."""
    from pyrogram.storage import file_storage as _fs

    return _fs.FileStorage(name, workdir)


def _run_open(storage) -> None:
    """Запускает storage.open() в event loop."""
    asyncio.run(storage.open())


# ---------------------------------------------------------------------------
# 1. WAL mode после patched open
# ---------------------------------------------------------------------------


def test_session_opens_in_wal_mode(tmp_session_dir):
    """После patched open, journal_mode должен быть WAL."""
    pp.apply_pyrogram_sqlite_hardening()
    storage = _make_file_storage("test_wal", tmp_session_dir)
    _run_open(storage)

    try:
        row = storage.conn.execute("PRAGMA journal_mode").fetchone()
        assert row is not None
        assert row[0].upper() == "WAL", f"Ожидался WAL, получен: {row[0]}"
    finally:
        storage.conn.close()


# ---------------------------------------------------------------------------
# 2. busy_timeout >= 5000ms
# ---------------------------------------------------------------------------


def test_session_has_busy_timeout(tmp_session_dir):
    """После patched open, busy_timeout должен быть >= 5000ms."""
    pp.apply_pyrogram_sqlite_hardening()
    storage = _make_file_storage("test_busy", tmp_session_dir)
    _run_open(storage)

    try:
        row = storage.conn.execute("PRAGMA busy_timeout").fetchone()
        assert row is not None
        timeout_ms = row[0]
        assert timeout_ms >= 5000, (
            f"busy_timeout={timeout_ms}ms, ожидалось >= 5000ms"
        )
    finally:
        storage.conn.close()


# ---------------------------------------------------------------------------
# 3. VACUUM не вызывается при открытии
# ---------------------------------------------------------------------------


def test_vacuum_not_called_on_open(tmp_session_dir):
    """
    Patched open не должен вызывать VACUUM.
    VACUUM требует exclusive lock и несовместим с WAL sidecar-файлами.

    Стратегия: создаём реальное соединение, оборачиваем в proxy-объект
    который перехватывает execute() — sqlite3.Connection является immutable
    C-типом и не поддаётся patch.object.
    """
    pp.apply_pyrogram_sqlite_hardening()

    vacuum_calls = []
    real_conns = []
    original_connect = sqlite3.connect

    class TrackingConnection:
        """Proxy вокруг sqlite3.Connection, отслеживающий VACUUM вызовы."""

        def __init__(self, real_conn):
            self._conn = real_conn

        def execute(self, sql, *args, **kwargs):
            sql_upper = sql.strip().upper()
            if sql_upper == "VACUUM" or sql_upper.startswith("VACUUM "):
                vacuum_calls.append(sql)
            return self._conn.execute(sql, *args, **kwargs)

        def executescript(self, script):
            return self._conn.executescript(script)

        def commit(self):
            return self._conn.commit()

        def close(self):
            return self._conn.close()

        def __enter__(self):
            return self._conn.__enter__()

        def __exit__(self, *args):
            return self._conn.__exit__(*args)

        def cursor(self):
            return self._conn.cursor()

    def tracking_connect(database, *args, **kwargs):
        real_conn = original_connect(database, *args, **kwargs)
        proxy = TrackingConnection(real_conn)
        real_conns.append(real_conn)
        return proxy

    storage = _make_file_storage("test_vacuum", tmp_session_dir)

    with patch("sqlite3.connect", side_effect=tracking_connect):
        _run_open(storage)

    # Закрываем реальное соединение (proxy делегирует)
    if hasattr(storage.conn, "_conn"):
        storage.conn._conn.close()
    elif hasattr(storage.conn, "close"):
        try:
            storage.conn.close()
        except Exception:
            pass

    assert len(vacuum_calls) == 0, (
        f"VACUUM был вызван {len(vacuum_calls)} раз(а): {vacuum_calls}"
    )


# ---------------------------------------------------------------------------
# 4. Данные выживают после unclean shutdown (краш без close)
# ---------------------------------------------------------------------------


def test_session_survives_unclean_shutdown(tmp_session_dir):
    """
    Сессия переживает краш без close():
    1. Открываем сессию через patched open (WAL mode).
    2. Записываем данные.
    3. Симулируем краш — не вызываем conn.close().
    4. Переоткрываем — данные должны быть на месте.

    В WAL режиме WAL-файлы (.wal/.shm) при повторном открытии
    автоматически проигрываются и данные восстанавливаются.
    """
    pp.apply_pyrogram_sqlite_hardening()

    # Первое открытие
    storage = _make_file_storage("test_crash", tmp_session_dir)
    _run_open(storage)

    # Записываем данные (используем таблицу peers из схемы pyrogram)
    storage.conn.execute(
        "INSERT OR REPLACE INTO peers (id, access_hash, type, username, phone_number) "
        "VALUES (?, ?, ?, ?, ?)",
        (12345678, 987654321, "user", "testuser", None),
    )
    storage.conn.commit()

    # Симулируем unclean shutdown: НЕ закрываем соединение
    # (WAL sidecar-файлы могут остаться на диске)
    del storage  # только удаляем объект без явного close

    # Переоткрываем
    storage2 = _make_file_storage("test_crash", tmp_session_dir)
    _run_open(storage2)

    try:
        row = storage2.conn.execute(
            "SELECT id, access_hash, username FROM peers WHERE id = ?",
            (12345678,),
        ).fetchone()
        assert row is not None, "Данные потеряны после переоткрытия (unclean shutdown)"
        assert row[0] == 12345678
        assert row[1] == 987654321
        assert row[2] == "testuser"
    finally:
        storage2.conn.close()


# ---------------------------------------------------------------------------
# 5. Concurrent доступ с busy_timeout — нет "database is locked"
# ---------------------------------------------------------------------------


def test_concurrent_open_with_busy_timeout(tmp_session_dir):
    """
    Два соединения к одной сессии не вызывают OperationalError("database is locked").
    WAL режим позволяет одновременные читающие + один пишущий.
    busy_timeout даёт запас времени при коллизиях.
    """
    pp.apply_pyrogram_sqlite_hardening()

    # Создаём сессию
    storage_a = _make_file_storage("test_concurrent", tmp_session_dir)
    _run_open(storage_a)

    # Открываем второе соединение к тому же файлу через patched open
    storage_b = _make_file_storage("test_concurrent", tmp_session_dir)
    _run_open(storage_b)

    errors = []

    def write_peer(conn, peer_id):
        try:
            for _ in range(10):
                conn.execute(
                    "INSERT OR REPLACE INTO peers (id, access_hash, type, username, phone_number) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (peer_id, peer_id * 10, "user", f"user_{peer_id}", None),
                )
                conn.commit()
        except Exception as exc:
            errors.append(str(exc))

    t1 = threading.Thread(target=write_peer, args=(storage_a.conn, 111))
    t2 = threading.Thread(target=write_peer, args=(storage_b.conn, 222))

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    storage_a.conn.close()
    storage_b.conn.close()

    # При WAL + busy_timeout не должно быть ошибок "database is locked"
    locked_errors = [e for e in errors if "database is locked" in e.lower()]
    assert len(locked_errors) == 0, (
        f"Получены ошибки 'database is locked': {locked_errors}"
    )


# ---------------------------------------------------------------------------
# 6. Идемпотентность патча
# ---------------------------------------------------------------------------


def test_patch_idempotent():
    """Повторный apply_pyrogram_sqlite_hardening — no-op, не ломает."""
    result1 = pp.apply_pyrogram_sqlite_hardening()
    result2 = pp.apply_pyrogram_sqlite_hardening()
    assert result1 is True
    assert result2 is True
    assert pp.is_patch_applied() is True


# ---------------------------------------------------------------------------
# 7. WAL режим сохраняется при повторном открытии (persists in file header)
# ---------------------------------------------------------------------------


def test_wal_mode_persists_on_reopen(tmp_session_dir):
    """
    WAL режим записывается в заголовок файла SQLite и
    сохраняется при последующих открытиях (даже без патча).
    """
    pp.apply_pyrogram_sqlite_hardening()

    # Первое открытие через патч
    storage = _make_file_storage("test_wal_persist", tmp_session_dir)
    _run_open(storage)
    db_path = str(storage.database)
    storage.conn.close()

    # Открываем тот же файл напрямую через sqlite3 (без патча)
    conn2 = sqlite3.connect(db_path)
    try:
        row = conn2.execute("PRAGMA journal_mode").fetchone()
        assert row[0].upper() == "WAL", (
            f"WAL не сохранился в заголовке файла: journal_mode={row[0]}"
        )
    finally:
        conn2.close()


# ---------------------------------------------------------------------------
# 8. synchronous=NORMAL выставлен
# ---------------------------------------------------------------------------


def test_synchronous_normal_set(tmp_session_dir):
    """После patched open, synchronous должен быть FULL (=2).

    Wave 6-A: bumped from NORMAL to FULL для atomic write guarantee.
    Защищает от torn pages при power-loss / kernel-panic. 12+ часов
    uptime при synchronous=NORMAL + WAL + macOS sleep cycle = corruption.
    """
    pp.apply_pyrogram_sqlite_hardening()
    storage = _make_file_storage("test_sync", tmp_session_dir)
    _run_open(storage)

    try:
        row = storage.conn.execute("PRAGMA synchronous").fetchone()
        assert row is not None
        # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
        assert row[0] == 2, f"synchronous={row[0]}, ожидалось 2 (FULL, Wave 6-A)"
    finally:
        storage.conn.close()


# ---------------------------------------------------------------------------
# 9. Порядок PRAGMA: busy_timeout первым
# ---------------------------------------------------------------------------


def test_execute_pragmas_order_busy_timeout_first():
    """
    _execute_pragmas применяет PRAGMA в правильном порядке:
    1. busy_timeout (чтобы сами PRAGMA не падали на lock)
    2. journal_mode=WAL
    3. synchronous=NORMAL
    """
    pragma_order = []
    conn = MagicMock()

    def tracking_execute(sql, *args, **kwargs):
        pragma_order.append(sql.strip())
        return MagicMock()

    conn.execute = tracking_execute

    pp._execute_pragmas(conn)

    # Wave 14-J: расширили set до 6 PRAGMA — busy_timeout / journal_mode /
    # synchronous / wal_autocheckpoint / temp_store / cache_size.
    assert len(pragma_order) == 6
    assert "busy_timeout" in pragma_order[0].lower(), (
        f"busy_timeout должен быть первым, получено: {pragma_order[0]}"
    )
    assert "journal_mode" in pragma_order[1].lower(), (
        f"journal_mode должен быть вторым, получено: {pragma_order[1]}"
    )
    assert "synchronous" in pragma_order[2].lower(), (
        f"synchronous должен быть третьим, получено: {pragma_order[2]}"
    )


# ---------------------------------------------------------------------------
# 10. Patched open использует длинный timeout (10s, не 1s)
# ---------------------------------------------------------------------------


def test_patched_open_uses_long_timeout(tmp_session_dir):
    """
    Patched open использует timeout=10 (не оригинальный timeout=1).
    Проверяем через перехват sqlite3.connect.
    """
    pp.apply_pyrogram_sqlite_hardening()
    storage = _make_file_storage("test_timeout", tmp_session_dir)

    captured_kwargs = {}
    original_connect = sqlite3.connect

    def tracking_connect(database, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return original_connect(database, *args, **kwargs)

    with patch("sqlite3.connect", side_effect=tracking_connect):
        _run_open(storage)

    storage.conn.close()

    timeout_used = captured_kwargs.get("timeout", None)
    assert timeout_used is not None, "sqlite3.connect был вызван без timeout"
    assert timeout_used >= 5, (
        f"Ожидался timeout >= 5s (фиксим pyrofork timeout=1), получено: {timeout_used}"
    )


# ---------------------------------------------------------------------------
# 11. _execute_pragmas не крашит при ошибке одной PRAGMA
# ---------------------------------------------------------------------------


def test_execute_pragmas_tolerates_single_failure():
    """
    Если одна PRAGMA упала — остальные всё равно применяются.
    Фейл одной PRAGMA не должен ломать весь старт.
    """
    call_count = {"n": 0}
    conn = MagicMock()

    def partial_fail(sql, *args, **kwargs):
        call_count["n"] += 1
        if "journal_mode" in sql.lower():
            raise sqlite3.OperationalError("simulated pragma failure")
        return MagicMock()

    conn.execute = partial_fail

    # Не должно поднимать исключение
    pp._execute_pragmas(conn)

    # Wave 14-J: 6 PRAGMA должны были попробоваться (не 3 как раньше).
    assert call_count["n"] == 6


# ---------------------------------------------------------------------------
# 12. Patched open создаёт новую сессию если файла нет
# ---------------------------------------------------------------------------


def test_patched_open_creates_new_session(tmp_session_dir):
    """
    Patched open вызывает create() при отсутствии файла — схема создаётся.
    """
    pp.apply_pyrogram_sqlite_hardening()
    storage = _make_file_storage("brand_new", tmp_session_dir)

    session_file = tmp_session_dir / "brand_new.session"
    assert not session_file.exists(), "Файл сессии уже существует до open()"

    _run_open(storage)

    try:
        assert session_file.exists(), "Файл сессии не создан после open()"
        # Схема создана — таблица sessions должна существовать
        tables = [
            row[0]
            for row in storage.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "sessions" in tables, f"Таблица sessions отсутствует: {tables}"
        assert "peers" in tables, f"Таблица peers отсутствует: {tables}"
    finally:
        storage.conn.close()


# ---------------------------------------------------------------------------
# 13. Patched open открывает существующую сессию (update path)
# ---------------------------------------------------------------------------


def test_patched_open_opens_existing_session(tmp_session_dir):
    """
    Patched open корректно открывает уже существующий файл сессии
    (вызывает update(), не create(), не теряет данные).
    """
    pp.apply_pyrogram_sqlite_hardening()

    # Создаём сессию
    storage = _make_file_storage("existing", tmp_session_dir)
    _run_open(storage)
    storage.conn.execute(
        "INSERT OR REPLACE INTO peers (id, access_hash, type, username, phone_number) "
        "VALUES (?, ?, ?, ?, ?)",
        (42, 100, "user", "existing_user", None),
    )
    storage.conn.commit()
    storage.conn.close()

    # Переоткрываем — должны увидеть данные
    storage2 = _make_file_storage("existing", tmp_session_dir)
    _run_open(storage2)

    try:
        row = storage2.conn.execute(
            "SELECT username FROM peers WHERE id = ?", (42,)
        ).fetchone()
        assert row is not None, "Данные потеряны при переоткрытии существующей сессии"
        assert row[0] == "existing_user"
    finally:
        storage2.conn.close()
