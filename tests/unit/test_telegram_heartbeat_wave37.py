# -*- coding: utf-8 -*-
"""
Тесты Wave 37-A: improved heartbeat reliability.

Меняем поведение `_telegram_heartbeat_loop`:
1. Success path БОЛЬШЕ НЕ обновляет `_last_telegram_event_ts` (был bug —
   маскировал silence detection при split-brain pyrogram session).
   Вместо этого обновляет новое поле `_last_heartbeat_ok_ts` (для диагностики).
2. На первый timeout/fail — попытка graceful `_try_reconnect_pyrofork()`
   ПЕРЕД escalation. Если reconnect success → counter=0, продолжаем.
   Если reconnect fail → counter остаётся, ждём threshold для launchd exit.

Запускаем real `NetworkWatchdogMixin._telegram_heartbeat_loop` через
test subclass + monkey-patched `asyncio.sleep`, чтобы проходило 1-2
итерации и потом cancel.
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.userbot.network_watchdog import NetworkWatchdogMixin

# ── helpers ────────────────────────────────────────────────────────────────────


class _TestUserbot(NetworkWatchdogMixin):
    """Минимальный subclass с атрибутами для запуска real loop."""

    def __init__(self) -> None:
        self._last_telegram_event_ts: float = time.time()
        self._last_heartbeat_ok_ts: float = time.time()
        self.client = MagicMock(is_connected=True)
        self._send_zombie_alert_to_owner = AsyncMock()
        # _try_reconnect_pyrofork может быть переопределён в тесте
        self._try_reconnect_pyrofork = AsyncMock(return_value=True)


def _make_iteration_limited_sleep(max_iters: int) -> tuple:
    """Возвращает (fake_sleep, get_count): fake_sleep raises CancelledError
    после max_iters вызовов. get_count возвращает текущее количество вызовов.

    ВАЖНО: capture real asyncio.sleep ДО патча, иначе внутренний `await
    asyncio.sleep(0)` тоже патчится → бесконечная рекурсия и тест ничего
    не проверяет.
    """
    counter = {"n": 0}
    _real_sleep = asyncio.sleep  # capture ПЕРЕД monkey-patching

    async def _fake_sleep(_seconds: float) -> None:
        counter["n"] += 1
        if counter["n"] > max_iters:
            raise asyncio.CancelledError
        # Yield event loop через REAL sleep (не пропатченный)
        await _real_sleep(0)

    return _fake_sleep, lambda: counter["n"]


# ── test A: heartbeat success НЕ обновляет _last_telegram_event_ts ───────────


@pytest.mark.asyncio
async def test_heartbeat_success_does_not_overwrite_last_telegram_event_ts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 37-A: heartbeat success НЕ обновляет _last_telegram_event_ts.

    Это критично для split-brain detection: silence-monitor должен видеть
    реальное время с last user event, не сбрасываться heartbeat'ом.
    """
    stub = _TestUserbot()

    old_event_ts = time.time() - 600  # 10 минут назад
    stub._last_telegram_event_ts = old_event_ts

    # Successful invoke
    stub.client.invoke = AsyncMock(return_value=[MagicMock()])

    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "5")

    fake_sleep, _get_count = _make_iteration_limited_sleep(max_iters=2)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        await stub._telegram_heartbeat_loop()
    except asyncio.CancelledError:
        pass

    assert stub._last_telegram_event_ts == pytest.approx(old_event_ts, abs=0.5), (
        "_last_telegram_event_ts должен ОСТАТЬСЯ старым "
        f"(было {old_event_ts}, стало {stub._last_telegram_event_ts}) — "
        "heartbeat success НЕ должен его обновлять"
    )


# ── test B: heartbeat success обновляет _last_heartbeat_ok_ts ────────────────


@pytest.mark.asyncio
async def test_heartbeat_success_updates_last_heartbeat_ok_ts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 37-A: новое поле _last_heartbeat_ok_ts обновляется при success."""
    stub = _TestUserbot()

    old_ok_ts = time.time() - 600
    stub._last_heartbeat_ok_ts = old_ok_ts

    stub.client.invoke = AsyncMock(return_value=[MagicMock()])

    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "1")

    fake_sleep, _ = _make_iteration_limited_sleep(max_iters=2)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        await stub._telegram_heartbeat_loop()
    except asyncio.CancelledError:
        pass

    assert stub._last_heartbeat_ok_ts > old_ok_ts, (
        "_last_heartbeat_ok_ts должен обновиться при heartbeat success "
        f"(было {old_ok_ts}, стало {stub._last_heartbeat_ok_ts})"
    )
    assert stub._last_heartbeat_ok_ts > (time.time() - 5), (
        "_last_heartbeat_ok_ts должен быть свежим (< 5s назад)"
    )


# ── test C: single timeout → triggers graceful reconnect attempt ─────────────


@pytest.mark.asyncio
async def test_heartbeat_single_timeout_attempts_graceful_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 37-A: на первый timeout — попытка _try_reconnect_pyrofork
    ПЕРЕД ожиданием threshold (не ждём 3 fail чтобы action принять)."""
    stub = _TestUserbot()

    # Invoke всегда timeout
    async def _invoke_timeout(*args, **kwargs):  # noqa: ANN001
        raise asyncio.TimeoutError

    stub.client.invoke = _invoke_timeout

    # Reconnect возвращает True (success)
    reconnect_mock = AsyncMock(return_value=True)
    stub._try_reconnect_pyrofork = reconnect_mock

    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "1")

    # Запретить _exit на всякий случай
    exit_calls: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

    fake_sleep, _ = _make_iteration_limited_sleep(max_iters=2)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        await stub._telegram_heartbeat_loop()
    except asyncio.CancelledError:
        pass

    assert reconnect_mock.await_count >= 1, (
        "Должна быть хотя бы 1 попытка _try_reconnect_pyrofork после первого heartbeat timeout"
    )
    assert exit_calls == [], (
        f"На single timeout НЕ должен вызваться os._exit — вызвался: {exit_calls}"
    )


# ── test D: graceful restart success → counter resets ────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_graceful_restart_success_resets_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 37-A: после fail → reconnect success → следующая success итерация
    подтверждает что counter сброшен (нет escalation на threshold).

    Сценарий: iteration 1 fails + reconnect success → counter=0;
    iteration 2 success — counter остаётся 0; iteration 3 success — total
    < threshold, escalation НЕ должна сработать.
    """
    stub = _TestUserbot()

    # Invoke timeout на 1-й итерации, success на остальных
    call_count = {"n": 0}

    async def _invoke_pattern(*args, **kwargs):  # noqa: ANN001
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise asyncio.TimeoutError
        return [MagicMock()]

    stub.client.invoke = _invoke_pattern
    reconnect_mock = AsyncMock(return_value=True)
    stub._try_reconnect_pyrofork = reconnect_mock

    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "1")

    exit_calls: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

    fake_sleep, _ = _make_iteration_limited_sleep(max_iters=4)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        await stub._telegram_heartbeat_loop()
    except asyncio.CancelledError:
        pass

    assert reconnect_mock.await_count >= 1, (
        "При первом fail должен быть вызван _try_reconnect_pyrofork "
        f"(was {reconnect_mock.await_count})"
    )
    assert exit_calls == [], (
        "После reconnect success counter должен быть сброшен — "
        f"escalation не должна срабатывать (exit_calls={exit_calls})"
    )


# ── test E: graceful restart fail does not immediately escalate ──────────────


@pytest.mark.asyncio
async def test_heartbeat_graceful_restart_fail_does_not_immediately_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 37-A: если reconnect fails, не делаем os._exit сразу — ждём
    остальных fails чтобы достичь threshold (3). Это даёт system shot
    at recovery без immediate destructive launchd respawn.

    Сценарий: 1 fail + reconnect fail → counter=1, no exit. Следующая
    итерация — мы её не запускаем (sleep cancelled), exit_calls пуст.
    """
    stub = _TestUserbot()

    async def _invoke_timeout(*args, **kwargs):  # noqa: ANN001
        raise asyncio.TimeoutError

    stub.client.invoke = _invoke_timeout
    # Reconnect failures — возвращает False
    reconnect_mock = AsyncMock(return_value=False)
    stub._try_reconnect_pyrofork = reconnect_mock

    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "1")

    exit_calls: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

    # Только одна итерация — counter будет 1
    fake_sleep, _ = _make_iteration_limited_sleep(max_iters=1)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        await stub._telegram_heartbeat_loop()
    except asyncio.CancelledError:
        pass

    assert reconnect_mock.await_count >= 1, (
        "При первом fail _try_reconnect_pyrofork должен быть вызван даже "
        "если он failed (мы пробуем хотя бы раз)"
    )
    assert exit_calls == [], (
        "Single fail + reconnect fail НЕ должен сразу вызывать os._exit — "
        "threshold=3, имеет смысл подождать остальных fails. "
        f"exit_calls={exit_calls}"
    )


# ── test F: смок — _last_heartbeat_ok_ts инициализируется в KraabUserbot ────


def test_last_heartbeat_ok_ts_attribute_in_userbot_bridge() -> None:
    """Wave 37-A: атрибут _last_heartbeat_ok_ts должен быть инициализирован
    в `KraabUserbot.__init__` (для diagnostics health endpoint)."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot_bridge.py"
    text = src.read_text(encoding="utf-8")
    assert "_last_heartbeat_ok_ts" in text, (
        "_last_heartbeat_ok_ts должен быть инициализирован в userbot_bridge.py"
    )
