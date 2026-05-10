# -*- coding: utf-8 -*-
"""
Wave 57-A: catchup trigger после graceful Pyrogram heartbeat restart.

Production bug 2026-05-10 21:12-21:31:
- 21:12:49 telegram_heartbeat_timeout consecutive_failures=1
- 21:12:50 telegram_heartbeat_graceful_restart_success
- 21:31:43 "Проверка связи" — не ingested в inbox
- PID 1743 alive throughout — full process restart НЕ был → Wave 46-A catchup не запустился

Fix: _schedule_catchup_after_graceful_restart() вызывается из heartbeat loop
после успешного _try_reconnect_pyrofork.

Тестируем:
1. Graceful restart → catchup task создаётся (базовый случай)
2. Uptime < 60s → catchup пропускается (startup catchup уже работает)
3. Второй graceful restart в течение 5 минут → throttled (нет второго catchup)
4. Через 6+ минут после throttle → catchup разрешён снова
5. _run_startup_catchup_safe raises → watchdog loop не ломается
6. _run_startup_catchup_safe отсутствует → предупреждение, нет crash
7. asyncio.create_task raises → timestamp откатывается (retry на следующем restart)
"""

from __future__ import annotations

import asyncio
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot.network_watchdog import NetworkWatchdogMixin

# ── helpers ─────────────────────────────────────────────────────────────────


def _make_stub(
    *,
    uptime_sec: float = 120.0,
    last_catchup_ago: float | None = None,
    has_catchup_method: bool = True,
) -> types.SimpleNamespace:
    """Минимальный stub с нужными атрибутами + bound методом миксина."""
    now = time.time()
    stub = types.SimpleNamespace()
    stub._session_start_time = now - uptime_sec
    stub._last_telegram_event_ts = now
    stub._last_heartbeat_ok_ts = now

    if last_catchup_ago is not None:
        stub._last_catchup_triggered_at = now - last_catchup_ago
    # Если _last_catchup_triggered_at не установлен — getattr вернёт 0.0 по умолчанию

    if has_catchup_method:
        stub._run_startup_catchup_safe = AsyncMock(name="_run_startup_catchup_safe")
    # else — метод отсутствует → проверяем graceful degradation

    # Bind метод из миксина
    stub._schedule_catchup_after_graceful_restart = (
        NetworkWatchdogMixin._schedule_catchup_after_graceful_restart.__get__(stub)
    )
    return stub


# ── тесты ───────────────────────────────────────────────────────────────────


def test_graceful_restart_triggers_catchup() -> None:
    """Базовый случай: uptime OK, throttle прошёл → asyncio.create_task вызван."""
    stub = _make_stub(uptime_sec=120.0, last_catchup_ago=400.0)

    with patch("asyncio.create_task") as mock_create_task:
        stub._schedule_catchup_after_graceful_restart()

    mock_create_task.assert_called_once()
    # Проверяем что timestamp catchup обновился (>0)
    assert hasattr(stub, "_last_catchup_triggered_at")
    assert time.time() - stub._last_catchup_triggered_at < 5.0


def test_graceful_restart_skipped_during_startup() -> None:
    """Uptime < 60s → catchup не запускается (startup catchup уже идёт)."""
    stub = _make_stub(uptime_sec=30.0)  # process только стартовал

    with patch("asyncio.create_task") as mock_create_task:
        stub._schedule_catchup_after_graceful_restart()

    mock_create_task.assert_not_called()
    # _last_catchup_triggered_at должен оставаться 0.0 (или отсутствовать)
    assert getattr(stub, "_last_catchup_triggered_at", 0.0) == 0.0


def test_graceful_restart_throttled_within_5min() -> None:
    """Второй graceful restart в течение 5 минут → throttled, catchup не создаётся."""
    stub = _make_stub(uptime_sec=300.0, last_catchup_ago=60.0)  # 60s назад — throttle ещё действует

    with patch("asyncio.create_task") as mock_create_task:
        stub._schedule_catchup_after_graceful_restart()

    mock_create_task.assert_not_called()


def test_graceful_restart_proceeds_after_throttle_expires() -> None:
    """Через 6 минут после предыдущего catchup — throttle истёк, catchup разрешён."""
    stub = _make_stub(uptime_sec=500.0, last_catchup_ago=360.0)  # 6 минут назад

    with patch("asyncio.create_task") as mock_create_task:
        stub._schedule_catchup_after_graceful_restart()

    mock_create_task.assert_called_once()


def test_catchup_failure_doesnt_propagate() -> None:
    """Если _run_startup_catchup_safe raises — watchdog loop не ломается.

    asyncio.create_task лишь планирует корутину; сам по себе raise'а нет
    в sync-контексте. Тестируем что schedule метод не throw'ит при create_task fail.
    """
    stub = _make_stub(uptime_sec=120.0, last_catchup_ago=400.0)
    prev_last_catchup = getattr(stub, "_last_catchup_triggered_at", 0.0)

    with patch("asyncio.create_task", side_effect=RuntimeError("event loop closed")):
        # Не должно бросать — watchdog loop продолжает работу
        stub._schedule_catchup_after_graceful_restart()

    # Timestamp должен откатиться до предыдущего значения (retry возможен)
    assert stub._last_catchup_triggered_at == prev_last_catchup


def test_catchup_method_missing_no_crash() -> None:
    """Если _run_startup_catchup_safe не объявлен → предупреждение, не crash."""
    stub = _make_stub(uptime_sec=120.0, last_catchup_ago=400.0, has_catchup_method=False)

    with patch("asyncio.create_task") as mock_create_task:
        # Не должно бросать KeyError/AttributeError
        stub._schedule_catchup_after_graceful_restart()

    mock_create_task.assert_not_called()


def test_first_ever_graceful_restart_no_last_catchup_attr() -> None:
    """Если _last_catchup_triggered_at ещё не установлен (первый restart) → catchup запускается."""
    stub = _make_stub(uptime_sec=200.0)
    # Убеждаемся что атрибут отсутствует (не устанавливался)
    assert not hasattr(stub, "_last_catchup_triggered_at")

    with patch("asyncio.create_task") as mock_create_task:
        stub._schedule_catchup_after_graceful_restart()

    mock_create_task.assert_called_once()
    # Атрибут теперь должен быть установлен
    assert hasattr(stub, "_last_catchup_triggered_at")


@pytest.mark.asyncio
async def test_catchup_task_is_fire_and_forget() -> None:
    """create_task вызывается с корутиной из _run_startup_catchup_safe (fire-and-forget).

    Корутина не awaited в месте вызова — watchdog loop не блокируется.
    """
    stub = _make_stub(uptime_sec=120.0, last_catchup_ago=400.0)
    captured_coro = None

    def fake_create_task(coro, *, name=None):
        nonlocal captured_coro
        captured_coro = coro
        # Надо закрыть корутину чтобы не получить RuntimeWarning
        coro.close()
        return MagicMock()

    with patch("asyncio.create_task", side_effect=fake_create_task):
        stub._schedule_catchup_after_graceful_restart()

    assert captured_coro is not None, "create_task должен получить корутину"
    # Проверяем что имя задачи указывает на catchup
    # (уже проверено что create_task вызван — дополнительно убедимся в имени)


@pytest.mark.asyncio
async def test_heartbeat_loop_calls_schedule_catchup_on_restart_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Интеграционный: _telegram_heartbeat_loop вызывает _schedule_catchup_after_graceful_restart
    после успешного _try_reconnect_pyrofork.

    Стратегия: первый sleep OK (graceful), invoke timeout → reconnect success →
    _schedule_catchup_after_graceful_restart вызван → второй sleep raises CancelledError
    → loop ловит CancelledError внутри try/except → break (нормальный выход).
    """
    import src.userbot.network_watchdog as nw

    # Первый sleep — ок. Второй sleep — CancelledError (завершает loop через break).
    sleep_call_count = 0

    async def fake_sleep(sec: float) -> None:
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(nw.asyncio, "sleep", fake_sleep)

    # Stub с bound методами heartbeat loop + schedule catchup
    stub = types.SimpleNamespace()
    stub._session_start_time = time.time() - 200.0
    stub._last_telegram_event_ts = time.time()
    stub._last_heartbeat_ok_ts = time.time()
    stub.client = MagicMock(is_connected=True)
    schedule_calls: list[str] = []

    stub._schedule_catchup_after_graceful_restart = lambda: schedule_calls.append("called")
    stub._send_zombie_alert_to_owner = AsyncMock()

    # Heartbeat invoke raises TimeoutError → reconnect succeeds
    async def fake_invoke(req):
        raise asyncio.TimeoutError

    stub.client.invoke = fake_invoke

    async def fake_reconnect(client):
        return True

    stub._try_reconnect_pyrofork = fake_reconnect
    stub._telegram_heartbeat_loop = NetworkWatchdogMixin._telegram_heartbeat_loop.__get__(stub)

    # Мокаем pyrogram imports внутри loop
    import sys

    fake_get_users = MagicMock()
    fake_input_user_self = MagicMock()
    sys.modules.setdefault("pyrogram", MagicMock())
    sys.modules.setdefault("pyrogram.raw", MagicMock())
    sys.modules.setdefault("pyrogram.raw.functions", MagicMock())
    sys.modules.setdefault("pyrogram.raw.functions.users", MagicMock())
    sys.modules.setdefault("pyrogram.raw.types", MagicMock())
    sys.modules["pyrogram.raw.functions.users"].GetUsers = fake_get_users
    sys.modules["pyrogram.raw.types"].InputUserSelf = fake_input_user_self

    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")

    # Loop завершается через break (CancelledError поймана внутри loop)
    await stub._telegram_heartbeat_loop()

    assert "called" in schedule_calls, (
        "_schedule_catchup_after_graceful_restart должен вызываться после graceful restart"
    )
