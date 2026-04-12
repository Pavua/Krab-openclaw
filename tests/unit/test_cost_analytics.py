# -*- coding: utf-8 -*-
"""
Тесты для CostAnalytics — подсчёт токенов, бюджет, отчёты.
"""

from __future__ import annotations

import time

import pytest

from src.core.cost_analytics import (
    CallRecord,
    CostAnalytics,
    _cost_usd,
    _is_local_model,
)

# --- Хелперы ---


def _cloud_usage(inp: int = 1000, out: int = 500) -> dict:
    """Типичный usage-словарь облачной модели."""
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}


def _make_analytics(**kwargs) -> CostAnalytics:
    """Фабрика с отключённым бюджетом по умолчанию."""
    kwargs.setdefault("monthly_budget_usd", 0.0)
    return CostAnalytics(**kwargs)


# --- _is_local_model ---


class TestIsLocalModel:
    """Локальные модели не тарифицируются."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "local-qwen-7b",
            "mlx-gemma",
            "my-model-gguf",
            "LOCAL",
        ],
    )
    def test_local_variants_detected(self, model_id: str):
        assert _is_local_model(model_id) is True

    @pytest.mark.parametrize(
        "model_id",
        [
            "google/gemini-3.1-pro",
            "openai/gpt-4o",
            "anthropic/claude-sonnet",
        ],
    )
    def test_cloud_models_not_local(self, model_id: str):
        assert _is_local_model(model_id) is False

    def test_empty_string_treated_as_local(self):
        assert _is_local_model("") is True


# --- _cost_usd ---


class TestCostUsd:
    """Расчёт стоимости вызова."""

    def test_local_model_zero_cost(self):
        assert _cost_usd("local-llama", 10_000, 5_000) == 0.0

    def test_cloud_model_positive_cost(self):
        cost = _cost_usd("google/gemini-3.1-pro", 1_000_000, 1_000_000)
        # 1M input * 0.075 + 1M output * 0.30 = 0.375
        assert cost == pytest.approx(0.375, abs=1e-6)

    def test_zero_tokens_zero_cost(self):
        assert _cost_usd("google/gemini-3.1-pro", 0, 0) == 0.0


# --- CostAnalytics.record_usage ---


class TestRecordUsage:
    """Запись и аккумуляция использования."""

    def test_single_call_accumulates_tokens(self):
        ca = _make_analytics()
        ca.record_usage(_cloud_usage(100, 50), model_id="m1")
        stats = ca.get_usage_stats()
        assert stats["input_tokens"] == 100
        assert stats["output_tokens"] == 50
        assert stats["total_tokens"] == 150

    def test_multiple_calls_sum(self):
        ca = _make_analytics()
        ca.record_usage(_cloud_usage(100, 50), model_id="m1")
        ca.record_usage(_cloud_usage(200, 100), model_id="m2")
        stats = ca.get_usage_stats()
        assert stats["input_tokens"] == 300
        assert stats["output_tokens"] == 150
        assert stats["total_tokens"] == 450

    def test_alternative_key_names(self):
        """OpenClaw иногда отдаёт input_tokens/output_tokens вместо prompt/completion."""
        ca = _make_analytics()
        ca.record_usage(
            {"input_tokens": 77, "output_tokens": 33, "total_tokens": 110}, model_id="m1"
        )
        stats = ca.get_usage_stats()
        assert stats["input_tokens"] == 77
        assert stats["output_tokens"] == 33

    def test_local_model_call_zero_cost(self):
        """Вызов локальной модели: токены считаем, стоимость = 0."""
        ca = _make_analytics()
        ca.record_usage(_cloud_usage(5000, 2000), model_id="local-qwen")
        assert ca.get_cost_so_far_usd() == 0.0
        assert ca.get_usage_stats()["total_tokens"] == 7000


# --- Бюджет ---


class TestBudget:
    """Контроль месячного бюджета."""

    def test_no_budget_always_ok(self):
        ca = _make_analytics(monthly_budget_usd=0.0)
        assert ca.check_budget_ok() is True
        assert ca.get_remaining_budget_usd() is None

    def test_budget_ok_under_limit(self):
        ca = _make_analytics(monthly_budget_usd=10.0)
        ca.record_usage(_cloud_usage(1000, 500), model_id="cloud-model")
        assert ca.check_budget_ok() is True
        remaining = ca.get_remaining_budget_usd()
        assert remaining is not None
        assert remaining > 0

    def test_budget_exceeded(self):
        """Превышение бюджета при дорогих вызовах."""
        ca = _make_analytics(monthly_budget_usd=0.0001)
        # 10M tokens = заметная стоимость
        ca.record_usage(_cloud_usage(10_000_000, 10_000_000), model_id="cloud-model")
        assert ca.check_budget_ok() is False
        assert ca.get_remaining_budget_usd() == 0.0

    def test_budget_from_env(self, monkeypatch: pytest.MonkeyPatch):
        """Бюджет из переменной окружения."""
        monkeypatch.setenv("COST_MONTHLY_BUDGET_USD", "42.5")
        ca = CostAnalytics()
        assert ca.get_monthly_budget_usd() == 42.5

    def test_invalid_env_budget_defaults_zero(self, monkeypatch: pytest.MonkeyPatch):
        """Невалидное значение env — бюджет 0 (без ограничений)."""
        monkeypatch.setenv("COST_MONTHLY_BUDGET_USD", "not-a-number")
        ca = CostAnalytics()
        assert ca.get_monthly_budget_usd() == 0.0


# --- Отчёты ---


class TestReports:
    """build_usage_report / build_usage_report_dict."""

    def test_empty_report(self):
        ca = _make_analytics()
        report = ca.build_usage_report()
        assert "Cost Analytics" in report
        assert "input=0" in report

    def test_report_contains_model_breakdown(self):
        ca = _make_analytics()
        ca.record_usage(_cloud_usage(1000, 500), model_id="google/gemini-3.1-pro")
        ca.record_usage(_cloud_usage(2000, 1000), model_id="openai/gpt-4o")
        report = ca.build_usage_report()
        assert "google/gemini-3.1-pro" in report
        assert "openai/gpt-4o" in report

    def test_report_dict_structure(self):
        """Словарный отчёт содержит все обязательные ключи."""
        ca = _make_analytics(monthly_budget_usd=5.0)
        ca.record_usage(_cloud_usage(100, 50), model_id="m1")
        d = ca.build_usage_report_dict()
        assert "input_tokens" in d
        assert "cost_session_usd" in d
        assert "by_model" in d
        assert d["budget_ok"] is True
        assert d["monthly_budget_usd"] == 5.0
        assert "m1" in d["by_model"]

    def test_report_dict_no_budget(self):
        """Без бюджета — monthly_budget_usd = None."""
        ca = _make_analytics(monthly_budget_usd=0.0)
        d = ca.build_usage_report_dict()
        assert d["monthly_budget_usd"] is None
        assert d["remaining_budget_usd"] is None


# --- Прогноз ---


class TestForecast:
    """monthly_calls_forecast."""

    def test_no_calls_returns_none(self):
        ca = _make_analytics()
        assert ca.monthly_calls_forecast() is None

    def test_with_calls_returns_positive(self):
        ca = _make_analytics()
        ca.record_usage(_cloud_usage(), model_id="cloud")
        forecast = ca.monthly_calls_forecast()
        # Хотя бы один вызов → прогноз > 0
        assert forecast is not None
        assert forecast > 0


# --- CallRecord dataclass ---


class TestCallRecord:
    """Базовые проверки датакласса."""

    def test_defaults(self):
        r = CallRecord(model_id="m", input_tokens=10, output_tokens=5, cost_usd=0.01)
        assert r.model_id == "m"
        assert r.timestamp <= time.time()
