# -*- coding: utf-8 -*-
"""
Тесты для src/core/cost_analytics.py — движок учёта токенов, стоимости и бюджета.

HIGH RISK: 0 тестов ранее. Покрываем CostAnalytics полностью:
record_usage, get_cost_so_far_usd, check_budget_ok, build_usage_report,
build_usage_report_dict, monthly_calls_forecast, edge cases.
"""

from __future__ import annotations

import pytest

from src.core.cost_analytics import (
    CallRecord,
    CostAnalytics,
    _cost_usd,
    _is_local_model,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _cloud_usage(inp: int = 100, out: int = 50) -> dict:
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}


def _local_usage(inp: int = 500, out: int = 200) -> dict:
    return {"input_tokens": inp, "output_tokens": out}


# ------------------------------------------------------------------
# _is_local_model
# ------------------------------------------------------------------


class TestIsLocalModel:
    def test_local_keyword(self) -> None:
        assert _is_local_model("local/llama-3") is True

    def test_mlx_keyword(self) -> None:
        assert _is_local_model("mlx-community/phi-4") is True

    def test_gguf_keyword(self) -> None:
        assert _is_local_model("llama-3-8b.gguf") is True

    def test_cloud_model(self) -> None:
        assert _is_local_model("google/gemini-3-pro-preview") is False

    def test_empty_string(self) -> None:
        assert _is_local_model("") is True

    def test_case_insensitive(self) -> None:
        assert _is_local_model("LOCAL-model") is True


# ------------------------------------------------------------------
# _cost_usd
# ------------------------------------------------------------------


class TestCostUsd:
    def test_local_model_zero_cost(self) -> None:
        assert _cost_usd("mlx-phi", 1000, 500) == 0.0

    def test_cloud_model_positive_cost(self) -> None:
        cost = _cost_usd("google/gemini-3-pro", 1_000_000, 0)
        assert cost == pytest.approx(0.075, abs=1e-6)

    def test_output_tokens_priced_higher(self) -> None:
        cost = _cost_usd("google/gemini-3-pro", 0, 1_000_000)
        assert cost == pytest.approx(0.30, abs=1e-6)

    def test_mixed_tokens(self) -> None:
        cost = _cost_usd("openai/gpt-4", 500_000, 500_000)
        expected = 0.075 / 2 + 0.30 / 2  # 0.0375 + 0.15
        assert cost == pytest.approx(expected, abs=1e-4)


# ------------------------------------------------------------------
# CostAnalytics — основные методы
# ------------------------------------------------------------------


class TestCostAnalyticsCore:
    def test_fresh_instance_zero_cost(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=50.0)
        assert ca.get_cost_so_far_usd() == 0.0
        assert ca.get_usage_stats() == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def test_record_usage_accumulates(self) -> None:
        ca = CostAnalytics()
        ca.record_usage(_cloud_usage(100, 50), model_id="google/gemini-3-pro")
        ca.record_usage(_cloud_usage(200, 100), model_id="google/gemini-3-pro")
        stats = ca.get_usage_stats()
        assert stats["input_tokens"] == 300
        assert stats["output_tokens"] == 150
        assert stats["total_tokens"] == 450

    def test_record_usage_local_zero_cost(self) -> None:
        ca = CostAnalytics()
        ca.record_usage(_local_usage(5000, 2000), model_id="local/llama-3")
        assert ca.get_cost_so_far_usd() == 0.0
        assert ca.get_usage_stats()["input_tokens"] == 5000

    def test_cost_accumulates_across_calls(self) -> None:
        ca = CostAnalytics()
        ca.record_usage(_cloud_usage(1_000_000, 0), model_id="google/gemini-3-pro")
        ca.record_usage(_cloud_usage(0, 1_000_000), model_id="google/gemini-3-pro")
        assert ca.get_cost_so_far_usd() == pytest.approx(0.375, abs=1e-4)

    def test_record_usage_alternative_keys(self) -> None:
        """input_tokens/output_tokens вместо prompt_tokens/completion_tokens."""
        ca = CostAnalytics()
        ca.record_usage({"input_tokens": 100, "output_tokens": 50}, model_id="google/x")
        assert ca.get_usage_stats()["input_tokens"] == 100


# ------------------------------------------------------------------
# Budget
# ------------------------------------------------------------------


class TestBudget:
    def test_no_budget_always_ok(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=0.0)
        assert ca.check_budget_ok() is True
        assert ca.get_remaining_budget_usd() is None

    def test_budget_within_limit(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=100.0)
        ca.record_usage(_cloud_usage(100, 50), model_id="google/x")
        assert ca.check_budget_ok() is True

    def test_budget_exceeded(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=0.001)
        ca.record_usage(_cloud_usage(1_000_000, 1_000_000), model_id="google/x")
        assert ca.check_budget_ok() is False

    def test_remaining_budget(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=50.0)
        # Нет вызовов → осталось 50
        remaining = ca.get_remaining_budget_usd()
        assert remaining == pytest.approx(50.0, abs=1e-2)

    def test_budget_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("COST_MONTHLY_BUDGET_USD", "42.5")
        ca = CostAnalytics()
        assert ca.get_monthly_budget_usd() == 42.5

    def test_budget_env_invalid(self, monkeypatch) -> None:
        monkeypatch.setenv("COST_MONTHLY_BUDGET_USD", "not_a_number")
        ca = CostAnalytics()
        assert ca.get_monthly_budget_usd() == 0.0


# ------------------------------------------------------------------
# Forecast
# ------------------------------------------------------------------


class TestForecast:
    def test_no_calls_returns_none(self) -> None:
        ca = CostAnalytics()
        assert ca.monthly_calls_forecast() is None

    def test_with_calls_returns_number(self) -> None:
        ca = CostAnalytics()
        ca.record_usage(_cloud_usage(), model_id="google/x")
        ca.record_usage(_cloud_usage(), model_id="google/x")
        forecast = ca.monthly_calls_forecast()
        assert forecast is not None
        assert forecast >= 2  # минимум текущие вызовы экстраполяция


# ------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------


class TestReports:
    def test_build_usage_report_text(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=10.0)
        ca.record_usage(_cloud_usage(500, 200), model_id="google/gemini-3-pro")
        report = ca.build_usage_report()
        assert "Cost Analytics" in report
        assert "google/gemini-3-pro" in report
        assert "Бюджет" in report

    def test_build_usage_report_dict_structure(self) -> None:
        ca = CostAnalytics()
        ca.record_usage(_cloud_usage(100, 50), model_id="google/gemini-3-pro")
        d = ca.build_usage_report_dict()
        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert "by_model" in d
        assert "google/gemini-3-pro" in d["by_model"]
        assert d["by_model"]["google/gemini-3-pro"]["calls"] == 1

    def test_empty_report_dict(self) -> None:
        ca = CostAnalytics()
        d = ca.build_usage_report_dict()
        assert d["input_tokens"] == 0
        assert d["cost_session_usd"] == 0.0
        assert d["by_model"] == {}

    def test_report_dict_budget_fields(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=50.0)
        d = ca.build_usage_report_dict()
        assert d["monthly_budget_usd"] == 50.0
        assert d["budget_ok"] is True
        assert d["remaining_budget_usd"] == pytest.approx(50.0, abs=1e-2)

    def test_report_no_budget_fields(self) -> None:
        ca = CostAnalytics(monthly_budget_usd=0.0)
        d = ca.build_usage_report_dict()
        assert d["monthly_budget_usd"] is None
        assert d["remaining_budget_usd"] is None
        assert d["budget_ok"] is True


# ------------------------------------------------------------------
# CallRecord dataclass
# ------------------------------------------------------------------


class TestCallRecord:
    def test_defaults(self) -> None:
        r = CallRecord(model_id="x", input_tokens=10, output_tokens=5, cost_usd=0.001)
        assert r.model_id == "x"
        assert r.timestamp > 0
