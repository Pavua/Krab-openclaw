# -*- coding: utf-8 -*-
"""
Тесты corruption-aware preflight для swarm team session-файлов
(Session 32 P1 — fix unconditional WAL/journal unlink в `_start_swarm_team_clients`).

Проверяет, что новый код:
- Сохраняет .session-wal / .session-journal, когда integrity_check вернул ok
  (uncheckpointed peer-cache writes из предыдущего запуска не теряются).
- Удаляет .session-wal / .session-journal, когда DB malformed / integrity провален.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.bootstrap.db_corruption_guard import integrity_check


def _make_healthy_session(path: Path) -> None:
    """Создаёт минимальную валидную SQLite БД (имитация Pyrogram .session)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("INSERT INTO sessions (dc_id, auth_key) VALUES (2, X'00')")
        conn.commit()
    finally:
        conn.close()


def _make_healthy_session_with_uncheckpointed_wal(path: Path) -> sqlite3.Connection:
    """
    Создаёт healthy session + оставляет uncheckpointed WAL рядом.

    Возвращает open-connection — caller обязан закрыть в конце теста.
    Пока connection открыт, SQLite держит .session-wal на диске; integrity_check
    из read-only режима проходит успешно (видит valid frames в WAL).
    """
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (dc_id INTEGER, auth_key BLOB)")
    conn.execute("INSERT INTO sessions (dc_id, auth_key) VALUES (2, X'00')")
    conn.commit()
    # Не закрываем — оставляем WAL на диске, чтобы воспроизвести сценарий
    # "previous Krab run died with uncheckpointed peer-cache writes".
    return conn


def _make_corrupt_session(path: Path) -> None:
    """Записывает мусор по пути .session — sqlite3.connect/integrity_check провалятся."""
    path.write_bytes(b"this is not a sqlite database, just garbage bytes" * 10)


def _swarm_preflight(sess_path: Path, team: str = "traders") -> dict:
    """
    Имитирует обновлённый блок preflight из `_start_swarm_team_clients`.
    Возвращает dict с полями {wal_present, journal_present, integrity_ok, detail}.
    """
    journal = sess_path.with_suffix(".session-journal")
    wal = sess_path.with_suffix(".session-wal")

    if not sess_path.exists():
        return {
            "wal_present": wal.exists(),
            "journal_present": journal.exists(),
            "integrity_ok": True,
            "detail": "session_missing",
        }

    ok, detail = integrity_check(sess_path)
    if not ok:
        for lockf in (journal, wal):
            if lockf.exists():
                try:
                    lockf.unlink()
                except OSError:
                    pass
    return {
        "wal_present": wal.exists(),
        "journal_present": journal.exists(),
        "integrity_ok": ok,
        "detail": detail,
    }


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def test_wal_preserved_when_integrity_ok(workdir: Path) -> None:
    """integrity_check ok → WAL и journal sidecars НЕ должны быть удалены."""
    sess_path = workdir / "swarm_traders.session"
    holder = _make_healthy_session_with_uncheckpointed_wal(sess_path)
    try:
        wal = sess_path.with_suffix(".session-wal")
        assert wal.exists(), "precondition: SQLite WAL-mode должен оставить .session-wal"
        wal_size_before = wal.stat().st_size

        result = _swarm_preflight(sess_path)

        assert result["integrity_ok"] is True, f"expected ok, got {result}"
        assert result["wal_present"] is True, "WAL must be preserved when integrity ok"
        # Размер WAL не уменьшился (не было unlink)
        assert wal.stat().st_size == wal_size_before
    finally:
        holder.close()


def test_wal_deleted_when_db_malformed(workdir: Path) -> None:
    """integrity_check failed (DB corrupt) → WAL и journal удаляются."""
    sess_path = workdir / "swarm_coders.session"
    _make_corrupt_session(sess_path)

    wal = sess_path.with_suffix(".session-wal")
    journal = sess_path.with_suffix(".session-journal")
    wal.write_bytes(b"stale_wal")
    journal.write_bytes(b"stale_journal")

    result = _swarm_preflight(sess_path)

    assert result["integrity_ok"] is False
    assert result["wal_present"] is False, "WAL must be deleted when DB malformed"
    assert result["journal_present"] is False, "journal must be deleted when DB malformed"


def test_session_missing_no_op(workdir: Path) -> None:
    """Нет .session файла → ничего не делаем (early return path)."""
    sess_path = workdir / "swarm_analysts.session"
    # файл не создаём
    result = _swarm_preflight(sess_path)
    assert result["detail"] == "session_missing"
    assert result["integrity_ok"] is True
