# -*- coding: utf-8 -*-
"""
Tests for Wave 93 — cost_budget monitor.

Покрытие:
- budget evaluation (daily/weekly windows, EUR conversion);
- threshold transitions (ok → warning → critical);
- escalation logic (alert only on escalation, не на de-escalation);
- Telegram notifier (mocked) — вызывается при транзиции;
- missing data graceful (empty calls, broken provider);
- env override для бюджетов;
- /api/cost/budget endpoint smoke test через monitor.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from dataclasses import dataclass

import pytest

from src.core.cost_budget import (
    DEFAULT_DAILY_BUDGET_EUR,
    DEFAULT_WEEKLY_BUDGET_EUR,
    CostBudgetMonitor,
)


@dataclass
class _FakeCall:
    cost_usd: float
    timestamp: float


def _fixed_now() -> _dt.datetime:
    # Wed 2026-05-13 12:00 UTC (середина недели → week_start = понедельник 11-го)
    return _dt.datetime(2026, 5, 13, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _make_monitor(
    calls: list[_FakeCall] | None = None,
    *,
    daily_eur: float = 5.0,
    weekly_eur: float = 25.0,
    rate: float = 1.0,
) -> CostBudgetMonitor:
    """Хелпер: монитор с fixed clock и in-memory calls."""
    calls = calls or []
    return CostBudgetMonitor(
        daily_budget_eur=daily_eur,
        weekly_budget_eur=weekly_eur,
        usd_to_eur_rate=rate,  # 1:1 для простоты математики
        now_fn=_fixed_now,
        calls_provider=lambda: calls,
    )


# ----------------------------------------------------------------- evaluation
def test_evaluate_empty_calls_returns_ok() -> None:
    monitor = _make_monitor([])
    status = monitor.evaluate_budget_status()
    assert status.daily_used_eur == 0.0
    assert status.daily_pct == 0.0
    assert status.daily_status == "ok"
    assert status.weekly_status == "ok"


def test_evaluate_daily_window_ignores_yesterday() -> None:
    now = _fixed_now()
    yesterday_ts = (now - _dt.timedelta(days=1)).timestamp()
    today_ts = now.timestamp() - 60
    monitor = _make_monitor(
        [
            _FakeCall(cost_usd=10.0, timestamp=yesterday_ts),  # outside daily
            _FakeCall(cost_usd=1.0, timestamp=today_ts),  # inside daily + weekly
        ]
    )
    status = monitor.evaluate_budget_status()
    assert status.daily_used_eur == pytest.approx(1.0)
    # Weekly включает и вчера (вчера — внутри недели)
    assert status.weekly_used_eur == pytest.approx(11.0)


def test_evaluate_weekly_window_ignores_last_week() -> None:
    now = _fixed_now()
    last_week_ts = (now - _dt.timedelta(days=10)).timestamp()
    monitor = _make_monitor([_FakeCall(cost_usd=20.0, timestamp=last_week_ts)])
    status = monitor.evaluate_budget_status()
    assert status.weekly_used_eur == 0.0


# ----------------------------------------------------------------- thresholds
def test_threshold_classification_ok_warning_critical() -> None:
    now_ts = _fixed_now().timestamp() - 60

    # 30% → ok
    monitor = _make_monitor([_FakeCall(cost_usd=1.5, timestamp=now_ts)], daily_eur=5.0)
    assert monitor.evaluate_budget_status().daily_status == "ok"

    # 60% → warning
    monitor = _make_monitor([_FakeCall(cost_usd=3.0, timestamp=now_ts)], daily_eur=5.0)
    assert monitor.evaluate_budget_status().daily_status == "warning"

    # 85% → critical
    monitor = _make_monitor([_FakeCall(cost_usd=4.25, timestamp=now_ts)], daily_eur=5.0)
    assert monitor.evaluate_budget_status().daily_status == "critical"

    # 150% → critical (over budget — всё ещё critical bucket)
    monitor = _make_monitor([_FakeCall(cost_usd=7.5, timestamp=now_ts)], daily_eur=5.0)
    s = monitor.evaluate_budget_status()
    assert s.daily_status == "critical"
    assert s.daily_pct == pytest.approx(150.0)


def test_eur_conversion_applied() -> None:
    now_ts = _fixed_now().timestamp() - 60
    # $10 * 0.5 = €5 → 100% of €5 daily budget
    monitor = _make_monitor(
        [_FakeCall(cost_usd=10.0, timestamp=now_ts)], daily_eur=5.0, rate=0.5
    )
    status = monitor.evaluate_budget_status()
    assert status.daily_used_eur == pytest.approx(5.0)
    assert status.daily_status == "critical"


# ----------------------------------------------------------------- transitions
def test_alert_fires_on_escalation_only() -> None:
    now_ts = _fixed_now().timestamp() - 60
    calls = [_FakeCall(cost_usd=0.0, timestamp=now_ts)]
    monitor = _make_monitor(calls, daily_eur=5.0, weekly_eur=25.0)

    received: list[str] = []

    async def notifier(text: str) -> None:
        received.append(text)

    # Tick 1: ok — no alert
    asyncio.run(monitor.tick(notifier=notifier))
    assert received == []

    # Bump usage to warning (€3)
    calls[0] = _FakeCall(cost_usd=3.0, timestamp=now_ts)
    asyncio.run(monitor.tick(notifier=notifier))
    assert len(received) == 1
    assert "WARNING" in received[0]
    assert "daily" in received[0].lower() or "Daily" in received[0]

    # Same warning bucket → no new alert
    calls[0] = _FakeCall(cost_usd=3.2, timestamp=now_ts)
    asyncio.run(monitor.tick(notifier=notifier))
    assert len(received) == 1  # без изменений

    # Escalate to critical → new alert
    calls[0] = _FakeCall(cost_usd=4.5, timestamp=now_ts)
    asyncio.run(monitor.tick(notifier=notifier))
    assert len(received) == 2
    assert "CRITICAL" in received[1]


def test_no_alert_on_deescalation() -> None:
    now_ts = _fixed_now().timestamp() - 60
    calls = [_FakeCall(cost_usd=4.5, timestamp=now_ts)]
    monitor = _make_monitor(calls, daily_eur=5.0)

    received: list[str] = []

    async def notifier(text: str) -> None:
        received.append(text)

    asyncio.run(monitor.tick(notifier=notifier))  # critical → alert
    assert len(received) == 1

    # De-escalation: usage снижается до warning — алерт не должен повторно сработать
    calls[0] = _FakeCall(cost_usd=3.0, timestamp=now_ts)
    asyncio.run(monitor.tick(notifier=notifier))
    assert len(received) == 1  # без новых


def test_notifier_exception_does_not_crash_tick() -> None:
    now_ts = _fixed_now().timestamp() - 60
    monitor = _make_monitor([_FakeCall(cost_usd=4.5, timestamp=now_ts)], daily_eur=5.0)

    async def broken_notifier(text: str) -> None:
        raise RuntimeError("simulated notifier failure")

    # Не должно бросать
    status = asyncio.run(monitor.tick(notifier=broken_notifier))
    assert status.daily_status == "critical"


# ----------------------------------------------------------------- robustness
def test_missing_calls_provider_graceful() -> None:
    """Если provider кидает — статус считается как пустой (0 EUR), не падает."""

    def broken() -> list:
        raise RuntimeError("simulated calls fetch failure")

    monitor = CostBudgetMonitor(
        daily_budget_eur=5.0,
        weekly_budget_eur=25.0,
        usd_to_eur_rate=1.0,
        now_fn=_fixed_now,
        calls_provider=broken,
    )
    status = monitor.evaluate_budget_status()
    assert status.daily_used_eur == 0.0
    assert status.daily_status == "ok"


def test_zero_budget_falls_back_to_defaults() -> None:
    """Защита: budget <= 0 заменяется дефолтом, чтобы не делить на ноль."""
    monitor = CostBudgetMonitor(
        daily_budget_eur=0.0,
        weekly_budget_eur=-5.0,
        usd_to_eur_rate=0.0,
        now_fn=_fixed_now,
        calls_provider=lambda: [],
    )
    assert monitor.daily_budget_eur == DEFAULT_DAILY_BUDGET_EUR
    assert monitor.weekly_budget_eur == DEFAULT_WEEKLY_BUDGET_EUR
    assert monitor.usd_to_eur_rate > 0


def test_env_override_for_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_DAILY_BUDGET_EUR", "10.0")
    monkeypatch.setenv("KRAB_WEEKLY_BUDGET_EUR", "50.0")
    monkeypatch.setenv("KRAB_USD_TO_EUR_RATE", "0.9")
    monitor = CostBudgetMonitor(now_fn=_fixed_now, calls_provider=lambda: [])
    assert monitor.daily_budget_eur == 10.0
    assert monitor.weekly_budget_eur == 50.0
    assert monitor.usd_to_eur_rate == 0.9


def test_to_dict_serialization() -> None:
    now_ts = _fixed_now().timestamp() - 60
    monitor = _make_monitor([_FakeCall(cost_usd=2.5, timestamp=now_ts)], daily_eur=5.0)
    payload = monitor.evaluate_budget_status().to_dict()
    assert set(payload.keys()) >= {
        "daily_used_eur",
        "daily_budget_eur",
        "daily_pct",
        "daily_status",
        "weekly_used_eur",
        "weekly_budget_eur",
        "weekly_pct",
        "weekly_status",
    }
    assert payload["daily_status"] in ("ok", "warning", "critical")
