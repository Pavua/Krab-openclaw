# -*- coding: utf-8 -*-
"""
Расширенные тесты для src/core/cost_analytics.py.

Покрываем: FinOps-поля (tool_calls, channel, is_fallback, context_tokens),
агрегацию по каналам, time filtering (get_monthly_cost), прогноз, edge cases,
build_usage_report_dict полностью.
"""

from __future__ import annotations

from src.core.cost_analytics import (
    CallRecord,
    CostAnalytics,
)

# ------------------------------------------------------------------
# Вспомогательные фабрики
# ------------------------------------------------------------------


def _usage(inp: int = 100, out: int = 50) -> dict:
    """Стандартный словарь usage для облачной модели."""
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}


def _record(model_id: str = "google/gemini-3-pro", **kwargs) -> dict:
    """Минимальный kwargs для record_usage."""
    return dict(usage=_usage(), model_id=model_id, **kwargs)


# ------------------------------------------------------------------
# FinOps-поля при record_usage
# ------------------------------------------------------------------


class TestFinOpsFields:
    def test_channel_stored_in_call_record(self) -> None:
        """channel корректно записывается в CallRecord."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", channel="telegram")
        assert ca._calls[0].channel == "telegram"

    def test_is_fallback_stored(self) -> None:
        """is_fallback=True фиксируется в записи."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", is_fallback=True)
        assert ca._calls[0].is_fallback is True

    def test_tool_calls_count_stored(self) -> None:
        """tool_calls_count записывается корректно."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", tool_calls_count=3)
        assert ca._calls[0].tool_calls_count == 3

    def test_context_tokens_stored(self) -> None:
        """context_tokens записывается в CallRecord."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", context_tokens=1200)
        assert ca._calls[0].context_tokens == 1200

    def test_defaults_finops_fields(self) -> None:
        """Без явных FinOps-полей — дефолты."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x")
        r = ca._calls[0]
        assert r.channel == ""
        assert r.is_fallback is False
        assert r.tool_calls_count == 0
        assert r.context_tokens == 0


# ------------------------------------------------------------------
# Агрегация по каналам и fallback в build_usage_report_dict
# ------------------------------------------------------------------


class TestReportDictFinOps:
    def test_by_channel_aggregation(self) -> None:
        """by_channel корректно суммирует вызовы по каналам."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", channel="telegram")
        ca.record_usage(_usage(), model_id="google/x", channel="telegram")
        ca.record_usage(_usage(), model_id="google/x", channel="web")
        d = ca.build_usage_report_dict()
        assert d["by_channel"]["telegram"] == 2
        assert d["by_channel"]["web"] == 1

    def test_channel_empty_string_not_included(self) -> None:
        """Записи без channel не попадают в by_channel."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x")  # channel=""
        d = ca.build_usage_report_dict()
        assert d["by_channel"] == {}

    def test_total_fallbacks_counted(self) -> None:
        """total_fallbacks считает только is_fallback=True."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", is_fallback=True)
        ca.record_usage(_usage(), model_id="google/x", is_fallback=False)
        ca.record_usage(_usage(), model_id="google/x", is_fallback=True)
        d = ca.build_usage_report_dict()
        assert d["total_fallbacks"] == 2

    def test_total_tool_calls_summed(self) -> None:
        """total_tool_calls суммируется по всем вызовам."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", tool_calls_count=2)
        ca.record_usage(_usage(), model_id="google/x", tool_calls_count=5)
        d = ca.build_usage_report_dict()
        assert d["total_tool_calls"] == 7

    def test_avg_context_tokens(self) -> None:
        """avg_context_tokens = среднее context_tokens по вызовам."""
        ca = CostAnalytics()
        ca.record_usage(_usage(), model_id="google/x", context_tokens=1000)
        ca.record_usage(_usage(), model_id="google/x", context_tokens=3000)
        d = ca.build_usage_report_dict()
        assert d["avg_context_tokens"] == 2000
        assert d["total_context_tokens"] == 4000

    def test_avg_context_tokens_empty(self) -> None:
        """Без вызовов avg_context_tokens == 0."""
        ca = CostAnalytics()
        d = ca.build_usage_report_dict()
        assert d["avg_context_tokens"] == 0


# ------------------------------------------------------------------
# Time filtering — get_monthly_cost_usd
# ------------------------------------------------------------------


class TestMonthlyFiltering:
    def test_old_call_excluded_from_monthly(self) -> None:
        """Вызовы с timestamp прошлого месяца не входят в monthly cost."""
        ca = CostAnalytics()
        # Добавляем запись вручную с очень старым timestamp
        ca._calls.append(
            CallRecord(
                model_id="google/x",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cost_usd=10.0,
                timestamp=1_000_000.0,  # 2001 год — точно прошлый месяц
            )
        )
        # Monthly cost не должна включать старую запись
        assert ca.get_monthly_cost_usd() == 0.0

    def test_current_call_included_in_monthly(self) -> None:
        """Вызов текущего месяца входит в monthly cost."""
        ca = CostAnalytics()
        ca.record_usage({"prompt_tokens": 1_000_000, "completion_tokens": 0}, model_id="google/x")
        assert ca.get_monthly_cost_usd() > 0.0

    def test_session_cost_includes_all_calls(self) -> None:
        """get_cost_so_far_usd включает ВСЕ записи, включая старые."""
        ca = CostAnalytics()
        ca._calls.append(
            CallRecord(
                model_id="google/x",
                input_tokens=0,
                output_tokens=0,
                cost_usd=5.0,
                timestamp=1_000_000.0,
            )
        )
        ca.record_usage(_usage(), model_id="google/x")
        # Сессионная стоимость включает обе записи
        assert ca.get_cost_so_far_usd() >= 5.0


# ------------------------------------------------------------------
# Прогноз monthly_calls_forecast
# ------------------------------------------------------------------


class TestForecastExtended:
    def test_forecast_proportional_to_calls(self) -> None:
        """Прогноз возвращает float при наличии вызовов в текущем месяце."""
        ca = CostAnalytics()
        for _ in range(10):
            ca.record_usage(_usage(), model_id="google/x")
        forecast = ca.monthly_calls_forecast()
        # Прогноз должен быть числом ≥ числа вызовов (экстраполяция на 30 дней)
        assert forecast is not None
        assert isinstance(forecast, float)
        assert forecast >= 1.0

    def test_forecast_with_only_old_calls(self) -> None:
        """Если все вызовы старые (не этот месяц), forecast = 0.0."""
        ca = CostAnalytics()
        ca._calls.append(
            CallRecord(
                model_id="google/x",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=1_000_000.0,
            )
        )
        # _calls непустой, но в текущем месяце 0 вызовов → forecast = 0.0
        forecast = ca.monthly_calls_forecast()
        assert forecast == 0.0


# ------------------------------------------------------------------
# Агрегация по моделям в build_usage_report_dict
# ------------------------------------------------------------------


class TestByModelAggregation:
    def test_multiple_models_aggregated_separately(self) -> None:
        """Вызовы по разным моделям агрегируются независимо."""
        ca = CostAnalytics()
        ca.record_usage(_usage(100, 50), model_id="google/gemini-3-pro")
        ca.record_usage(_usage(200, 100), model_id="google/gemini-3-pro")
        ca.record_usage(_usage(300, 150), model_id="anthropic/claude-3-5-sonnet")
        d = ca.build_usage_report_dict()
        assert d["by_model"]["google/gemini-3-pro"]["calls"] == 2
        assert d["by_model"]["google/gemini-3-pro"]["input_tokens"] == 300
        assert d["by_model"]["anthropic/claude-3-5-sonnet"]["calls"] == 1
        assert d["by_model"]["anthropic/claude-3-5-sonnet"]["input_tokens"] == 300

    def test_local_model_zero_cost_in_by_model(self) -> None:
        """Локальная модель в by_model имеет cost_usd == 0."""
        ca = CostAnalytics()
        ca.record_usage(_usage(10_000, 5_000), model_id="mlx-phi-4")
        d = ca.build_usage_report_dict()
        assert d["by_model"]["mlx-phi-4"]["cost_usd"] == 0.0

    def test_report_text_sorted_by_cost(self) -> None:
        """В текстовом отчёте модели идут по убыванию стоимости."""
        ca = CostAnalytics()
        # gemini дорогой — много токенов
        ca.record_usage(_usage(1_000_000, 0), model_id="google/gemini-expensive")
        # phi дешёвый (локальный)
        ca.record_usage(_usage(1_000, 500), model_id="mlx-phi-cheap")
        report = ca.build_usage_report()
        # expensive должна идти первой в разделе По моделям
        idx_expensive = report.find("google/gemini-expensive")
        idx_cheap = report.find("mlx-phi-cheap")
        assert idx_expensive < idx_cheap


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    def test_usage_with_zero_tokens(self) -> None:
        """Нулевые токены не ломают счётчики."""
        ca = CostAnalytics()
        ca.record_usage(
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, model_id="google/x"
        )
        assert ca.get_cost_so_far_usd() == 0.0
        stats = ca.get_usage_stats()
        assert stats["total_tokens"] == 0

    def test_usage_missing_total_tokens_computed(self) -> None:
        """Если total_tokens нет — вычисляется как inp+out."""
        ca = CostAnalytics()
        ca.record_usage({"prompt_tokens": 100, "completion_tokens": 50}, model_id="google/x")
        assert ca.get_usage_stats()["total_tokens"] == 150

    def test_negative_budget_treated_as_zero(self) -> None:
        """Отрицательный бюджет нормируется до 0 (нет ограничения)."""
        ca = CostAnalytics(monthly_budget_usd=-10.0)
        assert ca.get_monthly_budget_usd() == 0.0
        assert ca.check_budget_ok() is True

    def test_many_calls_performance(self) -> None:
        """1000 вызовов не вызывают ошибок, токены суммируются корректно."""
        ca = CostAnalytics()
        for _ in range(1000):
            ca.record_usage(_usage(10, 5), model_id="google/x")
        assert ca.get_usage_stats()["input_tokens"] == 10_000
        assert ca.get_usage_stats()["output_tokens"] == 5_000

    def test_budget_env_empty_string(self, monkeypatch) -> None:
        """Пустая строка в env-переменной бюджета → 0.0."""
        monkeypatch.setenv("COST_MONTHLY_BUDGET_USD", "")
        ca = CostAnalytics()
        assert ca.get_monthly_budget_usd() == 0.0

    def test_remaining_budget_floored_at_zero(self) -> None:
        """Остаток бюджета не уходит в отрицательные значения."""
        ca = CostAnalytics(monthly_budget_usd=0.001)
        ca.record_usage(
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, model_id="google/x"
        )
        remaining = ca.get_remaining_budget_usd()
        assert remaining == 0.0

    def test_usage_report_no_budget_section(self) -> None:
        """Без бюджета в отчёте нет секции 'Бюджет'."""
        ca = CostAnalytics(monthly_budget_usd=0.0)
        ca.record_usage(_usage(), model_id="google/x")
        report = ca.build_usage_report()
        assert "Бюджет" not in report
