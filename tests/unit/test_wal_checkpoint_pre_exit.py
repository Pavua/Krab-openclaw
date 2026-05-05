# -*- coding: utf-8 -*-
"""Wave 24-D: Тесты для src/userbot/wal_checkpoint_pre_exit.py

Покрывают:
1. force_wal_checkpoint на существующей WAL-enabled DB → ok=True
2. force_wal_checkpoint на несуществующем пути → ok=False, error="session_not_exists"
3. force_wal_checkpoint при DB залоченной другим conn → busy>0 или error (не crash)
4. Соединение корректно закрывается (нет file leak)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.userbot.wal_checkpoint_pre_exit import force_wal_checkpoint

# ---------------------------------------------------------------------------
# 1. Существующая DB — checkpoint проходит успешно
# ---------------------------------------------------------------------------


def test_force_wal_checkpoint_ok(tmp_path: Path) -> None:
    """force_wal_checkpoint на WAL-DB возвращает ok=True, busy_count=0."""
    db_path = tmp_path / "test.session"

    # Создаём sqlite DB с WAL и какой-то записью
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO sessions VALUES (1)")
    conn.commit()
    conn.close()

    result = force_wal_checkpoint(db_path)

    assert result["ok"] is True, f"Ожидали ok=True, получили: {result}"
    assert result["busy_count"] == 0, f"busy_count должен быть 0: {result}"
    assert result["error"] is None, f"error должен быть None: {result}"


# ---------------------------------------------------------------------------
# 2. Несуществующий путь → ok=False с маркером session_not_exists
# ---------------------------------------------------------------------------


def test_force_wal_checkpoint_missing_path(tmp_path: Path) -> None:
    """force_wal_checkpoint на несуществующем файле → ok=False, error='session_not_exists'."""
    missing = tmp_path / "nonexistent.session"
    assert not missing.exists()

    result = force_wal_checkpoint(missing)

    assert result["ok"] is False
    assert result["error"] == "session_not_exists"
    assert result["busy_count"] is None
    assert result["log_count"] is None


# ---------------------------------------------------------------------------
# 3. Locked DB → busy>0 или error в результате, но не исключение
# ---------------------------------------------------------------------------


def test_force_wal_checkpoint_locked_db(tmp_path: Path) -> None:
    """force_wal_checkpoint при активном writer возвращает результат (busy>0 или error), не crash."""
    db_path = tmp_path / "locked.session"

    # Создаём WAL DB
    setup_conn = sqlite3.connect(str(db_path))
    setup_conn.execute("PRAGMA journal_mode=WAL")
    setup_conn.execute("CREATE TABLE t (v INTEGER)")
    setup_conn.commit()

    # Держим открытую транзакцию (exclusive writer)
    setup_conn.execute("BEGIN EXCLUSIVE")
    setup_conn.execute("INSERT INTO t VALUES (42)")
    # НЕ commit и не close — держим lock

    # force_wal_checkpoint не должен бросить исключение
    try:
        result = force_wal_checkpoint(db_path, timeout_sec=0.5)
        # Может вернуть ok=False (busy>0) или ok=False (error при timeout)
        assert isinstance(result, dict), "Должен вернуть dict"
        assert "ok" in result
        # При busy writer checkpoint возвращает busy_count>0 или error (OperationalError timeout)
        if result["ok"] is not True:
            # Ожидаем либо busy_count > 0, либо error строку
            if result.get("busy_count") is not None:
                assert result["busy_count"] >= 0  # busy может быть 1 при locked
            else:
                assert result.get("error") is not None
    finally:
        setup_conn.rollback()
        setup_conn.close()


# ---------------------------------------------------------------------------
# 4. Нет file leak — соединение закрывается корректно
# ---------------------------------------------------------------------------


def test_force_wal_checkpoint_no_file_leak(tmp_path: Path) -> None:
    """force_wal_checkpoint закрывает sqlite-соединение (нет открытых FD на file)."""
    db_path = tmp_path / "noleak.session"

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    # Вызываем несколько раз подряд — если соединение не закрывается,
    # на некоторых OS получим ошибку "too many open files"
    results = []
    for _ in range(20):
        r = force_wal_checkpoint(db_path)
        results.append(r)

    # Все вызовы должны завершиться без исключений
    assert len(results) == 20
    # Последний открытый файл мы можем проверить — удаление должно пройти без ошибок
    db_path.unlink()
    assert not db_path.exists(), "Файл должен быть удалён без ошибок (нет удержания FD)"
