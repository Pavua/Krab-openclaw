# -*- coding: utf-8 -*-
"""
Wave 64: тесты на добавление "version" в _REQUIRED_TABLES.

Контекст: Pyrofork.update() сначала читает из таблицы ``version``. Если
sqlite3 .recover не сохранит её (corrupted version row), recovery пройдёт
verify_key_tables, но Pyrofork сразу упадёт на следующем open(). Поэтому
version обязана быть в required tables list.

Тесты:
1. _REQUIRED_TABLES содержит 'version'.
2. verify_key_tables FAIL если version отсутствует.
3. verify_key_tables OK если version присутствует.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.bootstrap.session_recovery import _REQUIRED_TABLES, verify_key_tables


def test_required_tables_includes_version() -> None:
    """
    _REQUIRED_TABLES должна включать 'version' — Pyrofork.update() читает её
    немедленно при open(). Recovery без version валит pyrofork.
    """
    assert "version" in _REQUIRED_TABLES, (
        f"'version' must be in _REQUIRED_TABLES, got {_REQUIRED_TABLES}"
    )
    # Остальные обязательные таблицы тоже должны остаться.
    for required in ("sessions", "peers", "usernames"):
        assert required in _REQUIRED_TABLES, f"'{required}' must remain in _REQUIRED_TABLES"


def test_verify_key_tables_fails_when_version_missing(tmp_path: Path) -> None:
    """
    Recovered файл БЕЗ version table должен fail verify_key_tables —
    защита от silently incomplete recovery.
    """
    db = tmp_path / "no_version.session"
    conn = sqlite3.connect(str(db))
    try:
        # Все таблицы кроме version
        conn.execute("CREATE TABLE sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE usernames (id INTEGER, username TEXT)")
        conn.commit()
    finally:
        conn.close()

    ok, detail = verify_key_tables(db)
    assert ok is False, f"verify_key_tables should fail when version missing; detail={detail}"
    assert "version" in detail.lower(), f"detail should mention 'version'; got: {detail}"


def test_verify_key_tables_passes_with_all_required(tmp_path: Path) -> None:
    """
    Полный set таблиц (sessions/peers/usernames/version) → verify проходит.
    """
    db = tmp_path / "complete.session"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE usernames (id INTEGER, username TEXT)")
        conn.execute("CREATE TABLE version (number INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO version VALUES (3)")
        conn.commit()
    finally:
        conn.close()

    ok, detail = verify_key_tables(db)
    assert ok is True, f"verify_key_tables should pass; detail={detail}"


def test_verify_key_tables_explicit_tables_override(tmp_path: Path) -> None:
    """
    Override через tables= параметр работает (для backward compat).
    """
    db = tmp_path / "custom.session"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE only_one (x INTEGER)")
        conn.commit()
    finally:
        conn.close()

    # С override только на "only_one" — должен пройти.
    ok, detail = verify_key_tables(db, tables=("only_one",))
    assert ok is True, f"should pass with explicit single-table override: {detail}"
