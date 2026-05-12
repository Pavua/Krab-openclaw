# -*- coding: utf-8 -*-
"""
Wave 128 tests: LLM context budget monitor.

Покрывает:
- pct compute для известных моделей (Gemini 1M, Claude 200K, GPT 128K)
- unknown model → 0.0 (gauge не обновляется)
- prompt_tokens <= 0 → 0.0
- non-numeric prompt_tokens → 0.0 (fail-safe)
- record_context_usage устанавливает gauge для known model
- record_context_usage no-op для unknown / zero tokens
- get_context_window для unknown model → 0
"""

from __future__ import annotations

import pytest

from src.core.metrics.context_budget import (
    MODEL_CONTEXT_WINDOW,
    compute_context_usage_pct,
    get_context_window,
    krab_llm_context_usage_pct,
    record_context_usage,
)


def test_compute_pct_gemini_3_pro_half_window() -> None:
    # 524288 / 1048576 = 0.5
    pct = compute_context_usage_pct("google/gemini-3-pro-preview", 524_288)
    assert pct == pytest.approx(0.5, rel=1e-6)


def test_compute_pct_claude_sonnet_near_cap() -> None:
    # 180000 / 200000 = 0.9 (выше 0.8 порога alert)
    pct = compute_context_usage_pct("anthropic/claude-sonnet-4-5", 180_000)
    assert pct == pytest.approx(0.9, rel=1e-6)
    assert pct > 0.8  # alert трипанулся бы


def test_compute_pct_gpt5_under_cap() -> None:
    # 64000 / 128000 = 0.5
    pct = compute_context_usage_pct("openai/gpt-5.5", 64_000)
    assert pct == pytest.approx(0.5, rel=1e-6)


def test_unknown_model_returns_zero() -> None:
    assert compute_context_usage_pct("some/unknown-model", 100_000) == 0.0
    assert get_context_window("some/unknown-model") == 0


def test_zero_or_negative_prompt_tokens() -> None:
    assert compute_context_usage_pct("google/gemini-3-pro-preview", 0) == 0.0
    assert compute_context_usage_pct("google/gemini-3-pro-preview", -50) == 0.0


def test_non_numeric_prompt_tokens_failsafe() -> None:
    # str numeric → int() succeeds
    assert compute_context_usage_pct(
        "google/gemini-3-pro-preview", "1024"  # type: ignore[arg-type]
    ) == pytest.approx(1024 / 1_048_576)
    # garbage → 0.0
    assert compute_context_usage_pct(
        "google/gemini-3-pro-preview", "not-a-number"  # type: ignore[arg-type]
    ) == 0.0
    assert compute_context_usage_pct(
        "google/gemini-3-pro-preview", None  # type: ignore[arg-type]
    ) == 0.0


def test_record_context_usage_sets_gauge_for_known_model() -> None:
    """record_context_usage обновляет gauge для известной модели."""
    record_context_usage("google/gemini-3-pro-preview", 838_861)  # 0.8

    # Достать значение gauge через _value (prometheus_client API).
    # Если no-op stub (нет prom_client) — пропускаем.
    sample = getattr(krab_llm_context_usage_pct, "_value", None)
    if sample is None:
        # No-op env — гарантируем что вызов не упал, и всё.
        return
    # Pythonic API: collect samples
    metric = krab_llm_context_usage_pct.collect()[0]
    seen = {
        s.labels.get("model"): s.value
        for s in metric.samples
        if s.name == "krab_llm_context_usage_pct"
    }
    assert "google/gemini-3-pro-preview" in seen
    assert seen["google/gemini-3-pro-preview"] == pytest.approx(
        838_861 / 1_048_576, rel=1e-5
    )


def test_record_context_usage_noop_for_unknown_or_zero() -> None:
    """Unknown model / нулевые токены / пустой model_id — без побочных эффектов."""
    # Просто проверяем что не падает (gauge state мутируется в test выше).
    record_context_usage("", 100_000)
    record_context_usage("totally/unknown", 100_000)
    record_context_usage("google/gemini-3-pro-preview", 0)
    record_context_usage("google/gemini-3-pro-preview", -1)


def test_model_table_covers_primary_routing() -> None:
    """Текущий primary routing (см. CLAUDE.md) должен быть в таблице."""
    primaries = [
        "google/gemini-3-pro-preview",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-pro-preview",
        "google/gemini-2.5-flash",
        "anthropic/claude-sonnet-4-5",
    ]
    for model_id in primaries:
        assert model_id in MODEL_CONTEXT_WINDOW, f"Missing context window: {model_id}"
        assert MODEL_CONTEXT_WINDOW[model_id] > 0
