# -*- coding: utf-8 -*-
"""
Тесты для FinOps (cost_analytics) и policy (runtime_policy) модулей.
"""

from __future__ import annotations

import os
import time

import pytest

from src.core.cost_analytics import (
    CostAnalytics,
    CallRecord,
    _cost_usd,
    _is_local_model,
)
from src.core.runtime_policy import (
    current_runtime_mode,
    runtime_mode_release_safe,
    provider_runtime_policy,
)


# ── Вспомогательная фабрика usage-словарей ──────────────────────────────────

def make_usage(inp: int = 1000, out: int = 500) -> dict:
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}


# ── cost_analytics: базовые вычисления ──────────────────────────────────────

class TestCostCalculations:
    """Юнит-тесты расчёта стоимости вызовов."""

    def test_local_model_cost_zero(self):
        """Локальные модели (local/mlx/gguf) должны стоить $0."""
        assert _cost_usd("lmstudio-local-model", 100_000, 50_000) == 0.0
        assert _cost_usd("gemma-mlx-q4", 100_000, 50_000) == 0.0
        assert _cost_usd("llama3-gguf", 100_000, 50_000) == 0.0

    def test_cloud_model_cost_positive(self):
        """Облачные модели должны давать ненулевую стоимость."""
        cost = _cost_usd("google/gemini-3-pro", 1_000_000, 500_000)
        assert cost > 0.0

    def test_cost_formula(self):
        """Проверяем точную формулу: 1M input = $0.075, 1M output = $0.30."""
        cost = _cost_usd("openai/gpt-4o", 1_000_000, 1_000_000)
        expected = 0.075 + 0.30  # $0.375
        assert abs(cost - expected) < 1e-5

    def test_is_local_model_detection(self):
        """_is_local_model должен правильно детектировать локальные модели."""
        assert _is_local_model("my-local-llama") is True
        assert _is_local_model("phi3-mlx-q8") is True
        assert _is_local_model("mistral-gguf-4bit") is True
        assert _is_local_model("google/gemini-3-flash") is False
        assert _is_local_model("") is True  # пустой ID — считаем локальным


# ── CostAnalytics: отслеживание бюджета ─────────────────────────────────────

class TestBudgetTracking:
    """Тесты бюджетного контроля CostAnalytics."""

    def test_no_budget_always_ok(self):
        """Без заданного бюджета check_budget_ok всегда True."""
        ca = CostAnalytics(monthly_budget_usd=0.0)
        ca.record_usage(make_usage(1_000_000, 1_000_000), model_id="openai/gpt-4o")
        assert ca.check_budget_ok() is True
        assert ca.get_remaining_budget_usd() is None

    def test_budget_within_limit(self):
        """Небольшой расход при большом бюджете — лимит не превышен."""
        ca = CostAnalytics(monthly_budget_usd=100.0)
        ca.record_usage(make_usage(1000, 500), model_id="openai/gpt-4o")
        assert ca.check_budget_ok() is True
        remaining = ca.get_remaining_budget_usd()
        assert remaining is not None
        assert remaining > 99.0

    def test_budget_exceeded(self):
        """При превышении лимита check_budget_ok должен вернуть False."""
        ca = CostAnalytics(monthly_budget_usd=0.0001)
        # Записываем огромный вызов чтобы превысить крошечный лимит
        ca.record_usage(make_usage(10_000_000, 5_000_000), model_id="openai/gpt-4o")
        # Стоимость будет >>> $0.0001, но get_monthly_cost_usd() считает по текущему месяцу
        # Поэтому устанавливаем бюджет выше нуля, но меньше реального cost_session
        ca2 = CostAnalytics(monthly_budget_usd=0.001)
        ca2.record_usage(make_usage(10_000_000, 5_000_000), model_id="openai/gpt-4o")
        # cost = (10M/1M)*0.075 + (5M/1M)*0.30 = 0.75 + 1.5 = $2.25 >> $0.001
        assert ca2.check_budget_ok() is False

    def test_record_usage_accumulates_tokens(self):
        """record_usage должен накапливать токены корректно."""
        ca = CostAnalytics()
        ca.record_usage(make_usage(100, 50), model_id="google/gemini-3-flash")
        ca.record_usage(make_usage(200, 100), model_id="google/gemini-3-flash")
        stats = ca.get_usage_stats()
        assert stats["input_tokens"] == 300
        assert stats["output_tokens"] == 150
        assert stats["total_tokens"] == 450

    def test_record_usage_finops_fields(self):
        """FinOps-поля (channel, is_fallback, tool_calls_count) попадают в отчёт."""
        ca = CostAnalytics()
        ca.record_usage(
            make_usage(500, 200),
            model_id="google/gemini-3-pro",
            tool_calls_count=3,
            channel="telegram",
            is_fallback=True,
            context_tokens=800,
        )
        report = ca.build_usage_report_dict()
        assert report["total_tool_calls"] == 3
        assert report["total_fallbacks"] == 1
        assert report["total_context_tokens"] == 800
        assert report["by_channel"]["telegram"] == 1

    def test_cost_session_vs_local_model(self):
        """Вызовы локальной модели не увеличивают стоимость сессии."""
        ca = CostAnalytics()
        ca.record_usage(make_usage(100_000, 50_000), model_id="local-llama3")
        assert ca.get_cost_so_far_usd() == 0.0

    def test_build_usage_report_dict_structure(self):
        """build_usage_report_dict должен возвращать все обязательные ключи."""
        ca = CostAnalytics(monthly_budget_usd=10.0)
        ca.record_usage(make_usage(1000, 500), model_id="openai/gpt-4o")
        report = ca.build_usage_report_dict()
        required_keys = [
            "input_tokens", "output_tokens", "total_tokens",
            "cost_session_usd", "cost_month_usd", "monthly_budget_usd",
            "remaining_budget_usd", "budget_ok", "by_model",
            "total_tool_calls", "total_fallbacks", "by_channel",
        ]
        for key in required_keys:
            assert key in report, f"Отсутствует ключ: {key}"


# ── PolicyMatrix: runtime_policy ────────────────────────────────────────────

class TestPolicyMatrix:
    """Тесты построения policy-матрицы провайдеров."""

    def test_google_release_safe(self):
        """google провайдер должен быть release_safe=True по умолчанию."""
        policy = provider_runtime_policy("google")
        assert policy["release_safe"] is True
        assert policy["primary_policy"] == "release-safe"

    def test_google_antigravity_blocked(self):
        """google-antigravity должен иметь primary_policy=blocked."""
        policy = provider_runtime_policy("google-antigravity")
        assert policy["primary_policy"] == "blocked"
        assert policy["release_safe"] is False

    def test_legacy_flag_downgrades_stability(self):
        """legacy=True должен снижать stability_score и ставить lab-only политику."""
        policy = provider_runtime_policy("google", legacy=True)
        assert policy["primary_policy"] == "lab-only"
        assert policy["stability_score"] <= 0.2

    def test_blocked_readiness_reduces_stability(self):
        """readiness=blocked без helper снижает stability и устанавливает unavailable."""
        policy = provider_runtime_policy(
            "openai", readiness="blocked", helper_available=False, auth_mode=""
        )
        assert policy["login_state"] == "unavailable"
        assert policy["stability_score"] < 0.8  # исходный балл 0.8, должен упасть

    def test_blocked_readiness_with_oauth_login_required(self):
        """readiness=blocked + auth_mode=oauth → login_required при helper=True."""
        policy = provider_runtime_policy(
            "openai", readiness="blocked", helper_available=True, auth_mode="oauth"
        )
        assert policy["login_state"] == "login_required"

    def test_quota_exhausted_penalty(self):
        """quota_state=exhausted должен дополнительно снижать stability."""
        policy_normal = provider_runtime_policy("google", readiness="ready")
        policy_exhausted = provider_runtime_policy(
            "google", readiness="ready", quota_state="exhausted"
        )
        assert policy_exhausted["stability_score"] < policy_normal["stability_score"]

    def test_stability_score_clamped(self):
        """stability_score всегда в диапазоне [0.05, 0.99]."""
        # Максимальные штрафы
        policy = provider_runtime_policy(
            "qwen-portal",
            readiness="blocked",
            legacy=True,
            quota_state="exhausted",
            oauth_status="expired",
            auth_mode="oauth",
        )
        score = policy["stability_score"]
        assert 0.05 <= score <= 0.99


# ── Tier calculation: current_runtime_mode ──────────────────────────────────

class TestTierCalculation:
    """Тесты определения текущего runtime-режима."""

    def test_default_mode(self, monkeypatch):
        """Без env-переменных — default personal-runtime."""
        monkeypatch.delenv("KRAB_RUNTIME_MODE", raising=False)
        monkeypatch.delenv("OPENCLAW_RUNTIME_MODE", raising=False)
        assert current_runtime_mode() == "personal-runtime"

    def test_alias_personal(self, monkeypatch):
        """Алиас 'personal' → 'personal-runtime'."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "personal")
        assert current_runtime_mode() == "personal-runtime"

    def test_alias_release(self, monkeypatch):
        """Алиас 'release' → 'release-safe-runtime'."""
        monkeypatch.setenv("KRAB_RUNTIME_MODE", "release")
        assert current_runtime_mode() == "release-safe-runtime"

    def test_release_safe_flag(self, monkeypatch):
        """runtime_mode_release_safe должен вернуть True только для release-safe-runtime."""
        assert runtime_mode_release_safe("release-safe-runtime") is True
        assert runtime_mode_release_safe("personal-runtime") is False
        assert runtime_mode_release_safe("lab-runtime") is False
        assert runtime_mode_release_safe("") is False
