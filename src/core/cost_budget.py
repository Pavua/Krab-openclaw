# -*- coding: utf-8 -*-
"""
Wave 93: Cost Budget Monitor — daily/weekly EUR-caps + threshold alerts.

Расширяет cost_analytics (USD-учёт) — конвертирует в EUR и сравнивает с
суточным/недельным бюджетом. При переходе через пороги (50% / 80% / 100%)
логирует событие и (опционально) шлёт Telegram-алерт владельцу.

Threshold buckets:
    ok        < 50%
    warning   50–80%
    critical  > 80%

Singleton: `cost_budget_monitor` в конце модуля.
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import structlog

from .metrics.cost_budget import update_cost_budget_gauges

logger = structlog.get_logger(__name__)

# Дефолтные курсы и бюджеты ---------------------------------------------------
DEFAULT_USD_TO_EUR_RATE = 0.92  # ~май 2026; перекрывается KRAB_USD_TO_EUR_RATE
DEFAULT_DAILY_BUDGET_EUR = 5.0
DEFAULT_WEEKLY_BUDGET_EUR = 25.0

# Threshold пороги (%)
_WARNING_PCT = 50.0
_CRITICAL_PCT = 80.0

# Опциональный callback: async (text) -> None — отправка Telegram DM владельцу
OwnerNotifier = Callable[[str], Awaitable[None]]


@dataclass
class BudgetStatus:
    """Snapshot статуса бюджета на момент evaluate()."""

    daily_used_eur: float
    daily_budget_eur: float
    daily_pct: float
    daily_status: str  # ok | warning | critical

    weekly_used_eur: float
    weekly_budget_eur: float
    weekly_pct: float
    weekly_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_used_eur": round(self.daily_used_eur, 4),
            "daily_budget_eur": self.daily_budget_eur,
            "daily_pct": round(self.daily_pct, 2),
            "daily_status": self.daily_status,
            "weekly_used_eur": round(self.weekly_used_eur, 4),
            "weekly_budget_eur": self.weekly_budget_eur,
            "weekly_pct": round(self.weekly_pct, 2),
            "weekly_status": self.weekly_status,
        }


def _classify(pct: float) -> str:
    if pct >= _CRITICAL_PCT:
        return "critical"
    if pct >= _WARNING_PCT:
        return "warning"
    return "ok"


def _read_float_env(name: str, default: float) -> float:
    """Безопасное чтение float из env, без падения на пустую/мусорную строку."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("cost_budget_env_invalid", env=name, raw=raw)
        return default


class CostBudgetMonitor:
    """
    Cost Budget Monitor — Wave 93.

    Использование:
        monitor = CostBudgetMonitor()
        status = monitor.evaluate_budget_status()
        await monitor.tick(notifier=...)  # background loop step
    """

    def __init__(
        self,
        *,
        daily_budget_eur: Optional[float] = None,
        weekly_budget_eur: Optional[float] = None,
        usd_to_eur_rate: Optional[float] = None,
        now_fn: Optional[Callable[[], _dt.datetime]] = None,
        calls_provider: Optional[Callable[[], list[Any]]] = None,
    ) -> None:
        self.daily_budget_eur: float = (
            daily_budget_eur
            if daily_budget_eur is not None
            else _read_float_env("KRAB_DAILY_BUDGET_EUR", DEFAULT_DAILY_BUDGET_EUR)
        )
        self.weekly_budget_eur: float = (
            weekly_budget_eur
            if weekly_budget_eur is not None
            else _read_float_env("KRAB_WEEKLY_BUDGET_EUR", DEFAULT_WEEKLY_BUDGET_EUR)
        )
        self.usd_to_eur_rate: float = (
            usd_to_eur_rate
            if usd_to_eur_rate is not None
            else _read_float_env("KRAB_USD_TO_EUR_RATE", DEFAULT_USD_TO_EUR_RATE)
        )
        # Защита: budget должен быть положительным; иначе деление на ноль и алерты теряют смысл
        if self.daily_budget_eur <= 0:
            self.daily_budget_eur = DEFAULT_DAILY_BUDGET_EUR
        if self.weekly_budget_eur <= 0:
            self.weekly_budget_eur = DEFAULT_WEEKLY_BUDGET_EUR
        if self.usd_to_eur_rate <= 0:
            self.usd_to_eur_rate = DEFAULT_USD_TO_EUR_RATE

        self._now_fn = now_fn or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._calls_provider = calls_provider  # для тестов: возвращает list[CallRecord-like]
        # Запоминаем последний отправленный статус — алерт только при ТРАНЗИЦИИ
        self._last_daily_status: str = "ok"
        self._last_weekly_status: str = "ok"

    # ------------------------------------------------------------------ helpers
    def _get_calls(self) -> list[Any]:
        """Достаёт список CallRecord; провайдер можно подменить в тестах."""
        if self._calls_provider is not None:
            try:
                return list(self._calls_provider())
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cost_budget_calls_provider_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return []
        try:
            from .cost_analytics import cost_analytics as _ca  # noqa: PLC0415

            return list(_ca._calls)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cost_budget_calls_fetch_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    def _sum_usd_since(self, since_ts: float) -> float:
        total = 0.0
        for r in self._get_calls():
            ts = getattr(r, "timestamp", None)
            cost = getattr(r, "cost_usd", None)
            if ts is None or cost is None:
                continue
            if ts >= since_ts:
                total += float(cost)
        return total

    # ----------------------------------------------------------------- evaluate
    def evaluate_budget_status(self) -> BudgetStatus:
        """Возвращает текущий статус бюджета. Никаких side-effects (no alerts)."""
        now = self._now_fn()
        # Окно дня — с полуночи UTC текущей даты
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Окно недели — с понедельника 00:00 UTC
        week_start = day_start - _dt.timedelta(days=day_start.weekday())

        day_start_ts = day_start.timestamp()
        week_start_ts = week_start.timestamp()

        daily_usd = self._sum_usd_since(day_start_ts)
        weekly_usd = self._sum_usd_since(week_start_ts)

        daily_eur = daily_usd * self.usd_to_eur_rate
        weekly_eur = weekly_usd * self.usd_to_eur_rate

        daily_pct = (
            (daily_eur / self.daily_budget_eur) * 100.0 if self.daily_budget_eur > 0 else 0.0
        )
        weekly_pct = (
            (weekly_eur / self.weekly_budget_eur) * 100.0 if self.weekly_budget_eur > 0 else 0.0
        )

        return BudgetStatus(
            daily_used_eur=daily_eur,
            daily_budget_eur=self.daily_budget_eur,
            daily_pct=daily_pct,
            daily_status=_classify(daily_pct),
            weekly_used_eur=weekly_eur,
            weekly_budget_eur=self.weekly_budget_eur,
            weekly_pct=weekly_pct,
            weekly_status=_classify(weekly_pct),
        )

    # ---------------------------------------------------------------- tick
    async def tick(self, *, notifier: Optional[OwnerNotifier] = None) -> BudgetStatus:
        """
        Один шаг фонового цикла:
          1. Считает статус.
          2. Обновляет Prometheus gauges.
          3. При транзиции ok→warning|critical, warning→critical логирует
             + опционально шлёт Telegram-алерт владельцу.
        """
        status = self.evaluate_budget_status()
        update_cost_budget_gauges(
            daily_used_eur=status.daily_used_eur,
            daily_pct=status.daily_pct,
            weekly_used_eur=status.weekly_used_eur,
            weekly_pct=status.weekly_pct,
        )

        # Детектим эскалацию: ok→warning, ok→critical, warning→critical
        if self._is_escalation(self._last_daily_status, status.daily_status):
            await self._fire_alert(
                scope="daily",
                used=status.daily_used_eur,
                budget=status.daily_budget_eur,
                pct=status.daily_pct,
                new_status=status.daily_status,
                notifier=notifier,
            )
        if self._is_escalation(self._last_weekly_status, status.weekly_status):
            await self._fire_alert(
                scope="weekly",
                used=status.weekly_used_eur,
                budget=status.weekly_budget_eur,
                pct=status.weekly_pct,
                new_status=status.weekly_status,
                notifier=notifier,
            )

        self._last_daily_status = status.daily_status
        self._last_weekly_status = status.weekly_status
        return status

    @staticmethod
    def _is_escalation(prev: str, current: str) -> bool:
        """True если транзиция повышает severity (ok→warning, ok→critical, warning→critical)."""
        order = {"ok": 0, "warning": 1, "critical": 2}
        return order.get(current, 0) > order.get(prev, 0)

    async def _fire_alert(
        self,
        *,
        scope: str,
        used: float,
        budget: float,
        pct: float,
        new_status: str,
        notifier: Optional[OwnerNotifier],
    ) -> None:
        """Логирует + (опционально) шлёт Telegram-алерт владельцу."""
        emoji = "🔥" if new_status == "critical" else "⚠️"
        text = (
            f"{emoji} {scope.capitalize()} budget {new_status.upper()}: "
            f"€{used:.2f}/€{budget:.2f} ({pct:.0f}%)"
        )
        logger.warning(
            "cost_budget_alert_triggered",
            scope=scope,
            new_status=new_status,
            used_eur=round(used, 4),
            budget_eur=budget,
            pct=round(pct, 2),
        )
        if notifier is None:
            return
        try:
            await notifier(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cost_budget_notifier_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ---------------------------------------------------------------- loop
    async def run_loop(
        self,
        *,
        notifier: Optional[OwnerNotifier] = None,
        interval_sec: int = 300,
        stop_after_iters: Optional[int] = None,
    ) -> None:
        """
        Фоновый loop: каждые `interval_sec` секунд (default 5 мин) вызывает tick().

        `stop_after_iters` — для тестов: завершиться после N итераций.
        """
        import asyncio  # noqa: PLC0415

        iters = 0
        while True:
            try:
                await self.tick(notifier=notifier)
            except Exception as exc:  # noqa: BLE001
                import traceback  # noqa: PLC0415

                logger.warning(
                    "cost_budget_tick_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
            iters += 1
            if stop_after_iters is not None and iters >= stop_after_iters:
                return
            await asyncio.sleep(max(1, int(interval_sec)))

    # Test helper: сбросить запомненные статусы
    def reset_status_memory(self) -> None:
        self._last_daily_status = "ok"
        self._last_weekly_status = "ok"


# Singleton -------------------------------------------------------------------
cost_budget_monitor = CostBudgetMonitor()


def _epoch_now() -> float:
    """Текущий unix timestamp (UTC)."""
    return time.time()


__all__ = [
    "BudgetStatus",
    "CostBudgetMonitor",
    "cost_budget_monitor",
    "DEFAULT_DAILY_BUDGET_EUR",
    "DEFAULT_WEEKLY_BUDGET_EUR",
    "DEFAULT_USD_TO_EUR_RATE",
]
