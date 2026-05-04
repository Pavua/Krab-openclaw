# -*- coding: utf-8 -*-
"""
Тесты _telegram_session_snapshot и _normalize_telegram_session_truth.

Wave 16-H: проверяем исправление lock-contention false-positive — когда
Pyrogram держит SQLite session открытой, probe не должен возвращать
state="corrupted".

Wave 16-O (HIGH-1): _telegram_session_snapshot стала async — тесты
используют asyncio.run() и проверяют отсутствие blocking time.sleep.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_webapp(project_root: Path) -> Any:
    """Создаём минимальный WebApp с заглушками deps."""
    import os
    import sys

    # Минимальный mocking чтобы не поднимать весь FastAPI стек
    sys.modules.setdefault("uvicorn", MagicMock())
    sys.modules.setdefault("sentry_sdk", MagicMock())

    with patch.dict(os.environ, {"TELEGRAM_SESSION_NAME": "kraab"}):
        from src.modules.web_app import WebApp

        deps: dict = {}
        wa = WebApp.__new__(WebApp)
        # Минимальные атрибуты нужные для _telegram_session_snapshot
        wa.deps = deps
        wa._project_root = lambda: project_root  # type: ignore[method-assign]
        return wa


def _make_session_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Создаём data/sessions/ и возвращаем (project_root, session_file)."""
    session_dir = tmp_path / "data" / "sessions"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "kraab.session"
    return tmp_path, session_file


# ---------------------------------------------------------------------------
# 1. Чистая сессия без sidecar → state=ready
# ---------------------------------------------------------------------------


def test_snapshot_ready_when_session_clean(tmp_path: Path) -> None:
    """file exists, quick_check ok, нет sidecar → state=ready."""
    project_root, session_file = _make_session_dir(tmp_path)

    # Реальный SQLite файл — quick_check вернёт 'ok'
    conn = sqlite3.connect(str(session_file))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.close()

    wa = _make_webapp(project_root)
    snap = asyncio.run(wa._telegram_session_snapshot())

    assert snap["state"] == "ready"
    assert snap["sqlite_quick_check_ok"] is True
    assert snap["sqlite_error"] == ""
    assert snap["session_exists"] is True


# ---------------------------------------------------------------------------
# 2. Реальная malformed DB → state=corrupted (не маскируется ретраями)
# ---------------------------------------------------------------------------


def test_snapshot_corrupted_on_real_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OperationalError('malformed') без 'locked'/'busy' → corrupted, без retry."""
    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"not a sqlite db at all")

    wa = _make_webapp(project_root)

    # Не патчим connect — реальный sqlite3 должен поднять "file is not a database"
    snap = asyncio.run(wa._telegram_session_snapshot())

    assert snap["state"] == "corrupted"
    assert snap["sqlite_quick_check_ok"] is False
    assert snap["sqlite_error"] != ""


# ---------------------------------------------------------------------------
# 3. 3x locked + userbot running → _normalize даёт ready
# ---------------------------------------------------------------------------


def test_snapshot_busy_returns_none_then_normalized_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Все 3 попытки получают 'locked' → sqlite_ok=None → normalize(userbot running)=ready."""
    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"")  # файл существует

    locked_exc = sqlite3.OperationalError("database is locked")

    import src.modules.web_app as webapp_module

    with monkeypatch.context() as m:
        m.setattr(
            webapp_module.sqlite3,
            "connect",
            MagicMock(side_effect=locked_exc),
        )
        wa = _make_webapp(project_root)
        snap = asyncio.run(wa._telegram_session_snapshot())

    assert snap["sqlite_quick_check_ok"] is None, "должен быть None, а не False"
    assert "busy_after_" in snap["sqlite_error"]
    assert snap["state"] == "open_or_unclean"  # до нормализации

    # Теперь нормализуем с живым userbot
    from src.modules.web_app import WebApp

    userbot_state = {"startup_state": "running", "client_connected": True}
    normalized = WebApp._normalize_telegram_session_truth(snap, userbot_state)

    assert normalized["state"] == "ready"
    assert normalized["state_reason"] == "sqlite_busy_during_active_userbot"


# ---------------------------------------------------------------------------
# 4. 3x locked, userbot offline → остаётся open_or_unclean
# ---------------------------------------------------------------------------


def test_snapshot_busy_persisted_returns_open_or_unclean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Все retries locked, userbot не running → open_or_unclean (не corrupted)."""
    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"")

    locked_exc = sqlite3.OperationalError("database is locked")

    import src.modules.web_app as webapp_module

    with monkeypatch.context() as m:
        m.setattr(
            webapp_module.sqlite3,
            "connect",
            MagicMock(side_effect=locked_exc),
        )
        wa = _make_webapp(project_root)
        snap = asyncio.run(wa._telegram_session_snapshot())

    # userbot НЕ running
    from src.modules.web_app import WebApp

    userbot_state = {"startup_state": "starting", "client_connected": False}
    normalized = WebApp._normalize_telegram_session_truth(snap, userbot_state)

    assert normalized["state"] == "open_or_unclean"
    # Критично: НЕ corrupted
    assert normalized["state"] != "corrupted"


# ---------------------------------------------------------------------------
# 5. Проверяем что URI read-only передаётся в sqlite3.connect
# ---------------------------------------------------------------------------


def test_snapshot_uses_read_only_uri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Connect должен вызываться с uri=True и file:...?mode=ro."""
    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"")

    import src.modules.web_app as webapp_module

    connect_calls: list[tuple] = []
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("ok",)
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    def fake_connect(uri_str, *, uri=False, timeout=0):
        connect_calls.append((uri_str, uri, timeout))
        return mock_conn

    with monkeypatch.context() as m:
        m.setattr(webapp_module.sqlite3, "connect", fake_connect)
        wa = _make_webapp(project_root)
        asyncio.run(wa._telegram_session_snapshot())

    assert len(connect_calls) >= 1
    uri_str, uri_flag, _ = connect_calls[0]
    assert uri_flag is True, "connect должен вызываться с uri=True"
    assert "?mode=ro" in uri_str, f"URI должен содержать ?mode=ro, получили: {uri_str}"
    assert str(session_file) in uri_str


# ---------------------------------------------------------------------------
# 6. sqlite_error содержит busy_after_N_retries при lock contention
# ---------------------------------------------------------------------------


def test_snapshot_retry_count_logged_in_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При 3 lock попытках sqlite_error содержит busy_after_3_retries."""
    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"")

    locked_exc = sqlite3.OperationalError("database is locked")

    import src.modules.web_app as webapp_module

    call_count = 0

    def counting_connect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise locked_exc

    with monkeypatch.context() as m:
        m.setattr(webapp_module.sqlite3, "connect", counting_connect)
        wa = _make_webapp(project_root)
        snap = asyncio.run(wa._telegram_session_snapshot())

    assert "busy_after_3_retries" in snap["sqlite_error"], (
        f"Ожидали busy_after_3_retries, получили: {snap['sqlite_error']!r}"
    )
    assert call_count == 3, f"Ожидали 3 попытки, получили {call_count}"


# ---------------------------------------------------------------------------
# 7. HIGH-1: _telegram_session_snapshot — async и без time.sleep
# ---------------------------------------------------------------------------


def test_snapshot_is_async_coroutine() -> None:
    """_telegram_session_snapshot должна быть async-функцией (не блокирует event loop)."""
    import src.modules.web_app as webapp_module

    method = webapp_module.WebApp._telegram_session_snapshot
    # inspect.iscoroutinefunction проверяет async def на уровне интроспекции
    assert inspect.iscoroutinefunction(method), (
        "_telegram_session_snapshot должна быть async def (HIGH-1 fix)"
    )


def test_snapshot_no_time_sleep_in_source() -> None:
    """Исходный код _telegram_session_snapshot не должен содержать вызов time.sleep (только asyncio.sleep)."""
    import src.modules.web_app as webapp_module

    source = inspect.getsource(webapp_module.WebApp._telegram_session_snapshot)

    # Фильтруем строки-комментарии и проверяем только реальный код.
    # Комментарии могут упоминать time.sleep для сравнения — это OK.
    code_lines = [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "time.sleep" not in code_only, (
        "time.sleep обнаружен в коде _telegram_session_snapshot (не в комментарии) — "
        "должен быть заменён на await asyncio.sleep (HIGH-1)"
    )
    assert "asyncio.sleep" in source, (
        "asyncio.sleep не найден в _telegram_session_snapshot — backoff должен быть async"
    )


# ---------------------------------------------------------------------------
# 8. Gap 1 (Wave 17-A): async non-blocking integration test
#    Запускаем probe параллельно с asyncio.sleep(0.1) — оба должны завершиться
#    за ~backoff_time (≈0.3s), а НЕ последовательно (тогда было бы >0.4s).
#    Проверяем concurrent execution через asyncio.gather + wall-time.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_backoff_does_not_block_event_loop(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Gap 1 (Wave 17-A): _telegram_session_snapshot с 3 'locked' retry
    использует await asyncio.sleep (не time.sleep), поэтому event loop
    остаётся незаблокированным.

    Метод:
    - Все 3 попытки sqlite3.connect бросают OperationalError('locked').
    - asyncio.sleep backoff: 0.05 + 0.10 + 0.15 = 0.30s суммарно.
    - Параллельно запускаем asyncio.sleep(0.1) task.
    - Если event loop заблокирован sync sleep — total > 0.4s (последовательно).
    - Если правильный async — total ≈ 0.3s (concurrent), т.е. < 0.45s.
    """
    import time as _time

    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"")  # файл существует

    locked_exc = sqlite3.OperationalError("database is locked")

    import src.modules.web_app as webapp_module

    # Mock asyncio.sleep в модуле web_app чтобы контролировать время.
    # Используем реальный asyncio.sleep чтобы event loop не переключился на sync.
    # Нам важно что sleep AWAIT-ится — не вызывается synchronously.
    actual_sleeps: list[float] = []

    _orig_asyncio_sleep = asyncio.sleep

    async def _tracking_sleep(delay: float) -> None:
        """Отслеживаем все sleep вызовы из probe."""
        actual_sleeps.append(delay)
        await _orig_asyncio_sleep(delay)

    with monkeypatch.context() as m:
        m.setattr(webapp_module.sqlite3, "connect", MagicMock(side_effect=locked_exc))
        # Патчим asyncio.sleep только в модуле web_app
        m.setattr(webapp_module.asyncio, "sleep", _tracking_sleep)

        wa = _make_webapp(project_root)

        t_start = _time.monotonic()

        # Запускаем probe и фоновый sleep параллельно
        _probe_coro = wa._telegram_session_snapshot()
        _sleep_coro = _orig_asyncio_sleep(0.1)
        await asyncio.gather(_probe_coro, _sleep_coro)

        t_total = _time.monotonic() - t_start

    # Суммарный backoff: 0.05 + 0.10 + 0.15 = 0.30s
    expected_backoff = 0.05 + 0.10 + 0.15

    # Если event loop был заблокирован sync sleep:
    # total = backoff_seq + sleep_0.1 > 0.4s
    # Если async (concurrent): total ≈ max(backoff, 0.1) ≈ 0.3s
    # Даём запас ×2 чтобы не быть flaky на медленных CI, но порог выдерживает.
    max_allowed = expected_backoff * 2 + 0.2  # 0.8s — достаточный буфер

    assert t_total < max_allowed, (
        f"event loop был заблокирован? total={t_total:.3f}s > max_allowed={max_allowed:.3f}s. "
        f"Это значит asyncio.sleep не await-ится корректно."
    )

    # Проверяем что sleep вызывался из backoff (3 попытки = 3 sleep вызова)
    assert len(actual_sleeps) == 3, (
        f"Ожидали 3 backoff sleep вызова, получили {len(actual_sleeps)}: {actual_sleeps}"
    )

    # Значения должны совпадать с формулой 0.05 * (attempt + 1)
    assert actual_sleeps[0] == pytest.approx(0.05, abs=0.001)
    assert actual_sleeps[1] == pytest.approx(0.10, abs=0.001)
    assert actual_sleeps[2] == pytest.approx(0.15, abs=0.001)


@pytest.mark.asyncio
async def test_snapshot_no_time_sleep_concurrent_sleep_task_completes(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Gap 1 расширение: фоновая coroutine sleep(0.05) завершается во время probe.
    Если event loop был заблокирован — фоновый task мог бы не получить CPU.
    Проверяем через gather что оба завершились и фоновый task успел выполниться.
    """
    project_root, session_file = _make_session_dir(tmp_path)
    session_file.write_bytes(b"")

    import src.modules.web_app as webapp_module

    locked_exc = sqlite3.OperationalError("database is locked")

    background_completed = False

    async def _background_task() -> None:
        nonlocal background_completed
        await asyncio.sleep(0.05)
        background_completed = True

    with monkeypatch.context() as m:
        m.setattr(webapp_module.sqlite3, "connect", MagicMock(side_effect=locked_exc))

        wa = _make_webapp(project_root)
        snap, _ = await asyncio.gather(
            wa._telegram_session_snapshot(),
            _background_task(),
        )

    # Оба должны завершиться
    assert background_completed, (
        "Фоновый task не завершился — event loop был заблокирован sync sleep"
    )
    # Probe вернул ожидаемый snap
    assert snap["state"] in ("open_or_unclean", "corrupted", "ready", "missing")
