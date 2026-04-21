"""Тесты LLM re-ranking (Chado §6 P1)."""

from __future__ import annotations

import asyncio
import os

import pytest

from src.core.memory_llm_rerank import Candidate, is_enabled, llm_rerank

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _mk(cid: str, rrf: float, text: str = "some text") -> Candidate:
    return Candidate(chunk_id=cid, text=text, rrf_score=rrf)


class MockProvider:
    """Мок-провайдер: возвращает предзаданный JSON-ответ."""

    def __init__(self, response: str) -> None:
        self._response = response

    async def generate(self, prompt: str) -> str:
        return self._response


class SlowProvider:
    """Провайдер с задержкой — для теста timeout."""

    def __init__(self, delay: float = 10.0) -> None:
        self._delay = delay

    async def generate(self, prompt: str) -> str:
        await asyncio.sleep(self._delay)
        return "[10]"


class RaisingProvider:
    """Провайдер, который бросает исключение."""

    async def generate(self, prompt: str) -> str:
        raise RuntimeError("LLM unavailable")


# ---------------------------------------------------------------------------
# is_enabled().
# ---------------------------------------------------------------------------


def test_is_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("KRAB_RAG_LLM_RERANK_ENABLED", raising=False)
    assert is_enabled() is False


def test_is_enabled_on(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    assert is_enabled() is True


def test_is_enabled_wrong_value(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "true")
    assert is_enabled() is False


# ---------------------------------------------------------------------------
# Disabled path (provider=None).
# ---------------------------------------------------------------------------


def test_no_provider_returns_unchanged_top_k(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    candidates = [_mk(str(i), float(10 - i)) for i in range(20)]
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("query", candidates, top_k=5, provider=None)
    )
    assert len(result) == 5
    # Порядок не меняется.
    assert [c.chunk_id for c in result] == ["0", "1", "2", "3", "4"]


def test_env_disabled_no_provider_returns_top_k(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "0")
    candidates = [_mk(str(i), float(5 - i)) for i in range(5)]
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=3, provider=None)
    )
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Mock provider — проверяем сортировку по llm_score.
# ---------------------------------------------------------------------------


def test_mock_provider_sorts_by_llm_score(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    # Три кандидата по убыванию rrf_score: a(0.9) > b(0.8) > c(0.7).
    # LLM-оценки: a=3, b=9, c=6 → после реранка: b > c > a.
    candidates = [
        _mk("a", 0.9),
        _mk("b", 0.8),
        _mk("c", 0.7),
    ]
    provider = MockProvider("[3, 9, 6]")
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=3, provider=provider)
    )
    assert len(result) == 3
    assert result[0].chunk_id == "b"
    assert result[1].chunk_id == "c"
    assert result[2].chunk_id == "a"


def test_mock_provider_llm_scores_populated(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    candidates = [_mk("x", 0.5), _mk("y", 0.4)]
    provider = MockProvider("[8, 2]")
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=2, provider=provider)
    )
    assert result[0].llm_score == pytest.approx(0.8)
    assert result[1].llm_score == pytest.approx(0.2)


def test_mock_provider_top_k_limits_output(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    candidates = [_mk(str(i), float(10 - i)) for i in range(10)]
    provider = MockProvider("[1,2,3,4,5,6,7,8,9,10]")
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=5, provider=provider)
    )
    assert len(result) == 5


# ---------------------------------------------------------------------------
# Timeout → fallback to original order.
# ---------------------------------------------------------------------------


def test_timeout_returns_original_top_k(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    candidates = [_mk(str(i), float(5 - i)) for i in range(5)]
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=3, provider=SlowProvider(10.0), timeout_sec=0.01)
    )
    # Fallback: top-3 в исходном порядке.
    assert len(result) == 3
    assert [c.chunk_id for c in result] == ["0", "1", "2"]
    # llm_score не выставлен при timeout.
    assert all(c.llm_score is None for c in result)


# ---------------------------------------------------------------------------
# Empty candidates.
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_empty(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", [], top_k=5, provider=MockProvider("[1,2,3]"))
    )
    assert result == []


# ---------------------------------------------------------------------------
# Provider raises → fallback, no crash.
# ---------------------------------------------------------------------------


def test_provider_raises_returns_original(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "1")
    candidates = [_mk(str(i), float(5 - i)) for i in range(4)]
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=3, provider=RaisingProvider())
    )
    # Нет краша, возвращает исходные top-3.
    assert len(result) == 3
    assert [c.chunk_id for c in result] == ["0", "1", "2"]


# ---------------------------------------------------------------------------
# Disabled env + real provider → still skips LLM.
# ---------------------------------------------------------------------------


def test_disabled_env_skips_provider(monkeypatch):
    monkeypatch.setenv("KRAB_RAG_LLM_RERANK_ENABLED", "0")
    candidates = [_mk("a", 0.9), _mk("b", 0.8), _mk("c", 0.7)]
    # Провайдер с обратными оценками: если бы сработал, переставил бы порядок.
    provider = MockProvider("[1, 5, 9]")
    result = asyncio.get_event_loop().run_until_complete(
        llm_rerank("q", candidates, top_k=3, provider=provider)
    )
    # Порядок должен остаться исходным (LLM не вызывался).
    assert [c.chunk_id for c in result] == ["a", "b", "c"]
    assert all(c.llm_score is None for c in result)
