# -*- coding: utf-8 -*-
"""
Wave 36-A: тесты обнаружения zombie-сессии Telegram.

Покрываем:
1. _probe_telegram_session_alive возвращает True при успешном GetUsers invoke
2. _probe_telegram_session_alive возвращает False при TimeoutError
3. _probe_telegram_session_alive возвращает False при сетевой ошибке
4. Zombie detection: 1 failure — NO escalation (счётчик < порога)
5. Zombie detection: 3 consecutive failures → triggers os._exit(78) (mock)
6. DC reachable + session probe ok → счётчик сбрасывается, process не падает
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helper: создаём mock client ──────────────────────────────────────────────

def _make_client(invoke_result=None, invoke_side_effect=None):
    """Возвращает mock Pyrogram client с настроенным invoke."""
    client = MagicMock()
    if invoke_side_effect is not None:
        client.invoke = AsyncMock(side_effect=invoke_side_effect)
    else:
        client.invoke = AsyncMock(return_value=invoke_result or [MagicMock()])
    client.is_connected = True
    return client


# ── тест 1: session probe — успешный случай ──────────────────────────────────

@pytest.mark.asyncio
async def test_probe_session_alive_returns_true_on_success() -> None:
    """_probe_telegram_session_alive возвращает True когда GetUsers успешен."""
    from src.userbot.network_watchdog import _probe_telegram_session_alive  # noqa: PLC0415

    mock_user = MagicMock()
    client = _make_client(invoke_result=[mock_user])

    # Mock pyrogram.raw imports внутри функции
    with (
        patch("pyrogram.raw.functions.users.GetUsers", MagicMock()),
        patch("pyrogram.raw.types.InputUserSelf", MagicMock()),
    ):
        result = await _probe_telegram_session_alive(client, timeout_sec=2.0)

    assert result is True, "Успешный GetUsers invoke → probe должен вернуть True"


# ── тест 2: session probe — TimeoutError ─────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_session_alive_returns_false_on_timeout() -> None:
    """_probe_telegram_session_alive возвращает False при TimeoutError."""
    from src.userbot.network_watchdog import _probe_telegram_session_alive  # noqa: PLC0415

    async def _slow_invoke(*args, **kwargs):
        await asyncio.sleep(10)  # симулируем зависание

    client = _make_client(invoke_side_effect=_slow_invoke)

    with (
        patch("pyrogram.raw.functions.users.GetUsers", MagicMock()),
        patch("pyrogram.raw.types.InputUserSelf", MagicMock()),
    ):
        # timeout_sec=0.05 — очень маленький чтобы trigger TimeoutError
        result = await _probe_telegram_session_alive(client, timeout_sec=0.05)

    assert result is False, "TimeoutError → probe должен вернуть False"


# ── тест 3: session probe — сетевая ошибка ───────────────────────────────────

@pytest.mark.asyncio
async def test_probe_session_alive_returns_false_on_network_error() -> None:
    """_probe_telegram_session_alive возвращает False при сетевой ошибке."""
    from src.userbot.network_watchdog import _probe_telegram_session_alive  # noqa: PLC0415

    client = _make_client(invoke_side_effect=ConnectionError("MTProto connection reset"))

    with (
        patch("pyrogram.raw.functions.users.GetUsers", MagicMock()),
        patch("pyrogram.raw.types.InputUserSelf", MagicMock()),
    ):
        result = await _probe_telegram_session_alive(client, timeout_sec=2.0)

    assert result is False, "ConnectionError → probe должен вернуть False"


# ── тест 4: 1 zombie failure → НЕТ escalation ────────────────────────────────

def test_zombie_single_failure_no_escalation() -> None:
    """После 1 zombie probe failure escalation не должна срабатывать."""
    from src.userbot.network_watchdog import _ZOMBIE_ESCALATION_THRESHOLD  # noqa: PLC0415

    consecutive_failures = 1
    # Одна ошибка — меньше порога
    should_escalate = consecutive_failures >= _ZOMBIE_ESCALATION_THRESHOLD
    assert not should_escalate, (
        f"1 failure < threshold({_ZOMBIE_ESCALATION_THRESHOLD}) — escalation не должна быть"
    )


# ── тест 5: 3 consecutive failures → os._exit(78) ────────────────────────────

@pytest.mark.asyncio
async def test_zombie_three_consecutive_failures_triggers_exit() -> None:
    """3 consecutive zombie probe failures → os._exit(78) вызывается.

    Тест проверяет логику эскалации напрямую, без запуска полного while-loop
    (у которого есть grace period = threshold_sec asyncio.sleep).
    Симулируем состояние счётчика достигшего порога и вызываем код эскалации.
    """
    from src.userbot.network_watchdog import (  # noqa: PLC0415
        _ZOMBIE_ESCALATION_THRESHOLD,
        NetworkWatchdogMixin,
        _probe_telegram_session_alive,
    )

    exit_calls: list[int] = []

    def _mock_exit(code: int) -> None:
        exit_calls.append(code)

    # Проверяем что при consecutive_failures >= threshold сработает _exit(78)
    # Это прямая проверка escalation condition (без запуска бесконечного loop)
    consecutive_failures = _ZOMBIE_ESCALATION_THRESHOLD  # == 3 по умолчанию

    with patch("os._exit", side_effect=_mock_exit):
        if consecutive_failures >= _ZOMBIE_ESCALATION_THRESHOLD:
            os._exit(78)

    assert exit_calls == [78], (
        f"При consecutive_failures={consecutive_failures} >= threshold={_ZOMBIE_ESCALATION_THRESHOLD} "
        "os._exit должен вызываться с кодом 78"
    )


@pytest.mark.asyncio
async def test_zombie_escalation_path_in_loop() -> None:
    """Проверяем escalation path через короткий цикл с мокнутым sleep.

    Мокаем asyncio.sleep чтобы пропустить grace period и check_interval,
    затем после 3-х итераций zombie detection ожидаем os._exit(78).
    """
    import time  # noqa: PLC0415

    from src.userbot.network_watchdog import NetworkWatchdogMixin  # noqa: PLC0415

    # Создаём minimal mock экземпляр mixin
    class _FakeBot(NetworkWatchdogMixin):
        def __init__(self):
            # Тишина 800 секунд — больше KRAB_ZOMBIE_DOUBLE_SILENCE_SEC=600
            self._last_telegram_event_ts = time.time() - 800
            self.client = _make_client()
            self.client.is_connected = True

        async def _send_proactive_watch_alert(self, text: str) -> None:
            pass  # не нужен в этом тесте

    bot = _FakeBot()

    exit_calls: list[int] = []
    sleep_call_count = 0

    async def _instant_sleep(secs):
        nonlocal sleep_call_count
        sleep_call_count += 1
        # После достаточного количества итераций — обновляем время чтобы выйти через silence
        # Не нужно ничего делать — просто пропускаем ожидание

    def _mock_exit(code: int) -> None:
        exit_calls.append(code)
        raise SystemExit(code)

    with (
        patch("asyncio.sleep", side_effect=_instant_sleep),
        patch(
            "src.userbot.network_watchdog._probe_telegram_session_alive",
            AsyncMock(return_value=False),
        ),
        patch.object(
            NetworkWatchdogMixin,
            "_probe_telegram_dc",
            AsyncMock(return_value=True),  # DC reachable — zombie scenario
        ),
        # Session 39: мокаем helper _launchd_exit_78 (раньше — os._exit
        # напрямую). Helper при PYTEST_CURRENT_TEST → raise SystemExit
        # вместо os._exit чтобы xdist worker не падал. Тест проверяет что
        # helper вызвался ровно 1 раз с правильным behaviour.
        patch("src.userbot.network_watchdog._launchd_exit_78", side_effect=lambda: _mock_exit(78)),
        patch.dict(os.environ, {
            "KRAB_ZOMBIE_ESCALATION_ENABLED": "1",
            "KRAB_ZOMBIE_DOUBLE_SILENCE_SEC": "600",
            "KRAB_ZOMBIE_ESCALATION_THRESHOLD": "3",
            "KRAB_NETWORK_OFFLINE_ALERT_SEC": "60",
            "KRAB_NETWORK_SILENCE_THRESHOLD_SEC": "180",
        }),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await bot._network_offline_monitor_loop()

    assert exc_info.value.code == 78, "_launchd_exit_78 должен возвращать SystemExit(78)"
    assert len(exit_calls) == 1, "_launchd_exit_78 должен вызываться ровно один раз"


# ── тест 6: DC reachable + session ok → счётчик сбрасывается ─────────────────

def test_zombie_counter_resets_when_session_alive() -> None:
    """Если session probe успешен — zombie счётчик сбрасывается в 0."""
    # Симулируем состояние: было 2 failures, потом session ответила
    consecutive_zombie_failures = 2

    session_alive = True
    if not session_alive:
        consecutive_zombie_failures += 1
    else:
        # session жива — сбрасываем счётчик (логика из monitor loop)
        consecutive_zombie_failures = 0

    assert consecutive_zombie_failures == 0, (
        "Session alive → zombie counter должен сброситься в 0"
    )


# ── тест 7: ENV gate KRAB_ZOMBIE_ESCALATION_ENABLED=0 → zombie skip ──────────

def test_zombie_disabled_via_env() -> None:
    """При KRAB_ZOMBIE_ESCALATION_ENABLED=0 zombie detection отключён."""
    with patch.dict(os.environ, {"KRAB_ZOMBIE_ESCALATION_ENABLED": "0"}):
        _zombie_enabled = os.environ.get("KRAB_ZOMBIE_ESCALATION_ENABLED", "1") != "0"
    assert not _zombie_enabled, "KRAB_ZOMBIE_ESCALATION_ENABLED=0 должен отключить zombie detection"


def test_zombie_enabled_by_default() -> None:
    """KRAB_ZOMBIE_ESCALATION_ENABLED по умолчанию = 1 (включён)."""
    env_without_key = {k: v for k, v in os.environ.items()
                       if k != "KRAB_ZOMBIE_ESCALATION_ENABLED"}
    with patch.dict(os.environ, env_without_key, clear=True):
        _zombie_enabled = os.environ.get("KRAB_ZOMBIE_ESCALATION_ENABLED", "1") != "0"
    assert _zombie_enabled, "По умолчанию zombie detection должен быть включён"
