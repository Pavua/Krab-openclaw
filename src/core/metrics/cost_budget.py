# -*- coding: utf-8 -*-
"""
Wave 93: Prometheus метрики для cost budget alerting.

Gauge'ы обновляются периодически из CostBudgetMonitor.evaluate_budget_status().
prometheus_client опционален — если отсутствует, все объекты None и helper'ы no-op.
"""

from __future__ import annotations

from typing import Any

# Структура: (gauge_obj_or_None)
_daily_used_eur: Any = None
_weekly_used_eur: Any = None
_daily_pct: Any = None
_weekly_pct: Any = None

try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    _daily_used_eur = _Gauge(
        "krab_cost_daily_used_eur",
        "Daily completion cost spent so far (EUR)",
    )
    _weekly_used_eur = _Gauge(
        "krab_cost_weekly_used_eur",
        "Weekly completion cost spent so far (EUR)",
    )
    _daily_pct = _Gauge(
        "krab_cost_daily_pct",
        "Daily cost as percentage of daily budget (0..100+)",
    )
    _weekly_pct = _Gauge(
        "krab_cost_weekly_pct",
        "Weekly cost as percentage of weekly budget (0..100+)",
    )
except Exception:  # noqa: BLE001 — prometheus_client optional
    _daily_used_eur = None
    _weekly_used_eur = None
    _daily_pct = None
    _weekly_pct = None


def update_cost_budget_gauges(
    *,
    daily_used_eur: float,
    daily_pct: float,
    weekly_used_eur: float,
    weekly_pct: float,
) -> None:
    """Обновить gauge'ы текущим состоянием бюджета. Fail-safe."""
    try:
        if _daily_used_eur is not None:
            _daily_used_eur.set(max(0.0, float(daily_used_eur)))
        if _weekly_used_eur is not None:
            _weekly_used_eur.set(max(0.0, float(weekly_used_eur)))
        if _daily_pct is not None:
            _daily_pct.set(max(0.0, float(daily_pct)))
        if _weekly_pct is not None:
            _weekly_pct.set(max(0.0, float(weekly_pct)))
    except Exception:  # noqa: BLE001
        return


__all__ = ["update_cost_budget_gauges"]
