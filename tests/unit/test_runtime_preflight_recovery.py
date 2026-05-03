# -*- coding: utf-8 -*-
"""
Integration тесты: preflight в src/userbot/session.py вызывает attempt_recovery
через db_corruption_guard.attempt_session_recovery (который сам делегирует
в session_recovery.attempt_recovery).

Wave 16-N: проверяем integration path:
    _main_session_integrity_preflight()
        → db_corruption_guard.attempt_session_recovery()
            → session_recovery.attempt_recovery()

Тесты:
1. test_runtime_preflight_invokes_recovery_on_malformed — corrupt session →
   attempt_session_recovery вызван (integration).
2. test_runtime_preflight_skips_recovery_on_clean — clean session →
   attempt_session_recovery НЕ вызывался.
3. test_runtime_preflight_recovery_success_continues_boot — recovery ok →
   preflight возвращает True (boot продолжается).
4. test_runtime_preflight_recovery_failure_raises_corruption_error — recovery
   fails → DBCorruptionError raised.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.bootstrap import db_corruption_guard
from src.bootstrap.db_corruption_guard import DBCorruptionError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_healthy_session(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE sessions (dc_id INTEGER, auth_key BLOB)")
        conn.execute("INSERT INTO sessions VALUES (2, X'BEEF')")
        conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT)")
        conn.execute("CREATE TABLE usernames (id INTEGER, username TEXT)")
        conn.commit()
    finally:
        conn.close()


def _make_mixin(tmp_path: Path) -> object:
    """Строит SessionMixin stub с _session_workdir = tmp_path."""
    from src.userbot.session import SessionMixin

    class _Stub(SessionMixin):
        def __init__(self, workdir: Path) -> None:
            self._session_workdir = workdir

    return _Stub(tmp_path)


# ── 1. Corrupt session → attempt_session_recovery вызван ─────────────────────


def test_runtime_preflight_invokes_recovery_on_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    При corrupt session preflight вызывает attempt_session_recovery.

    Проверяем через monkeypatch на db_corruption_guard.attempt_session_recovery
    (именно эта функция вызывается из _main_session_integrity_preflight).
    """
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    # Форсируем corruption через integrity_check monkeypatch.
    monkeypatch.setattr(
        db_corruption_guard,
        "integrity_check",
        lambda path, **kw: (False, "database disk image is malformed"),
    )
    # Убираем idempotency guard — даём recovery запуститься.
    monkeypatch.setattr(
        db_corruption_guard,
        "has_recent_recovery_backup",
        lambda path, **kw: False,
    )

    recovery_called_with: list[Path] = []

    def fake_attempt(path: Path, *, timeout_sec: float = 30.0) -> dict:
        recovery_called_with.append(path)
        return {
            "recovered": True,
            "backup_path": str(path) + ".bak-corrupt-fake",
            "peer_count": 10,
            "username_count": 5,
            "sessions_count": 1,
            "detail": "ok",
        }

    monkeypatch.setattr(
        db_corruption_guard,
        "attempt_session_recovery",
        fake_attempt,
    )

    stub = _make_mixin(tmp_path)
    result = stub._main_session_integrity_preflight()  # type: ignore[attr-defined]

    # Preflight вернул True → boot продолжается.
    assert result is True
    # attempt_session_recovery был вызван ровно один раз.
    assert len(recovery_called_with) == 1
    assert recovery_called_with[0] == sess


# ── 2. Clean session → attempt_session_recovery НЕ вызывался ─────────────────


def test_runtime_preflight_skips_recovery_on_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Healthy session → attempt_session_recovery НЕ вызван (нет смысла).
    """
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    recovery_call_count = 0

    def fake_attempt(path: Path, **kw) -> dict:
        nonlocal recovery_call_count
        recovery_call_count += 1
        return {"recovered": False, "detail": "should_not_be_called"}

    monkeypatch.setattr(
        db_corruption_guard,
        "attempt_session_recovery",
        fake_attempt,
    )

    stub = _make_mixin(tmp_path)
    result = stub._main_session_integrity_preflight()  # type: ignore[attr-defined]

    assert result is True
    assert recovery_call_count == 0, (
        f"attempt_session_recovery был вызван {recovery_call_count} раз на healthy session"
    )


# ── 3. Recovery success → boot продолжается ──────────────────────────────────


def test_runtime_preflight_recovery_success_continues_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Recovery возвращает recovered=True → preflight возвращает True (boot ok).
    Krab НЕ выходит с exit 78.
    """
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    monkeypatch.setattr(
        db_corruption_guard,
        "integrity_check",
        lambda path, **kw: (False, "database disk image is malformed"),
    )
    monkeypatch.setattr(
        db_corruption_guard,
        "has_recent_recovery_backup",
        lambda path, **kw: False,
    )
    monkeypatch.setattr(
        db_corruption_guard,
        "attempt_session_recovery",
        lambda path, **kw: {
            "recovered": True,
            "backup_path": str(path) + ".bak",
            "peer_count": 42,
            "username_count": 10,
            "sessions_count": 1,
            "detail": "ok",
        },
    )

    stub = _make_mixin(tmp_path)
    # Не должно бросать исключений.
    result = stub._main_session_integrity_preflight()  # type: ignore[attr-defined]
    assert result is True


# ── 4. Recovery failure → DBCorruptionError ───────────────────────────────────


def test_runtime_preflight_recovery_failure_raises_corruption_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Recovery возвращает recovered=False → DBCorruptionError (boot abort).
    """
    from src.config import config

    monkeypatch.setattr(config, "TELEGRAM_SESSION_NAME", "kraab")

    sess = tmp_path / "kraab.session"
    _make_healthy_session(sess)

    monkeypatch.setattr(
        db_corruption_guard,
        "integrity_check",
        lambda path, **kw: (False, "database disk image is malformed"),
    )
    monkeypatch.setattr(
        db_corruption_guard,
        "has_recent_recovery_backup",
        lambda path, **kw: False,
    )
    monkeypatch.setattr(
        db_corruption_guard,
        "attempt_session_recovery",
        lambda path, **kw: {
            "recovered": False,
            "backup_path": str(path) + ".bak-fake",
            "peer_count": None,
            "username_count": None,
            "sessions_count": None,
            "detail": "recover_dump_failed rc=1",
        },
    )

    stub = _make_mixin(tmp_path)
    with pytest.raises(DBCorruptionError) as exc_info:
        stub._main_session_integrity_preflight()  # type: ignore[attr-defined]

    # Сообщение должно включать path и detail.
    assert "kraab.session" in str(exc_info.value) or "corrupt" in str(exc_info.value).lower()


# ── 5. db_corruption_guard.attempt_session_recovery delegates to shared module ─


def test_db_corruption_guard_delegates_to_session_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    db_corruption_guard.attempt_session_recovery теперь делегирует в
    session_recovery.attempt_recovery (Wave 16-N DRY refactor).

    Проверяем что вызов через db_corruption_guard доходит до shared module.
    """
    from src.bootstrap import session_recovery as _sr

    sess = tmp_path / "kraab.session"
    sess.write_bytes(b"fake")

    shared_called_with: list[Path] = []

    def fake_attempt_recovery(path: Path, **kw) -> dict:
        shared_called_with.append(path)
        return {
            "recovered": False,
            "idempotency_blocked": False,
            "dry_run": False,
            "backup_path": "",
            "sidecars_removed": [],
            "peer_count": None,
            "username_count": None,
            "sessions_count": None,
            "detail": "missing",
        }

    monkeypatch.setattr(_sr, "attempt_recovery", fake_attempt_recovery)

    # Вызываем через db_corruption_guard (public API).
    result = db_corruption_guard.attempt_session_recovery(sess)

    # Shared module был вызван.
    assert len(shared_called_with) == 1
    assert shared_called_with[0] == sess
    # Результат проброшен корректно.
    assert result["recovered"] is False
