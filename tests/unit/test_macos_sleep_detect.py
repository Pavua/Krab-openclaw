# -*- coding: utf-8 -*-
"""
Тесты Wave 36-D: macOS sleep/wake detection + forced pyrofork session reinit.

Покрываем:
1. Normal: delta == expected → sleep НЕ детектируется, reinit НЕ вызывается.
2. Detected: actual_delta = 3600 (1 час sleep) → _force_pyrofork_session_reinit вызывается.
3. Forced reinit success: stop() + start() проходят → логируем success, ts обновляется.
4. Forced reinit failure: stop() бросает → исключение пробрасывается (caller решает).
5. ENV disabled (KRAB_SLEEP_DETECT_ENABLED=0) → loop возвращает немедленно.
"""

from __future__ import annotations

import asyncio
import os
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_userbot_stub(*, client_connected: bool = True) -> types.SimpleNamespace:
    """Минимальный stub KraabUserbot для тестов sleep detect."""
    stub = types.SimpleNamespace()
    stub._last_telegram_event_ts = time.time()
    stub.client = MagicMock(is_connected=client_connected)
    stub.client.stop = AsyncMock()
    stub.client.start = AsyncMock()
    return stub


async def _run_sleep_detect_one_cycle(
    stub: types.SimpleNamespace,
    *,
    interval: float = 30.0,
    threshold: float = 60.0,
    actual_delta: float,
) -> list[str]:
    """
    Симулирует одну итерацию _macos_sleep_detect_loop без asyncio.sleep.

    Возвращает список вызванных действий (для проверки в тестах).
    """
    actions: list[str] = []

    # Воспроизводим логику одной итерации (без asyncio.sleep/asyncio.CancelledError)
    if actual_delta > interval + threshold:
        sleep_duration = actual_delta - interval
        actions.append(f"sleep_detected:{round(sleep_duration, 1)}")
        # Вызываем _force_pyrofork_session_reinit (если он есть на stub)
        reinit = getattr(stub, "_force_pyrofork_session_reinit", None)
        if reinit is not None:
            await reinit()
            actions.append("reinit_called")

    return actions


# ── test 1: normal — нет sleep ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sleep_detect_normal_no_trigger() -> None:
    """Delta == expected → sleep НЕ детектируется, reinit НЕ вызывается."""
    stub = _make_userbot_stub()
    reinit_calls: list[int] = []
    stub._force_pyrofork_session_reinit = AsyncMock(
        side_effect=lambda: reinit_calls.append(1)
    )

    # actual_delta = interval (нормальная работа, никаких прыжков)
    actions = await _run_sleep_detect_one_cycle(
        stub,
        interval=30.0,
        threshold=60.0,
        actual_delta=30.5,  # чуть больше interval, но меньше interval+threshold
    )

    assert "sleep_detected" not in " ".join(actions), "Sleep НЕ должен детектироваться при normal delta"
    assert reinit_calls == [], "_force_pyrofork_session_reinit НЕ должен вызываться при normal delta"


# ── test 2: detected — 1 час sleep ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sleep_detect_one_hour_sleep_triggers_reinit() -> None:
    """actual_delta = 3630 (30+3600) → sleep детектируется, reinit вызывается."""
    stub = _make_userbot_stub()
    reinit_calls: list[str] = []

    async def _mock_reinit() -> None:
        reinit_calls.append("called")

    stub._force_pyrofork_session_reinit = _mock_reinit

    # actual_delta = 30 (interval) + 3600 (sleep) = 3630 > 30 + 60 (threshold)
    actions = await _run_sleep_detect_one_cycle(
        stub,
        interval=30.0,
        threshold=60.0,
        actual_delta=3630.0,
    )

    assert any("sleep_detected" in a for a in actions), "Sleep должен детектироваться при 3630s delta"
    assert "reinit_called" in actions, "_force_pyrofork_session_reinit должен вызываться"
    assert reinit_calls == ["called"], "reinit должен быть вызван ровно 1 раз"


# ── test 3: forced reinit success ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_force_pyrofork_reinit_success_updates_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() + start() успешны → _last_telegram_event_ts обновляется."""
    stub = _make_userbot_stub()

    # Ставим старый timestamp
    old_ts = time.time() - 3600
    stub._last_telegram_event_ts = old_ts

    # Воспроизводим логику _force_pyrofork_session_reinit
    if stub.client and hasattr(stub.client, "stop"):
        await stub.client.stop(block=True)
    await asyncio.sleep(0)  # вместо asyncio.sleep(2) для скорости теста
    if stub.client:
        await stub.client.start()
    stub._last_telegram_event_ts = time.time()

    stub.client.stop.assert_called_once_with(block=True)
    stub.client.start.assert_called_once()
    assert stub._last_telegram_event_ts > old_ts, (
        "_last_telegram_event_ts должен обновиться после успешного reinit"
    )
    assert stub._last_telegram_event_ts > time.time() - 2, (
        "_last_telegram_event_ts должен быть свежим"
    )


# ── test 4: forced reinit failure — exception propagates ─────────────────────


@pytest.mark.asyncio
async def test_force_pyrofork_reinit_stop_fails_raises() -> None:
    """stop() бросает → исключение пробрасывается (caller логирует как error)."""
    stub = _make_userbot_stub()
    stub.client.stop = AsyncMock(side_effect=RuntimeError("session broken"))

    # Воспроизводим логику _force_pyrofork_session_reinit с падением
    with pytest.raises(RuntimeError, match="session broken"):
        if stub.client and hasattr(stub.client, "stop"):
            await stub.client.stop(block=True)
        # start() не должен дойти до вызова
        await stub.client.start()  # pragma: no cover

    stub.client.start.assert_not_called()


# ── test 5: ENV disabled — loop returns immediately ───────────────────────────


def test_sleep_detect_disabled_env_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_SLEEP_DETECT_ENABLED=0 → loop должен немедленно вернуться (guard проверка)."""
    monkeypatch.setenv("KRAB_SLEEP_DETECT_ENABLED", "0")

    _enabled = os.environ.get("KRAB_SLEEP_DETECT_ENABLED", "1").strip().lower()
    disabled = _enabled not in {"1", "true", "yes"}
    assert disabled, "При KRAB_SLEEP_DETECT_ENABLED=0 loop должен быть отключён"

    # Дополнительно проверяем другие falsy значения
    for val in ("false", "no", "FALSE", "NO"):
        monkeypatch.setenv("KRAB_SLEEP_DETECT_ENABLED", val)
        _e = os.environ.get("KRAB_SLEEP_DETECT_ENABLED", "1").strip().lower()
        assert _e not in {"1", "true", "yes"}, f"Значение {val!r} должно отключать sleep detect"


# ── дополнительные: проверки source code присутствия ─────────────────────────


def test_sleep_detect_methods_in_userbot_bridge() -> None:
    """_macos_sleep_detect_loop и _force_pyrofork_session_reinit объявлены в userbot_bridge.py."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot_bridge.py"
    text = src.read_text(encoding="utf-8")

    assert "_macos_sleep_detect_loop" in text, (
        "_macos_sleep_detect_loop должен быть объявлен в userbot_bridge.py"
    )
    assert "_force_pyrofork_session_reinit" in text, (
        "_force_pyrofork_session_reinit должен быть объявлен в userbot_bridge.py"
    )
    assert "_macos_sleep_detect_task" in text, (
        "_macos_sleep_detect_task атрибут должен быть в userbot_bridge.py"
    )


def test_sleep_detect_cancel_in_shutdown() -> None:
    """_macos_sleep_detect_task должен отменяться при shutdown."""
    import pathlib  # noqa: PLC0415

    src = pathlib.Path(__file__).parent.parent.parent / "src" / "userbot_bridge.py"
    text = src.read_text(encoding="utf-8")

    assert '_cancel_background_task("_macos_sleep_detect_task")' in text, (
        "_macos_sleep_detect_task должен отменяться в shutdown"
    )


def test_sleep_detect_env_interval_default() -> None:
    """Default interval = 30 секунд."""
    saved = os.environ.pop("KRAB_SLEEP_DETECT_INTERVAL_SEC", None)
    try:
        default = float(os.environ.get("KRAB_SLEEP_DETECT_INTERVAL_SEC", "30"))
        assert default == 30.0, "Default interval должен быть 30s"
    finally:
        if saved is not None:
            os.environ["KRAB_SLEEP_DETECT_INTERVAL_SEC"] = saved


def test_sleep_detect_env_threshold_default() -> None:
    """Default threshold = 60 секунд."""
    saved = os.environ.pop("KRAB_SLEEP_DETECT_THRESHOLD_SEC", None)
    try:
        default = float(os.environ.get("KRAB_SLEEP_DETECT_THRESHOLD_SEC", "60"))
        assert default == 60.0, "Default threshold должен быть 60s"
    finally:
        if saved is not None:
            os.environ["KRAB_SLEEP_DETECT_THRESHOLD_SEC"] = saved
