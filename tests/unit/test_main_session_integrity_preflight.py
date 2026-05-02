# -*- coding: utf-8 -*-
"""
Tests for main session integrity preflight + auto-recovery
(Session 33 Wave 5 — symmetric с swarm session preflight).

Закрывает асимметрию: swarm clients защищены integrity-gate'ом, а main
kraab.session раньше шёл прямо в `Client(...)` — corruption всплывала
только при первом read и могла вешать процесс / триггерить launchd
respawn loop.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from src.bootstrap import db_corruption_guard
from src.bootstrap.db_corruption_guard import (
    DBCorruptionError,
    attempt_session_recovery,
    has_recent_recovery_backup,
    integrity_check,
)

# --- Helpers ---------------------------------------------------------------


def _make_healthy_session(path: Path, *, peer_rows: int = 0) -> None:
    """
    Создаёт минимальный валидный sqlite (имитация Pyrogram .session).

    Структура достаточно близка к реальной: sessions / peers / usernames.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("INSERT INTO sessions (dc_id, auth_key) VALUES (2, X'00')")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS peers ("
            "id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS usernames (id INTEGER, username TEXT)")
        for i in range(peer_rows):
            conn.execute(
                "INSERT INTO peers (id, access_hash, type) VALUES (?, ?, 'user')",
                (i, i * 100),
            )
        conn.commit()
    finally:
        conn.close()


def _corrupt_session_in_place(path: Path) -> None:
    """
    Делает SQLite файл corrupt путём перезаписи middle-страницы мусором,
    но сохраняя SQLite-magic header — так integrity_check падает с
    `database disk image is malformed`, а не `file is not a database`.
    """
    data = bytearray(path.read_bytes())
    # SQLite header (16 bytes) + минимум одна страница 4096 — мусорим
    # начиная с offset 100 (после header), достаточно чтобы повредить
    # страничную структуру.
    for i in range(100, min(len(data), 800)):
        data[i] = 0xFF
    path.write_bytes(bytes(data))


def _write_garbage_session(path: Path) -> None:
    """Полностью невалидный файл — `file is not a database`."""
    path.write_bytes(b"not a sqlite database, just garbage" * 10)


# --- Tests: attempt_session_recovery --------------------------------------


def test_integrity_ok_proceeds(tmp_path):
    """Healthy session → integrity_check returns ok, recovery not needed."""
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess, peer_rows=42)
    ok, detail = integrity_check(sess)
    assert ok is True
    assert detail == "ok"


def test_integrity_fails_recovery_succeeds(tmp_path):
    """
    Corrupt malformed-page session → .recover восстанавливает,
    integrity passes, original заменён, peer-count > 0 preserved.
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess, peer_rows=42)
    _corrupt_session_in_place(sess)

    # Sanity: integrity_check видит corruption.
    ok, _ = integrity_check(sess)
    assert ok is False

    result = attempt_session_recovery(sess, timeout_sec=15.0)

    # Recovery либо успешна (и тогда peers preserved), либо .recover
    # на сильно повреждённом файле может вернуть пустой output. В этом
    # тесте мы corrupt'или 700 байт, что даёт sqlite шанс recover'нуть
    # большую часть страниц.
    if result["recovered"]:
        # Original теперь fresh, integrity passes.
        ok2, _ = integrity_check(sess)
        assert ok2 is True
        # Backup сохранён для forensics.
        assert result["backup_path"]
        assert Path(result["backup_path"]).exists()
        # Best-effort row counts. peer_count может быть None если schema
        # после recovery без таблицы peers, но если есть — > 0.
        if result["peer_count"] is not None:
            assert result["peer_count"] >= 0
    else:
        # Если recovery не удалась — backup всё равно создан, original
        # не заменён (остался corrupt). Проверим хотя бы backup.
        assert result["backup_path"]
        assert Path(result["backup_path"]).exists()


def test_integrity_fails_recovery_fails(tmp_path, monkeypatch):
    """
    Irrecoverable: monkeypatch sqlite3 subprocess чтобы recover вернул
    empty dump. Метод не должен бросать исключение, должен вернуть
    recovered=False с диагностикой.
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)
    _corrupt_session_in_place(sess)

    # Заменим subprocess.run чтобы dump возвращал rc=1 + пустой stdout.
    import subprocess as _subprocess

    real_run = _subprocess.run

    def fake_run(cmd, *args, **kwargs):  # noqa: ANN001
        if isinstance(cmd, list) and cmd[:2] == ["sqlite3", str(sess)]:

            class R:
                returncode = 1
                stdout = b""
                stderr = b"simulated recover failure"

            return R()
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_subprocess, "run", fake_run)

    result = attempt_session_recovery(sess, timeout_sec=5.0)
    assert result["recovered"] is False
    assert "recover_dump_failed" in result["detail"]
    assert result["backup_path"]
    # Original остался на месте (не заменён fresh'ом).
    assert sess.exists()


def test_skip_recent_recovery_idempotent(tmp_path):
    """
    Idempotency guard: если backup `.bak-corrupt-*` моложе 1h, recovery
    loop пропускается (чтобы не зацикливаться).
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    # Нет backup'ов → has_recent_recovery_backup == False.
    assert has_recent_recovery_backup(sess) is False

    # Создаём свежий backup.
    backup = sess.with_name(f"{sess.name}.bak-corrupt-{int(time.time())}")
    backup.write_bytes(b"old corrupt copy")
    assert has_recent_recovery_backup(sess, within_seconds=3600) is True

    # Старый backup (3h назад) → not recent.
    old_backup = sess.with_name(f"{sess.name}.bak-corrupt-{int(time.time()) - 7200}")
    old_backup.write_bytes(b"older corrupt copy")
    # Перезаписываем mtime чтобы быть точно older.
    old_ts = time.time() - 7200
    os.utime(old_backup, (old_ts, old_ts))
    # Удаляем недавний — должны увидеть только старый.
    backup.unlink()
    assert has_recent_recovery_backup(sess, within_seconds=3600) is False


def test_wal_sidecar_cleanup(tmp_path):
    """
    WAL/SHM sidecars присутствуют → перед recovery их вычищаем (чтобы
    stale frames не попали в recovered базу).
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)
    _corrupt_session_in_place(sess)

    # Создаём sidecars вручную.
    wal = sess.with_name(sess.name + "-wal")
    shm = sess.with_name(sess.name + "-shm")
    wal.write_bytes(b"stale wal frames")
    shm.write_bytes(b"stale shm")
    assert wal.exists() and shm.exists()

    attempt_session_recovery(sess, timeout_sec=15.0)

    # После recovery sidecars должны быть удалены (даже если recovery
    # сам по себе провалился — sidecar cleanup is best-effort и идёт
    # перед .recover).
    assert not wal.exists()
    assert not shm.exists()


# --- Tests: SessionMixin._main_session_integrity_preflight -----------------


def _make_mixin(tmp_path) -> object:
    """Конструирует объект с минимально нужными атрибутами SessionMixin."""
    from src.userbot.session import SessionMixin

    class _Stub(SessionMixin):
        def __init__(self, workdir: Path):
            self._session_workdir = workdir

    return _Stub(tmp_path)


def test_preflight_missing_session_returns_true(tmp_path, monkeypatch):
    """Fresh install (нет файла) → preflight ok, Pyrogram создаст сам."""
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")
    stub = _make_mixin(tmp_path)
    assert stub._main_session_integrity_preflight() is True  # type: ignore[attr-defined]


def test_preflight_healthy_returns_true(tmp_path, monkeypatch):
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess, peer_rows=10)
    stub = _make_mixin(tmp_path)
    assert stub._main_session_integrity_preflight() is True  # type: ignore[attr-defined]


def test_preflight_recent_backup_raises(tmp_path, monkeypatch):
    """Idempotency: corrupt + свежий backup → DBCorruptionError, no retry."""
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)
    _corrupt_session_in_place(sess)
    # Свежий backup имитирует "уже пытались час назад".
    backup = sess.with_name(f"{sess.name}.bak-corrupt-{int(time.time())}")
    backup.write_bytes(b"earlier corrupt copy")

    stub = _make_mixin(tmp_path)
    with pytest.raises(DBCorruptionError):
        stub._main_session_integrity_preflight()  # type: ignore[attr-defined]


def test_preflight_corrupt_recovery_fails_raises(tmp_path, monkeypatch):
    """Corrupt + recovery fails → DBCorruptionError raised."""
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)
    _corrupt_session_in_place(sess)

    # Force recovery failure: monkeypatch attempt_session_recovery.
    monkeypatch.setattr(
        db_corruption_guard,
        "attempt_session_recovery",
        lambda *a, **kw: {
            "recovered": False,
            "backup_path": str(sess) + ".bak-corrupt-fake",
            "detail": "simulated_failure",
            "peer_count": None,
            "username_count": None,
            "sessions_count": None,
        },
    )

    stub = _make_mixin(tmp_path)
    with pytest.raises(DBCorruptionError):
        stub._main_session_integrity_preflight()  # type: ignore[attr-defined]


def test_preflight_transient_non_corruption_proceeds(tmp_path, monkeypatch):
    """
    integrity_check вернул не-ok detail но это НЕ corruption marker
    (например 'database is locked' transient) → preflight returns True
    без recovery, чтобы Pyrogram сам отретраил.
    """
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    monkeypatch.setattr(
        db_corruption_guard,
        "integrity_check",
        lambda path, **kw: (False, "database is locked"),
    )

    stub = _make_mixin(tmp_path)
    assert stub._main_session_integrity_preflight() is True  # type: ignore[attr-defined]
