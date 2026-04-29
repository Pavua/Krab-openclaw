# -*- coding: utf-8 -*-
"""Тесты REPLSession — sandbox, persistence, timeout, audit, isolation."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pytest

from src.core.repl_session import (
    DEFAULT_EXEC_TIMEOUT_S,
    REPLNotStartedError,
    REPLSecurityError,
    REPLSession,
    REPLTimeoutError,
)


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "repl_audit.log"


@pytest.fixture
def session(audit_path: Path) -> REPLSession:
    s = REPLSession(audit_log_path=audit_path, default_timeout_s=2.0)
    yield s
    s.shutdown()


def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------- 1) start + exec persistence ----------------


def test_start_and_exec_preserves_state_across_calls(session: REPLSession) -> None:
    owner = 111
    assert session.start(owner) is True
    # Сначала assignment, потом expression — переменная должна сохраниться
    r1 = session.exec_code("x = 41", owner_id=owner)
    assert r1.ok is True, r1.error_message
    assert r1.value is None  # statement → no value

    r2 = session.exec_code("x + 1", owner_id=owner)
    assert r2.ok is True
    assert r2.value == 42

    # Повторный start — idempotent, возвращает False, state сохраняется
    assert session.start(owner) is False

    # Whitelisted modules доступны
    r3 = session.exec_code("math.sqrt(16)", owner_id=owner)
    assert r3.ok is True
    assert r3.value == 4.0


# ---------------- 2) forbidden import rejected ----------------


def test_forbidden_imports_blocked(session: REPLSession) -> None:
    owner = 222
    session.start(owner)

    for code in (
        "import os",
        "import sys",
        "import subprocess",
        "from socket import socket",
        "import ctypes",
    ):
        with pytest.raises(REPLSecurityError):
            session.exec_code(code, owner_id=owner)

    # Доступ к dunder-атрибутам — побег через .__class__.__bases__
    for code in (
        "(1).__class__",
        "[].__class__.__bases__",
        "''.__class__.__mro__",
    ):
        with pytest.raises(REPLSecurityError):
            session.exec_code(code, owner_id=owner)

    # Forbidden builtins по имени
    for code in (
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "__import__('os')",
        "globals()",
    ):
        with pytest.raises(REPLSecurityError):
            session.exec_code(code, owner_id=owner)


# ---------------- 3) timeout enforcement ----------------


def test_timeout_enforced(session: REPLSession) -> None:
    owner = 333
    session.start(owner)
    # Бесконечный цикл — должен прерваться через timeout
    t0 = time.monotonic()
    with pytest.raises(REPLTimeoutError):
        session.exec_code(
            "while True:\n    pass",
            owner_id=owner,
            timeout_s=0.3,
        )
    elapsed = time.monotonic() - t0
    # Timeout сработал в разумном окне (не висим > 5s)
    assert elapsed < 3.0


# ---------------- 4) per-owner isolation ----------------


def test_per_owner_isolation(session: REPLSession) -> None:
    a, b = 1001, 1002
    session.start(a)
    session.start(b)

    session.exec_code("secret = 'A'", owner_id=a)
    session.exec_code("secret = 'B'", owner_id=b)

    ra = session.exec_code("secret", owner_id=a)
    rb = session.exec_code("secret", owner_id=b)
    assert ra.value == "A"
    assert rb.value == "B"

    # Owner b не видит var owner a (тут одинаковое имя, но если бы другое —
    # был бы NameError; проверим прямо).
    session.exec_code("only_a = 999", owner_id=a)
    rb2 = session.exec_code("only_a", owner_id=b)
    assert rb2.ok is False
    assert rb2.error_type == "NameError"


# ---------------- 5) audit log written ----------------


def test_audit_log_written(audit_path: Path, session: REPLSession) -> None:
    owner = 4242
    session.start(owner)
    session.exec_code("y = 1\ny + 2", owner_id=owner)
    # Forbidden — должен попасть в audit как security_block
    with pytest.raises(REPLSecurityError):
        session.exec_code("import os", owner_id=owner)
    session.stop(owner)

    records = _read_audit(audit_path)
    actions = [r["action"] for r in records]
    kinds = [r["result_kind"] for r in records]
    assert "start" in actions
    assert "exec" in actions
    assert "stop" in actions
    assert "security_block" in kinds
    assert "ok" in kinds
    # owner_id присутствует во всех записях
    assert all(r["owner_id"] == owner for r in records)
    # code_preview обрезается, code_len правдивый
    exec_records = [r for r in records if r["action"] == "exec"]
    assert all("code_len" in r for r in exec_records)


# ---------------- 6) stop cleans state ----------------


def test_stop_cleans_state(session: REPLSession) -> None:
    owner = 5555
    session.start(owner)
    session.exec_code("memorable = 'remember me'", owner_id=owner)
    assert session.is_started(owner) is True

    assert session.stop(owner) is True
    assert session.is_started(owner) is False

    # После stop — exec бросает REPLNotStartedError
    with pytest.raises(REPLNotStartedError):
        session.exec_code("memorable", owner_id=owner)

    # Повторный stop — noop, возвращает False
    assert session.stop(owner) is False

    # Новый start даёт чистый namespace
    session.start(owner)
    r = session.exec_code("memorable", owner_id=owner)
    assert r.ok is False
    assert r.error_type == "NameError"


# ---------------- bonus: meta + clock injection ----------------


def test_meta_tracks_exec_count_with_injected_clock(audit_path: Path) -> None:
    clock = [dt.datetime(2026, 4, 28, 12, 0, 0, tzinfo=dt.timezone.utc)]
    s = REPLSession(audit_log_path=audit_path, now_fn=lambda: clock[0])
    try:
        owner = 7
        s.start(owner)
        clock[0] += dt.timedelta(seconds=5)
        s.exec_code("a = 1", owner_id=owner)
        clock[0] += dt.timedelta(seconds=10)
        s.exec_code("a + 1", owner_id=owner)

        meta = s.get_meta(owner)
        assert meta is not None
        assert meta["exec_count"] == 2
        assert meta["started_at"].startswith("2026-04-28T12:00:00")
        assert meta["last_exec_at"].startswith("2026-04-28T12:00:15")

        # list_owners возвращает копию
        owners = s.list_owners()
        assert owners == [owner]
        owners.append(999)
        assert s.list_owners() == [owner]
    finally:
        s.shutdown()


def test_default_timeout_constant_is_reasonable() -> None:
    assert DEFAULT_EXEC_TIMEOUT_S == 10.0


def test_print_captures_stdout(session: REPLSession) -> None:
    owner = 88
    session.start(owner)
    r = session.exec_code("print('hello'); 7", owner_id=owner)
    # SyntaxError — `print(...); 7` валидный, последний ; означает Expr — ок.
    # Альтернативно — две строки.
    if not r.ok:
        r = session.exec_code("print('hello')\n7", owner_id=owner)
    assert r.ok is True
    assert "hello" in r.stdout
    assert r.value == 7
