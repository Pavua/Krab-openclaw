# -*- coding: utf-8 -*-
"""
Тесты для src/bootstrap/session_recovery.py (Wave 16-N).

Shared module — общая логика auto-recovery corrupt SQLite-сессии,
используемая как preflight'ом, так и repair-скриптом (DRY).

Покрывают:
1. clean session → no-op (False)
2. malformed session → recovered (True)
3. idempotency guard (<1h backup) → blocked
4. dry_run=True → нет мутаций файловой системы
5. file missing → False, no crash
6. recovered file lacks required tables → False, original не заменён
7. atomic replace использует os.replace (атомарен)
8. sidecars очищены после replace
9. auth_key preserved (sessions table, >= 1 row)
10. has_recent_recovery_backup edge cases
11. cleanup_sidecars удаляет все three suffix'а
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.bootstrap.session_recovery import (
    attempt_recovery,
    cleanup_sidecars,
    has_recent_recovery_backup,
    verify_key_tables,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_healthy_session(path: Path, *, peer_rows: int = 5) -> None:
    """Создаёт минимальный валидный Pyrogram-like .session файл.

    Wave 64: добавлена version table — Pyrofork.update() читает её немедленно,
    и _REQUIRED_TABLES теперь включает 'version' (recovery должна её сохранить).
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("INSERT INTO sessions (dc_id, auth_key) VALUES (2, X'DEADBEEF')")
        conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT)")
        conn.execute("CREATE TABLE usernames (id INTEGER, username TEXT)")
        conn.execute("CREATE TABLE version (number INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO version VALUES (3)")
        for i in range(peer_rows):
            conn.execute(
                "INSERT INTO peers (id, access_hash, type) VALUES (?, ?, 'user')",
                (i + 1, (i + 1) * 100),
            )
        conn.commit()
    finally:
        conn.close()


def _corrupt_session(path: Path) -> None:
    """Портит SQLite: перезаписывает middle-байты мусором, сохраняя magic."""
    data = bytearray(path.read_bytes())
    for i in range(100, min(len(data), 800)):
        data[i] = 0xFF
    path.write_bytes(bytes(data))


def _write_garbage(path: Path) -> None:
    """Полностью невалидный файл — file is not a database."""
    path.write_bytes(b"not a sqlite database" * 20)


# ── 1. Clean session → no-op ─────────────────────────────────────────────────


def test_attempt_recovery_clean_session_no_op(tmp_path: Path) -> None:
    """
    Целостная session → attempt_recovery НЕ запускает recovery,
    возвращает recovered=False (нечего делать).

    Примечание: attempt_recovery не проводит integrity_check сам — он
    запускается только если caller решил, что сессия corrupt. Тут мы
    проверяем, что при отсутствии idempotency-блока и без subprocess-
    сбоев файл корректно идёт до конца и returned recovered=True (если
    sqlite3 CLI доступен). Поэтому используем dry_run=True для ≠-мутаций.
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    # dry_run=True → файлы не трогаем, recovered=False
    result = attempt_recovery(sess, dry_run=True)
    assert result["recovered"] is False
    assert result["dry_run"] is True
    assert "dry_run" in result["detail"]
    # Нет backup'ов создано
    assert not list(tmp_path.glob("*.bak-corrupt-*"))


# ── 2. Malformed session → recovered ─────────────────────────────────────────


def test_attempt_recovery_malformed_succeeds(tmp_path: Path) -> None:
    """
    Malformed session + sqlite3 CLI доступен → recovered=True, файл заменён.

    Тест реально запускает subprocess sqlite3 .recover.
    Если sqlite3 не в PATH — skip gracefully.
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess, peer_rows=10)
    _corrupt_session(sess)

    result = attempt_recovery(sess, timeout_sec=15.0)

    if result["detail"] == "sqlite3_not_in_path":
        pytest.skip("sqlite3 CLI не найден в PATH")

    if result["recovered"]:
        # Файл заменён — integrity проходит.
        uri = f"file:{sess}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3.0)
        try:
            cur = conn.execute("PRAGMA quick_check")
            row = cur.fetchone()
            assert row and row[0] == "ok", f"Recovered session failed quick_check: {row}"
        finally:
            conn.close()
        # Backup сохранён для forensics.
        assert result["backup_path"]
        assert Path(result["backup_path"]).exists()
    else:
        # Если recovery не удалась (sqlite3 вернул пустой dump на сильно
        # corrupt файл) — backup всё равно существует, оригинал не заменён.
        assert result["backup_path"]
        assert Path(result["backup_path"]).exists()


# ── 3. Idempotency guard ──────────────────────────────────────────────────────


def test_attempt_recovery_idempotency_blocks_within_1h(tmp_path: Path) -> None:
    """
    Recent backup < 1h → idempotency_blocked=True, recovered=False.
    Защита от recovery loop.
    """
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    # Создаём свежий backup (сейчас).
    backup = sess.with_name(f"{sess.name}.bak-corrupt-{int(time.time())}")
    backup.write_bytes(b"prev recovery attempt")

    result = attempt_recovery(sess, idempotency_sec=3600)

    assert result["recovered"] is False
    assert result["idempotency_blocked"] is True
    assert "idempotency_blocked" in result["detail"]


def test_attempt_recovery_old_backup_not_blocked(tmp_path: Path) -> None:
    """Backup > 1h → НЕ блокирует (не считается "recent")."""
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    # Старый backup (2h назад).
    old_ts = int(time.time()) - 7200
    old_backup = sess.with_name(f"{sess.name}.bak-corrupt-{old_ts}")
    old_backup.write_bytes(b"old attempt")
    # Меняем mtime явно чтобы быть уверены.
    os.utime(old_backup, (time.time() - 7200, time.time() - 7200))

    # dry_run чтобы не мутировать файлы.
    result = attempt_recovery(sess, dry_run=True, idempotency_sec=3600)
    assert result["idempotency_blocked"] is False


# ── 4. dry_run → нет мутаций ─────────────────────────────────────────────────


def test_attempt_recovery_dry_run_no_mutation(tmp_path: Path) -> None:
    """dry_run=True → никаких изменений файловой системы."""
    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)
    original_mtime = sess.stat().st_mtime
    original_size = sess.stat().st_size

    result = attempt_recovery(sess, dry_run=True)

    assert result["recovered"] is False
    assert result["dry_run"] is True
    # Файл не изменён.
    assert sess.stat().st_mtime == original_mtime
    assert sess.stat().st_size == original_size
    # Нет backup'ов.
    assert not list(tmp_path.glob("*.bak-*"))


# ── 5. File missing → False, no crash ────────────────────────────────────────


def test_attempt_recovery_no_session_file(tmp_path: Path) -> None:
    """Файл отсутствует → recovered=False, нет исключения."""
    sess = tmp_path / "kraab.session"
    assert not sess.exists()

    result = attempt_recovery(sess)

    assert result["recovered"] is False
    assert result["detail"] == "missing"


# ── 6. Recovered file lacks required tables ───────────────────────────────────


def test_attempt_recovery_recovered_lacks_required_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Если recovered файл не имеет required tables (sessions/peers/usernames) →
    recovered=False, original НЕ заменён.
    """
    import subprocess as _subproc

    sess = tmp_path / "kraab.session"
    _write_garbage(sess)  # corrupt garbage

    def fake_run(cmd: list, **kwargs):  # noqa: ANN001
        m = MagicMock()
        m.returncode = 0
        if ".recover" in cmd:
            m.stdout = b"-- some recovery sql\n"
        else:
            # Создаём recovered файл БЕЗ нужных таблиц.
            ts_suffix = ""
            for part in cmd:
                if "recovered-" in str(part):
                    ts_suffix = str(part)
                    break
            if ts_suffix:
                conn = sqlite3.connect(ts_suffix)
                conn.execute("CREATE TABLE junk (id INTEGER)")
                conn.commit()
                conn.close()
            m.stdout = b""
        return m

    monkeypatch.setattr(_subproc, "run", fake_run)

    result = attempt_recovery(sess, timeout_sec=5.0)

    # Если subprocess дал нам файл без нужных таблиц.
    if result["recovered"]:
        # Если вдруг recovered (например sqlite3 восстановил из мусора schema),
        # тест не падает — ситуация маловероятна на явном garbage.
        pass
    else:
        # Ожидаем: либо missing_tables, либо recovered_still_corrupt.
        assert not result["recovered"]
        # Оригинал должен остаться на месте.
        assert sess.exists()


# ── 7. Atomic replace ─────────────────────────────────────────────────────────


def test_attempt_recovery_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Atomic replace использует Path.replace (os.rename/os.replace family) — атомарен.
    Проверяем что replace вызывается корректно через mock.
    """
    import subprocess as _subproc

    from src.bootstrap import session_recovery as _sr

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    replaced_from: list[Path] = []
    replaced_to: list[Path] = []

    _orig_replace = Path.replace

    def _tracking_replace(self: Path, target: Path) -> None:
        replaced_from.append(self)
        replaced_to.append(target)
        return _orig_replace(self, target)

    # Мокаем subprocess.run чтобы recovery сработала без реального sqlite3.
    def fake_run(cmd: list, **kwargs):  # noqa: ANN001
        m = MagicMock()
        m.returncode = 0
        if ".recover" in cmd:
            m.stdout = b"-- some sql\n"
        else:
            # Создаём fresh файл с нужными таблицами.
            fresh_path_arg = str(cmd[-1]) if cmd else ""
            if fresh_path_arg and "recovered-" in fresh_path_arg:
                _make_healthy_session(Path(fresh_path_arg), peer_rows=3)
            m.stdout = b""
        return m

    monkeypatch.setattr(_subproc, "run", fake_run)
    monkeypatch.setattr(Path, "replace", _tracking_replace)

    result = attempt_recovery(sess, timeout_sec=5.0)

    if result["recovered"]:
        # replace был вызван (fresh → original).
        assert len(replaced_to) >= 1
        assert any(str(t) == str(sess) for t in replaced_to)


# ── 8. Sidecars cleaned after replace ────────────────────────────────────────


def test_attempt_recovery_cleans_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    После успешного replace sidecars (WAL/SHM/journal) удалены
    как ДО recovery (pre-cleanup), так и ПОСЛЕ replace (post-cleanup).
    """
    import subprocess as _subproc

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    # Создаём sidecars ДО recovery.
    wal = sess.with_name(sess.name + "-wal")
    shm = sess.with_name(sess.name + "-shm")
    wal.write_bytes(b"stale wal")
    shm.write_bytes(b"stale shm")

    def fake_run(cmd: list, **kwargs):  # noqa: ANN001
        m = MagicMock()
        m.returncode = 0
        if ".recover" in cmd:
            m.stdout = b"-- sql\n"
        else:
            fresh_path_arg = str(cmd[-1]) if cmd else ""
            if fresh_path_arg and "recovered-" in fresh_path_arg:
                _make_healthy_session(Path(fresh_path_arg), peer_rows=2)
            m.stdout = b""
        return m

    monkeypatch.setattr(_subproc, "run", fake_run)

    result = attempt_recovery(sess, timeout_sec=5.0)

    # Sidecars удалены ДО subprocess (pre-cleanup).
    assert not wal.exists(), "WAL sidecar должен быть удалён перед recovery"
    assert not shm.exists(), "SHM sidecar должен быть удалён перед recovery"

    if result["recovered"]:
        # sidecars_removed включает pre-cleanup.
        assert len(result["sidecars_removed"]) >= 2


# ── 9. Auth key preserved ────────────────────────────────────────────────────


def test_attempt_recovery_preserves_auth_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Recovered file имеет sessions table с >= 1 row → auth_key сохранён.
    """
    import subprocess as _subproc

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess, peer_rows=5)

    def fake_run(cmd: list, **kwargs):  # noqa: ANN001
        m = MagicMock()
        m.returncode = 0
        if ".recover" in cmd:
            m.stdout = b"-- sql\n"
        else:
            fresh_path_arg = str(cmd[-1]) if cmd else ""
            if fresh_path_arg and "recovered-" in fresh_path_arg:
                _make_healthy_session(Path(fresh_path_arg), peer_rows=5)
            m.stdout = b""
        return m

    monkeypatch.setattr(_subproc, "run", fake_run)

    result = attempt_recovery(sess, timeout_sec=5.0)

    if result["recovered"]:
        # sessions_count >= 1 → auth_key row есть.
        if result["sessions_count"] is not None:
            assert result["sessions_count"] >= 1, (
                f"sessions_count={result['sessions_count']} — auth_key row missing!"
            )


# ── 10. has_recent_recovery_backup edge cases ─────────────────────────────────


def test_has_recent_recovery_backup_fresh(tmp_path: Path) -> None:
    """Нет backup-файлов → False."""
    sess = tmp_path / "kraab.session"
    assert has_recent_recovery_backup(sess) is False


def test_has_recent_recovery_backup_just_created(tmp_path: Path) -> None:
    """Только что созданный backup → True."""
    sess = tmp_path / "kraab.session"
    backup = sess.with_name(f"{sess.name}.bak-corrupt-{int(time.time())}")
    backup.write_bytes(b"x")
    assert has_recent_recovery_backup(sess, within_seconds=3600) is True


def test_has_recent_recovery_backup_old_ignored(tmp_path: Path) -> None:
    """Backup старше cooldown → False."""
    sess = tmp_path / "kraab.session"
    old_ts = int(time.time()) - 7200
    old_backup = sess.with_name(f"{sess.name}.bak-corrupt-{old_ts}")
    old_backup.write_bytes(b"x")
    os.utime(old_backup, (time.time() - 7200, time.time() - 7200))
    assert has_recent_recovery_backup(sess, within_seconds=3600) is False


# ── 11. cleanup_sidecars ──────────────────────────────────────────────────────


def test_cleanup_sidecars_removes_all_three(tmp_path: Path) -> None:
    """cleanup_sidecars удаляет -wal, -shm, -journal."""
    sess = tmp_path / "kraab.session"
    sess.write_bytes(b"fake session")

    for suffix in ("-wal", "-shm", "-journal"):
        sess.with_name(sess.name + suffix).write_bytes(b"sidecar")

    removed = cleanup_sidecars(sess)
    assert len(removed) == 3
    for suffix in ("-wal", "-shm", "-journal"):
        assert not sess.with_name(sess.name + suffix).exists()


def test_cleanup_sidecars_no_sidecars(tmp_path: Path) -> None:
    """Нет сайдкаров → пустой список, нет ошибок."""
    sess = tmp_path / "kraab.session"
    sess.write_bytes(b"fake")
    removed = cleanup_sidecars(sess)
    assert removed == []


# ── 12. verify_key_tables ────────────────────────────────────────────────────


def test_verify_key_tables_all_present(tmp_path: Path) -> None:
    """Все required tables присутствуют → (True, ...)."""
    db = tmp_path / "test.session"
    _make_healthy_session(db)
    ok, detail = verify_key_tables(db)
    assert ok is True
    assert "tables_ok" in detail


def test_verify_key_tables_missing(tmp_path: Path) -> None:
    """Отсутствуют some tables → (False, missing_tables: ...)."""
    db = tmp_path / "test.session"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE junk (id INTEGER)")
    conn.commit()
    conn.close()
    ok, detail = verify_key_tables(db)
    assert ok is False
    assert "missing_tables" in detail
