# -*- coding: utf-8 -*-
"""
Тесты алерта недоступности OpenClaw gateway (proactive_watch).

Покрываем:
1) gate KRAB_OPENCLAW_HEALTH_ALERT_ENABLED=0 — loop не запускается;
2) gateway OK — счётчик сбрасывается, нет алерта;
3) N сбоев < порога — нет алерта;
4) N сбоев >= порога — inbox item создаётся, notifier вызывается;
5) debounce: повторный алерт не отправляется в течение 30 минут;
6) recovery: после восстановления notifier получает recovery-текст, флаг сбрасывается.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.proactive_watch import ProactiveWatchService


@pytest.fixture()
def svc(tmp_path: Path) -> ProactiveWatchService:
    """ProactiveWatchService с изолированным state."""
    return ProactiveWatchService(state_path=tmp_path / "state.json")


# --------------------------------------------------------------------------- #
# 1. Gate off
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openclaw_health_alert_gate_disabled(
    svc: ProactiveWatchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При KRAB_OPENCLAW_HEALTH_ALERT_ENABLED=0 метод возвращает disabled=True без probe."""
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "0")
    probe_calls: list[str] = []

    async def _fake_probe(url: str) -> bool:
        probe_calls.append(url)
        return False

    monkeypatch.setattr(svc, "_probe_service_health", _fake_probe)
    result = await svc._check_openclaw_gateway_unreachable(
        notifier=None, _consecutive_failures=[0]
    )
    assert result.get("disabled") is True
    assert probe_calls == []  # probe не должен вызываться


# --------------------------------------------------------------------------- #
# 2. Gateway OK — сброс счётчика
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openclaw_health_ok_resets_counter(
    svc: ProactiveWatchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "1")
    monkeypatch.setattr(svc, "_probe_service_health", AsyncMock(return_value=True))
    failures = [5]  # было 5 сбоев
    result = await svc._check_openclaw_gateway_unreachable(
        notifier=None, _consecutive_failures=failures
    )
    assert result["ok"] is True
    assert failures[0] == 0


# --------------------------------------------------------------------------- #
# 3. Сбои ниже порога — нет алерта
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openclaw_health_below_threshold_no_alert(
    svc: ProactiveWatchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "1")
    monkeypatch.setattr(svc, "_probe_service_health", AsyncMock(return_value=False))
    notifier = AsyncMock()
    failures = [0]
    # Два сбоя — порог 3, алерта не должно быть
    for _ in range(2):
        result = await svc._check_openclaw_gateway_unreachable(
            notifier=notifier, _consecutive_failures=failures
        )
    assert result["alerted"] is False
    notifier.assert_not_called()
    assert failures[0] == 2


# --------------------------------------------------------------------------- #
# 4. Порог достигнут — inbox item + notifier
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openclaw_health_threshold_triggers_alert(
    svc: ProactiveWatchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "1")
    monkeypatch.setattr(svc, "_probe_service_health", AsyncMock(return_value=False))
    notifier = AsyncMock()
    upserted: list[dict[str, Any]] = []

    def _fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    monkeypatch.setattr(
        "src.core.proactive_watch.inbox_service.upsert_item", _fake_upsert
    )
    # Уже 2 сбоя — третий вызов должен сработать
    failures = [2]
    result = await svc._check_openclaw_gateway_unreachable(
        notifier=notifier, _consecutive_failures=failures
    )
    assert result["alerted"] is True
    assert failures[0] == 3
    notifier.assert_awaited_once()
    alert_text = notifier.call_args.args[0]
    assert "OpenClaw" in alert_text
    assert len(upserted) == 1
    assert upserted[0]["dedupe_key"] == "proactive:alert:openclaw_gateway_unreachable"


# --------------------------------------------------------------------------- #
# 5. Debounce: повторный алерт не отправляется
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openclaw_health_debounce_suppresses_repeat(
    svc: ProactiveWatchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "1")
    monkeypatch.setattr(svc, "_probe_service_health", AsyncMock(return_value=False))
    notifier = AsyncMock()

    monkeypatch.setattr(
        "src.core.proactive_watch.inbox_service.upsert_item", lambda **kw: None
    )

    # Симулируем что алерт уже был 1 секунду назад (debounce 1800 сек)
    state = {"openclaw_health_last_alert_ts": time.time() - 1, "openclaw_health_alert_active": True}
    svc._save_state(state)

    failures = [svc.OPENCLAW_HEALTH_FAIL_THRESHOLD]  # уже на пороге
    result = await svc._check_openclaw_gateway_unreachable(
        notifier=notifier, _consecutive_failures=failures
    )
    assert result["alerted"] is False
    notifier.assert_not_called()


# --------------------------------------------------------------------------- #
# 6. Recovery: notifier получает recovery-текст
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openclaw_health_recovery_notifies(
    svc: ProactiveWatchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "1")
    monkeypatch.setattr(svc, "_probe_service_health", AsyncMock(return_value=True))
    notifier = AsyncMock()

    # Флаг активного алерта в state
    state: dict[str, Any] = {"openclaw_health_alert_active": True}
    svc._save_state(state)

    closed: list[str] = []

    def _fake_close(dedupe_key: str, **kw: Any) -> None:
        closed.append(dedupe_key)

    monkeypatch.setattr(
        "src.core.proactive_watch.inbox_service.set_status_by_dedupe", _fake_close
    )

    failures = [5]
    result = await svc._check_openclaw_gateway_unreachable(
        notifier=notifier, _consecutive_failures=failures
    )
    assert result["ok"] is True
    assert failures[0] == 0
    notifier.assert_awaited_once()
    recovery_text = notifier.call_args.args[0]
    assert "восстановлен" in recovery_text.lower() or "✅" in recovery_text
    assert "proactive:alert:openclaw_gateway_unreachable" in closed


# --------------------------------------------------------------------------- #
# 7. Gate check — enabled by default
# --------------------------------------------------------------------------- #


def test_openclaw_health_alert_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", raising=False)
    assert ProactiveWatchService._is_openclaw_health_alert_enabled() is True


def test_openclaw_health_alert_disabled_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "0")
    assert ProactiveWatchService._is_openclaw_health_alert_enabled() is False
