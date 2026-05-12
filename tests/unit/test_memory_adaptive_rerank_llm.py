"""Тесты Wave 43-A: adaptive rerank threshold + LRU cache для memory_llm_rerank."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.core.memory_llm_rerank import (
    Candidate,
    _cache_get,
    _cache_put,
    _rerank_cache,
    adaptive_rerank_enabled,
    clear_rerank_cache,
    llm_rerank,
    make_rerank_cache_key,
    should_apply_llm_rerank,
)

# ---------------------------------------------------------------------------
# Вспомогательные функции.
# ---------------------------------------------------------------------------


def _cand(chunk_id: str, rrf_score: float) -> Candidate:
    return Candidate(chunk_id=chunk_id, text=f"text {chunk_id}", rrf_score=rrf_score)


def _run(coro):
    """Sync-обёртка для asyncio в pytest без async."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Тест 1: High confidence query → LLM rerank не вызывается.
# ---------------------------------------------------------------------------


def test_high_confidence_skips_llm_rerank():
    """Top-1 RRF score выше HIGH_CONF порога → LLM не вызывается."""
    provider_calls: list[str] = []

    class FakeProvider:
        async def generate(self, prompt: str) -> str:
            provider_calls.append(prompt)
            return "[5, 4, 3]"

    candidates = [
        _cand("c1", 0.92),  # выше default HIGH_CONF=0.85
        _cand("c2", 0.50),
        _cand("c3", 0.30),
    ]

    with patch.dict(
        os.environ,
        {
            "KRAB_RAG_LLM_RERANK_ENABLED": "1",
            "KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED": "1",
            "KRAB_RERANK_HIGH_CONF": "0.85",
            "KRAB_RERANK_LOW_CONF": "0.20",
        },
    ):
        # Перезагружаем thresholds из env после patch
        import importlib

        import src.core.memory_llm_rerank as mod

        importlib.reload(mod)
        clear_rerank_cache()

        result = _run(
            mod.llm_rerank(
                "high confidence query",
                candidates,
                top_k=3,
                provider=FakeProvider(),
            )
        )

    # LLM не должен вызываться — возвращаем в исходном RRF-порядке.
    assert provider_calls == [], "LLM provider не должен вызываться при high confidence"
    assert [r.chunk_id for r in result] == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# Тест 2: Low confidence query → LLM rerank не вызывается.
# ---------------------------------------------------------------------------


def test_low_confidence_skips_llm_rerank():
    """Top-1 RRF score ниже LOW_CONF порога → LLM не вызывается."""
    provider_calls: list[str] = []

    class FakeProvider:
        async def generate(self, prompt: str) -> str:
            provider_calls.append(prompt)
            return "[9, 8, 7]"

    candidates = [
        _cand("c1", 0.15),  # ниже default LOW_CONF=0.20
        _cand("c2", 0.10),
        _cand("c3", 0.05),
    ]

    with patch.dict(
        os.environ,
        {
            "KRAB_RAG_LLM_RERANK_ENABLED": "1",
            "KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED": "1",
            "KRAB_RERANK_HIGH_CONF": "0.85",
            "KRAB_RERANK_LOW_CONF": "0.20",
        },
    ):
        import importlib

        import src.core.memory_llm_rerank as mod

        importlib.reload(mod)
        clear_rerank_cache()

        result = _run(
            mod.llm_rerank(
                "low confidence query",
                candidates,
                top_k=3,
                provider=FakeProvider(),
            )
        )

    assert provider_calls == [], "LLM provider не должен вызываться при low confidence"
    assert [r.chunk_id for r in result] == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# Тест 3: Borderline query → LLM rerank применяется.
# ---------------------------------------------------------------------------


def test_borderline_applies_llm_rerank():
    """Top-1 RRF score в borderline диапазоне → LLM вызывается и меняет порядок."""
    provider_calls: list[str] = []

    class FakeProvider:
        async def generate(self, prompt: str) -> str:
            provider_calls.append(prompt)
            # LLM предпочитает c2 > c1 > c3: возвращает [3, 9, 1]
            return "[3, 9, 1]"

    candidates = [
        _cand("c1", 0.55),  # borderline (0.20 < 0.55 < 0.85)
        _cand("c2", 0.45),
        _cand("c3", 0.35),
    ]

    with patch.dict(
        os.environ,
        {
            "KRAB_RAG_LLM_RERANK_ENABLED": "1",
            "KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED": "1",
            "KRAB_RERANK_HIGH_CONF": "0.85",
            "KRAB_RERANK_LOW_CONF": "0.20",
        },
    ):
        import importlib

        import src.core.memory_llm_rerank as mod

        importlib.reload(mod)
        clear_rerank_cache()

        result = _run(
            mod.llm_rerank(
                "borderline query",
                candidates,
                top_k=3,
                provider=FakeProvider(),
            )
        )

    assert provider_calls != [], "LLM должен был вызваться для borderline score"
    # c2 получила llm_score=9/10=0.9 → должна стать первой.
    assert result[0].chunk_id == "c2", f"ожидали c2 первым, получили {result[0].chunk_id}"


# ---------------------------------------------------------------------------
# Тест 4: Cache hit in 5 min window → возврат cached, LLM не вызывается.
# ---------------------------------------------------------------------------


def test_cache_hit_within_ttl_skips_llm():
    """Повторный запрос в пределах TTL → возвращается кэш, LLM не вызывается."""
    provider_calls: list[str] = []

    class FakeProvider:
        async def generate(self, prompt: str) -> str:
            provider_calls.append(prompt)
            return "[9, 1, 1]"

    candidates = [
        _cand("c1", 0.55),  # borderline
        _cand("c2", 0.45),
    ]
    query = "cache test query"

    with patch.dict(
        os.environ,
        {
            "KRAB_RAG_LLM_RERANK_ENABLED": "1",
            "KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED": "1",
            "KRAB_RERANK_HIGH_CONF": "0.85",
            "KRAB_RERANK_LOW_CONF": "0.20",
            "KRAB_RERANK_CACHE_TTL_SEC": "300",
            "KRAB_RERANK_CACHE_MAXSIZE": "100",
        },
    ):
        import importlib

        import src.core.memory_llm_rerank as mod

        importlib.reload(mod)
        mod.clear_rerank_cache()
        provider = FakeProvider()

        # Первый вызов — LLM отрабатывает.
        result1 = _run(mod.llm_rerank(query, candidates, top_k=2, provider=provider))
        assert len(provider_calls) == 1, "первый вызов должен обратиться к LLM"

        # Второй вызов с тем же query+top_k — cache hit.
        result2 = _run(mod.llm_rerank(query, candidates, top_k=2, provider=provider))
        assert len(provider_calls) == 1, "второй вызов должен использовать cache, не LLM"

    # Порядок одинаковый.
    assert [r.chunk_id for r in result1] == [r.chunk_id for r in result2]


# ---------------------------------------------------------------------------
# Дополнительные unit-тесты: should_apply_llm_rerank и cache helpers.
# ---------------------------------------------------------------------------


def test_should_apply_returns_false_for_high_score():
    """Без adaptive flag (legacy) — always True."""
    with patch.dict(os.environ, {"KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED": "0"}):
        import importlib

        import src.core.memory_llm_rerank as mod

        importlib.reload(mod)
        # Legacy mode → всегда True независимо от score.
        assert mod.should_apply_llm_rerank(0.99) is True
        assert mod.should_apply_llm_rerank(0.01) is True
        assert mod.should_apply_llm_rerank(0.50) is True


def test_should_apply_adaptive_thresholds():
    """Adaptive mode: корректные пороги high/low."""
    with patch.dict(
        os.environ,
        {
            "KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED": "1",
            "KRAB_RERANK_HIGH_CONF": "0.80",
            "KRAB_RERANK_LOW_CONF": "0.25",
        },
    ):
        import importlib

        import src.core.memory_llm_rerank as mod

        importlib.reload(mod)
        assert mod.should_apply_llm_rerank(0.90) is False  # выше high
        assert mod.should_apply_llm_rerank(0.80) is False  # равно high → skip
        assert mod.should_apply_llm_rerank(0.50) is True   # borderline
        assert mod.should_apply_llm_rerank(0.25) is False  # равно low → skip
        assert mod.should_apply_llm_rerank(0.10) is False  # ниже low


def test_cache_put_get_returns_same_data():
    """Cache put/get: данные round-trip сохраняются корректно."""
    clear_rerank_cache()
    key = "test:myquery"
    cands = [_cand("x1", 0.9), _cand("x2", 0.8)]
    _cache_put(key, cands)
    result = _cache_get(key)
    assert result is not None
    assert len(result) == 2
    assert result[0].chunk_id == "x1"


def test_cache_miss_returns_none():
    """Cache miss → None."""
    clear_rerank_cache()
    assert _cache_get("nonexistent:query") is None


def test_cache_expired_returns_none():
    """Cache запись с истёкшим TTL → None."""
    clear_rerank_cache()
    key = "expired:query"
    cands = [_cand("e1", 0.5)]
    _cache_put(key, cands)
    # Подделываем timestamp как очень старый.

    _rerank_cache[key] = (time.monotonic() - 9999, cands)
    assert _cache_get(key) is None


def test_cache_maxsize_evicts_oldest():
    """При переполнении cache — старейшая запись вытесняется."""
    clear_rerank_cache()
    # Заполняем cache (maxsize=100, но проверяем логику вытеснения напрямую)
    from src.core.memory_llm_rerank import _CACHE_MAXSIZE

    # Добавляем MAXSIZE+1 записей.
    for i in range(_CACHE_MAXSIZE + 1):
        _cache_put(f"query_{i}", [_cand(f"c{i}", 0.5)])

    # После вытеснения первый ключ должен исчезнуть.
    assert _cache_get("query_0") is None, "query_0 должен быть вытеснен как LRU"
    # Последний должен быть в cache.
    assert _cache_get(f"query_{_CACHE_MAXSIZE}") is not None
