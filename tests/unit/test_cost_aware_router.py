# -*- coding: utf-8 -*-
"""Тесты CostAwareRouter — классификация и подбор модели по бюджету."""

from __future__ import annotations

import pytest

from src.core.cost_aware_router import CostAwareRouter, ModelTiers


@pytest.fixture
def router() -> CostAwareRouter:
    return CostAwareRouter()


@pytest.fixture
def models() -> list[str]:
    # Реалистичный набор available моделей
    return [
        "google/gemini-3-pro-preview",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-flash",
        "openai/gpt-5.5",
        "openai/gpt-5.5-pro",
        "anthropic/opus-4.7",
    ]


# ── Классификация ───────────────────────────────────────────────


def test_classify_trivial_single_word(router: CostAwareRouter) -> None:
    # Одиночное слово без greeting → trivial
    assert router.classify_task("krab") == "trivial"
    assert router.classify_task("foo") == "trivial"


def test_classify_simple_greeting(router: CostAwareRouter) -> None:
    assert router.classify_task("Привет!") == "simple"
    assert router.classify_task("hello there") == "simple"
    assert router.classify_task("спасибо") == "simple"


def test_classify_standard_short_text(router: CostAwareRouter) -> None:
    prompt = "Расскажи про погоду в Барселоне завтра, кратко."
    assert router.classify_task(prompt) == "standard"


def test_classify_code_by_fence(router: CostAwareRouter) -> None:
    prompt = "Что не так?\n```python\ndef f(x):\n    return x+1\n```"
    assert router.classify_task(prompt) == "code"


def test_classify_code_by_keyword(router: CostAwareRouter) -> None:
    assert router.classify_task("Напиши функцию для сортировки массива") == "code"
    assert router.classify_task("debug this stacktrace please, длинное описание тут") == "code"


def test_classify_reasoning(router: CostAwareRouter) -> None:
    assert router.classify_task("Объясни почему небо голубое и обоснуй ответ") == "reasoning"
    assert router.classify_task("Посчитай 25 * 17 + 9 пожалуйста") == "reasoning"


def test_classify_multimodal_overrides(router: CostAwareRouter) -> None:
    # Даже если текст похож на код, has_media побеждает
    assert router.classify_task("```py\nprint(1)\n```", has_media=True) == "multimodal"


# ── Recommend model ─────────────────────────────────────────────


def test_recommend_trivial_picks_cheap(router: CostAwareRouter, models: list[str]) -> None:
    picked = router.recommend_model("trivial", budget_remaining_usd=10.0, available_models=models)
    assert picked is not None and "flash" in picked.lower()


def test_recommend_code_picks_premium(router: CostAwareRouter, models: list[str]) -> None:
    picked = router.recommend_model("code", budget_remaining_usd=10.0, available_models=models)
    assert picked is not None
    assert "opus" in picked.lower() or "pro" in picked.lower()


def test_recommend_low_budget_downgrades_premium(
    router: CostAwareRouter, models: list[str]
) -> None:
    # При budget < 1.0 code/reasoning должны идти в standard, не premium
    picked = router.recommend_model("reasoning", budget_remaining_usd=0.5, available_models=models)
    assert picked is not None
    # standard tier — gpt-5.5/gemini-pro, не opus
    assert "opus" not in picked.lower()


def test_recommend_depleted_budget_forces_cheap(router: CostAwareRouter, models: list[str]) -> None:
    picked = router.recommend_model("code", budget_remaining_usd=0.0, available_models=models)
    assert picked is not None and "flash" in picked.lower()


def test_recommend_multimodal_picks_vision(router: CostAwareRouter, models: list[str]) -> None:
    picked = router.recommend_model("multimodal", budget_remaining_usd=5.0, available_models=models)
    assert picked is not None
    # vision tier начинается с gemini-3-pro
    assert "pro" in picked.lower() or "opus" in picked.lower()


def test_recommend_empty_models_returns_none(router: CostAwareRouter) -> None:
    assert router.recommend_model("standard", budget_remaining_usd=5.0, available_models=[]) is None


def test_recommend_fallback_when_no_tier_match(router: CostAwareRouter) -> None:
    # Только экзотическая модель — fallback на available[0]
    picked = router.recommend_model(
        "code", budget_remaining_usd=10.0, available_models=["custom/exotic-llm"]
    )
    assert picked == "custom/exotic-llm"


def test_custom_tiers_respected() -> None:
    tiers = ModelTiers(cheap=("tiny",), standard=("mid",), premium=("big",), vision=("eye",))
    r = CostAwareRouter(tiers=tiers)
    available = ["tiny-v1", "mid-v2", "big-v3", "eye-v1"]
    assert r.recommend_model("trivial", 10.0, available) == "tiny-v1"
    assert r.recommend_model("code", 10.0, available) == "big-v3"
    assert r.recommend_model("multimodal", 10.0, available) == "eye-v1"
