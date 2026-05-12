"""Wave 78: token-cost FinOps tracking — Prometheus + cost_analytics integration.

Тесты для:
- pricing table lookup (exact / suffix / unknown).
- расчёт стоимости в EUR (prompt + completion + thoughts).
- record_completion_cost helper (counters + histogram + return value).
- defaults: thoughts_tokens=0, unknown model = 0.0 cost.
- provider inference из имени модели.
"""

from __future__ import annotations

import pytest

from src.core.prometheus_metrics import (
    _calculate_cost_eur,
    _infer_provider_from_model,
    _resolve_pricing,
    record_completion_cost,
)


class TestPricingLookup:
    """Pricing table — точные и suffix-совпадения."""

    def test_exact_gemini_pro(self) -> None:
        price_in, price_out = _resolve_pricing("gemini-2.5-pro")
        assert price_in == 1.25
        assert price_out == 10.0

    def test_provider_prefix_stripped(self) -> None:
        # google/gemini-2.5-pro-preview → совпадает с gemini-2.5-pro-preview
        price_in, price_out = _resolve_pricing("google/gemini-2.5-pro-preview")
        assert price_in == 1.25
        assert price_out == 10.0

    def test_claude_sonnet(self) -> None:
        price_in, price_out = _resolve_pricing("claude-sonnet-4-5")
        assert price_in == 3.0
        assert price_out == 15.0

    def test_unknown_model_returns_zero(self) -> None:
        assert _resolve_pricing("unknown-model-xyz-7000") == (0.0, 0.0)

    def test_empty_model_returns_zero(self) -> None:
        assert _resolve_pricing("") == (0.0, 0.0)


class TestCostCalculation:
    """Расчёт стоимости в EUR с конвертацией из USD."""

    def test_zero_tokens_zero_cost(self) -> None:
        assert _calculate_cost_eur("gemini-2.5-pro", 0, 0, 0) == 0.0

    def test_unknown_model_zero_cost(self) -> None:
        assert _calculate_cost_eur("foo-bar", 1_000_000, 1_000_000) == 0.0

    def test_gemini_pro_1m_prompt_only(self) -> None:
        # 1M prompt tokens × $1.25 × 0.92 EUR/USD = $1.15
        cost = _calculate_cost_eur("gemini-2.5-pro", 1_000_000, 0, 0)
        assert cost == pytest.approx(1.15, abs=0.01)

    def test_thoughts_billed_as_completion(self) -> None:
        # Без thoughts.
        base = _calculate_cost_eur("gemini-2.5-pro", 0, 0, 0)
        # 1M thoughts токенов = 1M completion ($10/M).
        with_thoughts = _calculate_cost_eur("gemini-2.5-pro", 0, 0, 1_000_000)
        assert with_thoughts > base
        # Должно быть равно стоимости 1M completion токенов.
        completion_only = _calculate_cost_eur("gemini-2.5-pro", 0, 1_000_000, 0)
        assert with_thoughts == pytest.approx(completion_only, abs=0.001)


class TestRecordCompletionCost:
    """Helper для записи метрик. Best-effort: не падает при отсутствии prometheus_client."""

    def test_default_thoughts_zero(self) -> None:
        # Не передаём thoughts_tokens — default 0, не должно бросать.
        cost = record_completion_cost(
            provider="google",
            model="gemini-2.5-pro",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert cost >= 0.0

    def test_unknown_model_returns_zero(self) -> None:
        cost = record_completion_cost(
            provider="unknown",
            model="totally-bogus-model-name-9999",
            prompt_tokens=10_000,
            completion_tokens=10_000,
        )
        assert cost == 0.0

    def test_explicit_cost_override(self) -> None:
        # Если cost_eur передан явно — используется он, не из таблицы.
        cost = record_completion_cost(
            provider="google",
            model="gemini-2.5-pro",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_eur=42.0,
        )
        assert cost == 42.0

    def test_negative_tokens_clamped_to_zero(self) -> None:
        # Защита от мусорных данных.
        cost = record_completion_cost(
            provider="google",
            model="gemini-2.5-pro",
            prompt_tokens=-100,
            completion_tokens=-50,
            thoughts_tokens=-1,
        )
        assert cost == 0.0


class TestProviderInference:
    """Best-effort провайдер по имени модели."""

    def test_provider_prefix(self) -> None:
        assert _infer_provider_from_model("google/gemini-2.5-pro") == "google"
        assert _infer_provider_from_model("anthropic/claude-sonnet-4-5") == "anthropic"

    def test_gemini_bare(self) -> None:
        assert _infer_provider_from_model("gemini-2.5-pro") == "google"

    def test_claude_bare(self) -> None:
        assert _infer_provider_from_model("claude-opus-4") == "anthropic"

    def test_gpt_bare(self) -> None:
        assert _infer_provider_from_model("gpt-5.5") == "openai"

    def test_local(self) -> None:
        assert _infer_provider_from_model("local-mlx-mistral") == "local"

    def test_empty(self) -> None:
        assert _infer_provider_from_model("") == "unknown"
