# -*- coding: utf-8 -*-
"""
Тесты OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC — Wave 29-Y.

Проверяем:
1. Дефолт 90s без env.
2. Кастомное значение из env (120) respected.
3. Значение < 10 clamp-ится до 10.
4. wait_for_healthy использует переданный timeout и возвращает True при успехе.
5. wait_for_healthy возвращает False при истечении timeout.
6. Лог openclaw_slow_startup при elapsed > 30s.
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest

# ─── config: OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC ────────────────────────────────


def test_config_default_is_90(monkeypatch):
    """Без env переменной дефолт = 90."""
    monkeypatch.delenv("OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC", raising=False)
    import src.config as _cfg_mod

    importlib.reload(_cfg_mod)
    assert _cfg_mod.config.OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC == 90
    # откат — conftest.py автоматически выровняет canonical
    importlib.reload(_cfg_mod)


def test_config_custom_env_respected(monkeypatch):
    """Env=120 → значение 120."""
    monkeypatch.setenv("OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC", "120")
    import src.config as _cfg_mod

    importlib.reload(_cfg_mod)
    assert _cfg_mod.config.OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC == 120
    importlib.reload(_cfg_mod)


def test_config_clamp_below_minimum(monkeypatch):
    """Env=3 (< 10) → clamped до 10."""
    monkeypatch.setenv("OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC", "3")
    import src.config as _cfg_mod

    importlib.reload(_cfg_mod)
    assert _cfg_mod.config.OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC == 10
    importlib.reload(_cfg_mod)


def test_config_clamp_zero(monkeypatch):
    """Env=0 → clamped до 10."""
    monkeypatch.setenv("OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC", "0")
    import src.config as _cfg_mod

    importlib.reload(_cfg_mod)
    assert _cfg_mod.config.OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC == 10
    importlib.reload(_cfg_mod)


# ─── wait_for_healthy: функциональное поведение ──────────────────────────────


@pytest.mark.asyncio
async def test_wait_for_healthy_returns_true_on_success():
    """Возвращает True как только health_check возвращает True."""
    from src.openclaw_client import OpenClawClient

    client = object.__new__(OpenClawClient)
    call_count = [0]

    async def _health_ok():
        call_count[0] += 1
        return True

    client.health_check = _health_ok  # type: ignore[attr-defined]
    result = await client.wait_for_healthy(timeout=5)
    assert result is True
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_wait_for_healthy_returns_false_on_timeout():
    """Возвращает False если health_check всегда False и timeout истёк."""
    from src.openclaw_client import OpenClawClient

    client = object.__new__(OpenClawClient)

    async def _health_fail():
        return False

    client.health_check = _health_fail  # type: ignore[attr-defined]

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # Подменяем loop.time() чтобы имитировать истечение времени после 1 итерации
        times = iter([0.0, 0.0, 99.0])

        async def fast_sleep(_):
            pass

        mock_sleep.side_effect = fast_sleep

        original_get = asyncio.get_running_loop

        class _FakeLoop:
            _t = [0.0, 0.0, 99.0]
            _idx = 0

            def time(self):
                val = self._t[min(self._idx, len(self._t) - 1)]
                self._idx += 1
                return val

        fake_loop = _FakeLoop()
        with patch("asyncio.get_running_loop", return_value=fake_loop):
            result = await client.wait_for_healthy(timeout=10)

    assert result is False


@pytest.mark.asyncio
async def test_wait_for_healthy_slow_startup_log():
    """При elapsed > 30s логируется openclaw_slow_startup через structlog."""
    from src.openclaw_client import OpenClawClient

    client = object.__new__(OpenClawClient)

    async def _health_ok():
        return True

    client.health_check = _health_ok  # type: ignore[attr-defined]

    class _FakeLoop:
        """Симулирует прошедшее время > 30s."""

        _call_count = 0

        def time(self):
            self._call_count += 1
            if self._call_count == 1:
                return 0.0  # started
            return 35.0  # elapsed > 30

    logged_events: list[dict] = []

    import src.openclaw_client as _oc_mod

    original_logger = _oc_mod.logger

    class _CapturingLogger:
        def info(self, event, **kw):
            logged_events.append({"event": event, **kw})

        def warning(self, event, **kw):
            logged_events.append({"event": event, **kw})

        def error(self, event, **kw):
            logged_events.append({"event": event, **kw})

    fake_loop = _FakeLoop()
    _oc_mod.logger = _CapturingLogger()  # type: ignore[assignment]
    try:
        with patch("asyncio.get_running_loop", return_value=fake_loop):
            result = await client.wait_for_healthy(timeout=90)
    finally:
        _oc_mod.logger = original_logger

    assert result is True
    events = [e["event"] for e in logged_events]
    assert "openclaw_slow_startup" in events
