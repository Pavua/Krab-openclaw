# -*- coding: utf-8 -*-
"""
Cost Analytics — подсчёт токенов, Cost Engine, бюджет и отчёты по использованию моделей (Фаза 4.1, Шаг 4).

Вынесено из зоны model_manager для единого места аналитики затрат.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Лимит бюджета в USD в месяц (0 = не проверять)
COST_MONTHLY_BUDGET_USD_ENV = "COST_MONTHLY_BUDGET_USD"

# Цены за 1M токенов (input, output) в USD — по умолчанию для облачных моделей
# Локальные модели (local, mlx, gguf) считаем как 0
DEFAULT_PRICE_PER_1M_INPUT_USD = 0.075
DEFAULT_PRICE_PER_1M_OUTPUT_USD = 0.30


@dataclass
class CallRecord:
    """Один зафиксированный вызов модели."""
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: float = field(default_factory=time.time)


def _is_local_model(model_id: str) -> bool:
    """Локальные модели не тарифицируем."""
    if not model_id:
        return True
    low = model_id.lower()
    return "local" in low or "mlx" in low or "gguf" in low


def _cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Стоимость вызова в USD по умолчанию (облако — тариф, локаль — 0)."""
    if _is_local_model(model_id):
        return 0.0
    inp = (input_tokens / 1_000_000.0) * DEFAULT_PRICE_PER_1M_INPUT_USD
    out = (output_tokens / 1_000_000.0) * DEFAULT_PRICE_PER_1M_OUTPUT_USD
    return round(inp + out, 6)


class CostAnalytics:
    """
    Движок учёта токенов, стоимости и лимитов (Budget).
    Генерация отчётов по использованию моделей.
    """

    def __init__(
        self,
        monthly_budget_usd: Optional[float] = None,
        price_per_1m_input_usd: float = DEFAULT_PRICE_PER_1M_INPUT_USD,
        price_per_1m_output_usd: float = DEFAULT_PRICE_PER_1M_OUTPUT_USD,
    ):
        # Текущие итоги по токенам (совместимо с get_usage_stats)
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._total_tokens: int = 0
        # История вызовов для отчётов и прогноза
        self._calls: list[CallRecord] = []
        # Бюджет: None = не ограничен, иначе лимит в USD на месяц
        if monthly_budget_usd is None:
            try:
                raw = os.getenv(COST_MONTHLY_BUDGET_USD_ENV, "0").strip()
                monthly_budget_usd = float(raw) if raw else 0.0
            except ValueError:
                monthly_budget_usd = 0.0
        self._monthly_budget_usd: float = max(0.0, monthly_budget_usd)
        self._price_in = price_per_1m_input_usd
        self._price_out = price_per_1m_output_usd

    def record_usage(
        self,
        usage: dict[str, Any],
        model_id: str = "unknown",
    ) -> None:
        """
        Учитывает использование токенов и стоимость одного вызова.
        usage: словарь с ключами prompt_tokens, completion_tokens, total_tokens (как в OpenClaw).
        """
        inp = int(usage.get("prompt_tokens") or usage.get("input_tokens", 0))
        out = int(usage.get("completion_tokens") or usage.get("output_tokens", 0))
        total = int(usage.get("total_tokens", 0)) or (inp + out)
        self._input_tokens += inp
        self._output_tokens += out
        self._total_tokens += total
        cost = _cost_usd(model_id, inp, out)
        self._calls.append(
            CallRecord(
                model_id=model_id,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
            )
        )
        if cost > 0:
            logger.debug(
                "cost_recorded",
                model_id=model_id,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
            )

    def get_usage_stats(self) -> dict[str, int]:
        """Статистика токенов в формате, совместимом с openclaw_client.get_usage_stats()."""
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._total_tokens,
        }

    def get_cost_so_far_usd(self) -> float:
        """Суммарная стоимость за текущую сессию (все зафиксированные вызовы)."""
        return sum(r.cost_usd for r in self._calls)

    def get_monthly_cost_usd(self) -> float:
        """Стоимость за текущий месяц (по timestamp записей)."""
        now = time.time()
        # Упрощённо: считаем текущий месяц по локальному времени через 30 дней
        import datetime
        today = datetime.date.today()
        month_start = today.replace(day=1)
        month_start_ts = time.mktime(month_start.timetuple())
        return sum(r.cost_usd for r in self._calls if r.timestamp >= month_start_ts)

    def get_monthly_budget_usd(self) -> float:
        """Лимит бюджета в USD в месяц (0 = не задан)."""
        return self._monthly_budget_usd

    def check_budget_ok(self) -> bool:
        """Проверка: не превышен ли месячный бюджет."""
        if self._monthly_budget_usd <= 0:
            return True
        return self.get_monthly_cost_usd() < self._monthly_budget_usd

    def get_remaining_budget_usd(self) -> Optional[float]:
        """Оставшийся бюджет в USD в месяц; None если лимит не задан."""
        if self._monthly_budget_usd <= 0:
            return None
        return max(0.0, self._monthly_budget_usd - self.get_monthly_cost_usd())

    def monthly_calls_forecast(self) -> Optional[float]:
        """
        Прогноз числа вызовов в конце месяца по текущему темпу.
        Возвращает None если данных недостаточно (нет вызовов или нет дней в месяце).
        """
        if not self._calls:
            return None
        import datetime
        today = datetime.date.today()
        day_of_month = today.day
        if day_of_month <= 0:
            return None
        # Вызовов за текущий месяц
        month_start = today.replace(day=1)
        month_start_ts = time.mktime(month_start.timetuple())
        calls_this_month = [r for r in self._calls if r.timestamp >= month_start_ts]
        n_calls = len(calls_this_month)
        # Линейная экстраполяция на конец месяца
        days_in_month = 30  # упрощение
        return (n_calls / day_of_month) * days_in_month if day_of_month else None

    def build_usage_report(self) -> str:
        """Текстовый отчёт по использованию моделей и затратам."""
        lines = [
            "**Cost Analytics**",
            f"- Токены: input={self._input_tokens}, output={self._output_tokens}, total={self._total_tokens}",
            f"- Стоимость (сессия): ${self.get_cost_so_far_usd():.4f}",
            f"- Стоимость (месяц): ${self.get_monthly_cost_usd():.4f}",
        ]
        if self._monthly_budget_usd > 0:
            rem = self.get_remaining_budget_usd()
            lines.append(f"- Бюджет (месяц): ${self._monthly_budget_usd:.2f}, осталось: ${rem:.2f}")
            lines.append(f"- В пределах лимита: {'да' if self.check_budget_ok() else 'нет'}")
        forecast = self.monthly_calls_forecast()
        if forecast is not None:
            lines.append(f"- Прогноз вызовов в конце месяца: ~{int(forecast)}")
        by_model = defaultdict(lambda: {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "calls": 0})
        for r in self._calls:
            by_model[r.model_id]["tokens_in"] += r.input_tokens
            by_model[r.model_id]["tokens_out"] += r.output_tokens
            by_model[r.model_id]["cost"] += r.cost_usd
            by_model[r.model_id]["calls"] += 1
        if by_model:
            lines.append("**По моделям:**")
            for mid, data in sorted(by_model.items(), key=lambda x: -x[1]["cost"]):
                lines.append(
                    f"- {mid}: {data['calls']} вызовов, "
                    f"${data['cost']:.4f}, "
                    f"токены in/out {data['tokens_in']}/{data['tokens_out']}"
                )
        return "\n".join(lines)

    def build_usage_report_dict(self) -> dict[str, Any]:
        """Отчёт в виде словаря для API/JSON."""
        by_model = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0})
        for r in self._calls:
            by_model[r.model_id]["input_tokens"] += r.input_tokens
            by_model[r.model_id]["output_tokens"] += r.output_tokens
            by_model[r.model_id]["cost_usd"] += r.cost_usd
            by_model[r.model_id]["calls"] += 1
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._total_tokens,
            "cost_session_usd": self.get_cost_so_far_usd(),
            "cost_month_usd": self.get_monthly_cost_usd(),
            "monthly_budget_usd": self._monthly_budget_usd if self._monthly_budget_usd > 0 else None,
            "remaining_budget_usd": self.get_remaining_budget_usd(),
            "budget_ok": self.check_budget_ok(),
            "monthly_calls_forecast": self.monthly_calls_forecast(),
            "by_model": dict(by_model),
        }


# Синглтон для использования из model_manager и других модулей
cost_analytics = CostAnalytics()
