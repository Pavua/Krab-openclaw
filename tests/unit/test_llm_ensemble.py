# -*- coding: utf-8 -*-
"""
Тесты LLMEnsemble (Idea 11).

LLM-вызовы мокаются через инжектируемый ``llm_callable`` — никаких сетей.
Покрытие: vote consensus, vote disagreement, concat, best_of, timeout fallback,
single-model degraded.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.llm_ensemble import (
    EnsembleResult,
    LLMEnsemble,
    is_enabled,
)


def _make_callable(responses: dict[str, str | Exception], delays: dict[str, float] | None = None):
    """Фабрика fake llm_callable.

    responses[model] — либо строка-ответ, либо исключение.
    delays[model]    — опциональная задержка в секундах перед ответом.
    """
    delays = delays or {}

    async def _call(model: str, prompt: str) -> str:
        if model in delays:
            await asyncio.sleep(delays[model])
        res = responses[model]
        if isinstance(res, Exception):
            raise res
        return res

    return _call


@pytest.mark.asyncio
async def test_vote_consensus_high_agreement():
    """Похожие ответы → high agreement_score, выбран мажоритарный."""
    fake = _make_callable(
        {
            "gpt-5": "Столица Франции — Париж.",
            "gemini-3-pro": "Столица Франции — Париж.",
        }
    )
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "Столица Франции?",
        models=["gpt-5", "gemini-3-pro"],
        strategy="vote",
    )
    assert isinstance(res, EnsembleResult)
    assert "Париж" in res.final_answer
    assert res.agreement_score == 1.0
    assert res.degraded is False
    assert len(res.individual_answers) == 2


@pytest.mark.asyncio
async def test_vote_disagreement_low_agreement():
    """Совершенно разные ответы → agreement_score=0.5 (по половине каждый)."""
    fake = _make_callable(
        {
            "gpt-5": "Это апельсин.",
            "gemini-3-pro": "Луна сегодня в фазе растущей четверти, видна на юго-западе.",
        }
    )
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "?",
        models=["gpt-5", "gemini-3-pro"],
        strategy="vote",
    )
    assert res.agreement_score == 0.5  # два разных кластера по одному
    assert res.final_answer  # один из ответов всё равно выбран
    assert res.degraded is False


@pytest.mark.asyncio
async def test_concat_combines_with_attribution():
    """concat склеивает оба ответа с заголовками."""
    fake = _make_callable(
        {
            "gpt-5": "Ответ A",
            "gemini-3-pro": "Ответ B",
        }
    )
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "вопрос",
        models=["gpt-5", "gemini-3-pro"],
        strategy="concat",
    )
    assert "### Ответ от gpt-5" in res.final_answer
    assert "### Ответ от gemini-3-pro" in res.final_answer
    assert "Ответ A" in res.final_answer
    assert "Ответ B" in res.final_answer
    assert res.degraded is False


@pytest.mark.asyncio
async def test_best_of_picks_highest_self_rate():
    """best_of выбирает ответ с максимальным RATING:N/10."""
    fake = _make_callable(
        {
            "gpt-5": "Это слабый ответ.\nRATING:4/10",
            "gemini-3-pro": "Это сильный детальный ответ.\nRATING:9/10",
        }
    )
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "вопрос",
        models=["gpt-5", "gemini-3-pro"],
        strategy="best_of",
    )
    assert "сильный детальный" in res.final_answer
    assert "RATING:" not in res.final_answer  # rate-строка вырезана
    assert res.agreement_score == 0.9


@pytest.mark.asyncio
async def test_timeout_fallback_to_surviving_model():
    """Если одна модель таймаутит — берём ответ выжившей, degraded=True."""
    fake = _make_callable(
        {
            "slow": "никогда не вернётся",
            "fast": "быстрый ответ",
        },
        delays={"slow": 5.0},
    )
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "вопрос",
        models=["slow", "fast"],
        strategy="vote",
        timeout_sec=0.2,
    )
    assert res.degraded is True
    assert res.final_answer == "быстрый ответ"
    assert "partial_failure" in res.notes
    assert len(res.individual_answers) == 1


@pytest.mark.asyncio
async def test_single_model_degraded_passthrough():
    """С одной моделью ансамбль вырождается в pass-through, degraded=True."""
    fake = _make_callable({"only": "одинокий ответ"})
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "вопрос",
        models=["only"],
        strategy="vote",
    )
    assert res.final_answer == "одинокий ответ"
    assert res.degraded is True
    assert res.notes == "single_model_degraded"
    assert res.agreement_score == 1.0


@pytest.mark.asyncio
async def test_all_models_failed_returns_empty_degraded():
    """Если все модели упали — пустой final_answer, degraded=True."""
    fake = _make_callable(
        {
            "a": RuntimeError("boom-a"),
            "b": ValueError("boom-b"),
        }
    )
    ens = LLMEnsemble(fake)
    res = await ens.ensemble_query(
        "вопрос",
        models=["a", "b"],
        strategy="vote",
    )
    assert res.final_answer == ""
    assert res.degraded is True
    assert "all_models_failed" in res.notes


def test_is_enabled_default_false(monkeypatch):
    monkeypatch.delenv("KRAB_LLM_ENSEMBLE_ENABLED", raising=False)
    assert is_enabled() is False


def test_is_enabled_truthy(monkeypatch):
    monkeypatch.setenv("KRAB_LLM_ENSEMBLE_ENABLED", "1")
    assert is_enabled() is True
    monkeypatch.setenv("KRAB_LLM_ENSEMBLE_ENABLED", "true")
    assert is_enabled() is True
    monkeypatch.setenv("KRAB_LLM_ENSEMBLE_ENABLED", "off")
    assert is_enabled() is False
