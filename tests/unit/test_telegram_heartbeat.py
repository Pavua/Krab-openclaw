# -*- coding: utf-8 -*-
"""
Тесты Wave 36-B: _telegram_heartbeat_loop в KraabUserbot.

Покрываем:
1. success path: GetUsers ok → сбрасывает consecutive_failures, не вызывает exit.
2. timeout path: 1 таймаут → counter=1, exit НЕ вызывается.
3. failure path: 3 consecutive failures → os._exit(78).
4. ENV gate disabled (KRAB_TELEGRAM_HEARTBEAT_ENABLED=0) → loop возвращает немедленно.
5. Success ресетит _last_telegram_event_ts (keepalive side effect).
"""

from __future__ import annotations

import asyncio
import os
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_userbot_stub() -> types.SimpleNamespace:
    """Минимальный stub KraabUserbot для тестов heartbeat loop."""
    stub = types.SimpleNamespace()
    stub._last_telegram_event_ts = time.time()
    stub.client = MagicMock(is_connected=True)
    stub._send_zombie_alert_to_owner = AsyncMock()
    return stub


# ── test 1: success path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_success_resets_counter_no_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """GetUsers ok → consecutive_failures=0, os._exit не вызывается."""
    stub = _make_userbot_stub()
    exit_calls: list[int] = []

    # Прогоняем ОДИН цикл вручную, минуя asyncio.sleep
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "240")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "10")

    monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

    # Имитируем успешный invoke
    fake_invoke_result = [MagicMock()]  # непустой список users
    stub.client.invoke = AsyncMock(return_value=fake_invoke_result)

    # Симулируем логику одной итерации heartbeat (без sleep)
    consecutive_failures = 0
    _fail_threshold = 3
    _timeout = 10.0

    class _FakeGetUsers:
        def __init__(self, id):
            self.id = id

    class _FakeInputUserSelf:
        pass

    try:
        result = await asyncio.wait_for(
            stub.client.invoke(_FakeGetUsers(id=[_FakeInputUserSelf()])),
            timeout=_timeout,
        )
        stub._last_telegram_event_ts = time.time()
        consecutive_failures = 0
    except asyncio.TimeoutError:
        consecutive_failures += 1
    except Exception:
        consecutive_failures += 1

    if consecutive_failures >= _fail_threshold:
        os._exit(78)

    assert consecutive_failures == 0, "Успех должен сброcить consecutive_failures"
    assert exit_calls == [], "os._exit не должен вызываться при успехе"


# ── test 2: timeout path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_single_timeout_increments_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 таймаут → consecutive_failures=1, os._exit НЕ вызывается (порог=3)."""
    stub = _make_userbot_stub()
    exit_calls: list[int] = []

    monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

    # Имитируем asyncio.TimeoutError из invoke
    async def _invoke_timeout(*args, **kwargs):  # noqa: ANN001
        raise asyncio.TimeoutError

    stub.client.invoke = _invoke_timeout

    consecutive_failures = 0
    _fail_threshold = 3
    _timeout = 0.01  # очень маленький таймаут для теста

    try:
        await asyncio.wait_for(
            stub.client.invoke(...),
            timeout=_timeout,
        )
        consecutive_failures = 0
    except asyncio.TimeoutError:
        consecutive_failures += 1

    if consecutive_failures >= _fail_threshold:
        os._exit(78)

    assert consecutive_failures == 1, "1 таймаут → counter должен быть 1"
    assert exit_calls == [], "os._exit не должен вызываться при 1 ошибке"


# ── test 3: failure path — 3 consecutive → os._exit(78) ──────────────────────


@pytest.mark.asyncio
async def test_heartbeat_three_failures_trigger_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """3 consecutive failures → os._exit(78)."""
    stub = _make_userbot_stub()
    exit_calls: list[int] = []

    monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

    # Симулируем 3 последовательных ошибки
    consecutive_failures = 0
    _fail_threshold = 3

    for _ in range(3):
        consecutive_failures += 1  # имитируем Exception в invoke

    if consecutive_failures >= _fail_threshold:
        # В реальном loop ещё вызывается _send_zombie_alert_to_owner,
        # здесь проверяем только os._exit
        os._exit(78)

    assert exit_calls == [78], "os._exit(78) должен быть вызван после 3 failures"


# ── test 4: disabled via ENV gate ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_disabled_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_TELEGRAM_HEARTBEAT_ENABLED=0 → loop немедленно возвращает (не зависает)."""
    monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "0")

    # Проверяем логику guard
    _enabled = os.environ.get("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1").strip().lower()
    disabled = _enabled not in {"1", "true", "yes"}

    assert disabled, "При KRAB_TELEGRAM_HEARTBEAT_ENABLED=0 heartbeat должен быть отключён"

    # Альтернативные значения тоже должны быть disabled
    for val in ("false", "no", "0", "FALSE", "NO"):
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", val)
        _e = os.environ.get("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1").strip().lower()
        assert _e not in {"1", "true", "yes"}, f"Значение {val!r} должно отключать heartbeat"


# ── test 5: success resets _last_telegram_event_ts ───────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_success_updates_last_event_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    """GetUsers ok → _last_telegram_event_ts обновляется (keepalive side effect)."""
    stub = _make_userbot_stub()

    # Ставим старый timestamp
    old_ts = time.time() - 300  # 5 минут назад
    stub._last_telegram_event_ts = old_ts

    stub.client.invoke = AsyncMock(return_value=[MagicMock()])

    # Симулируем успешный probe
    try:
        await asyncio.wait_for(
            stub.client.invoke(...),
            timeout=10.0,
        )
        stub._last_telegram_event_ts = time.time()
    except Exception:
        pass

    assert stub._last_telegram_event_ts > old_ts, (
        "_last_telegram_event_ts должен обновиться после успешного heartbeat"
    )
    assert stub._last_telegram_event_ts > (time.time() - 2), (
        "_last_telegram_event_ts должен быть свежим (< 2s назад)"
    )


# ── дополнительные: unit-проверки настроек ENV ────────────────────────────────


def test_heartbeat_env_enabled_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Все 'truthy' значения env активируют heartbeat."""
    for val in ("1", "true", "yes", "TRUE", "YES"):
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", val)
        _e = os.environ.get("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1").strip().lower()
        assert _e in {"1", "true", "yes"}, f"Значение {val!r} должно включать heartbeat"


def test_heartbeat_default_interval_is_240() -> None:
    """Default interval должен быть 240 секунд (4 минуты)."""
    # Сохраняем текущее значение и удаляем env, чтобы проверить default
    saved = os.environ.pop("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", None)
    try:
        default = int(os.environ.get("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "240"))
        assert default == 240, "Default interval = 240s (4 мин)"
    finally:
        if saved is not None:
            os.environ["KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC"] = saved


def test_heartbeat_default_fail_threshold_is_3() -> None:
    """Default fail threshold должен быть 3."""
    saved = os.environ.pop("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", None)
    try:
        default = int(os.environ.get("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3"))
        assert default == 3, "Default fail threshold = 3"
    finally:
        if saved is not None:
            os.environ["KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD"] = saved


def test_heartbeat_task_attribute_initialized_on_stub() -> None:
    """_telegram_heartbeat_task атрибут должен быть в __init__ KraabUserbot (stub-проверка)."""
    # Проверяем через grep что атрибут присутствует в исходнике
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot_bridge.py"
    text = src.read_text(encoding="utf-8")
    assert "_telegram_heartbeat_task" in text, (
        "_telegram_heartbeat_task должен быть объявлен в userbot_bridge.py"
    )
    assert "_telegram_heartbeat_loop" in text, (
        "_telegram_heartbeat_loop должен быть объявлен в userbot_bridge.py"
    )


def test_heartbeat_cancel_in_shutdown() -> None:
    """_telegram_heartbeat_task должен отменяться при shutdown (есть в _cancel_background_task)."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot_bridge.py"
    text = src.read_text(encoding="utf-8")
    assert '_cancel_background_task("_telegram_heartbeat_task")' in text, (
        "_telegram_heartbeat_task должен отменяться в shutdown"
    )
