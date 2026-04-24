"""Тесты LLM-based query expansion (P2 carry-over).

Проверяем:
    1. disabled-flag → возвращается только original.
    2. Длинный запрос → expansion не вызывается (threshold min_tokens).
    3. LLM timeout → fallback на [original].
    4. Нормальный flow → original + rephrases.
    5. Malformed LLM response → fallback.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from src.core.memory_llm_query_expansion import (
    _count_tokens,
    _parse_rephrases,
    expand_query_llm,
    is_enabled,
    min_tokens,
)


class _FakeProvider:
    """Тестовый провайдер — подставляет заданный ответ или бросает."""

    def __init__(
        self,
        response: str | None = None,
        *,
        raise_exc: Exception | None = None,
        sleep_s: float = 0.0,
    ):
        self.response = response
        self.raise_exc = raise_exc
        self.sleep_s = sleep_s
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        if self.raise_exc:
            raise self.raise_exc
        return self.response or ""


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_disabled_by_default(monkeypatch):
    """Flag off по умолчанию → только original."""
    monkeypatch.delenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", raising=False)
    assert is_enabled() is False
    out = _run(expand_query_llm("короткий"))
    assert out == ["короткий"]


def test_enabled_flag(monkeypatch):
    """KRAB_RAG_QUERY_EXPANSION_ENABLED=1 → is_enabled True."""
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "1")
    assert is_enabled() is True
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "0")
    assert is_enabled() is False


def test_long_query_not_expanded(monkeypatch):
    """Длинный запрос (>= min_tokens) — expansion не триггерится."""
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS", "3")
    # 4 токена → >= 3 → no expansion.
    provider = _FakeProvider(response='["a","b","c"]')
    out = _run(expand_query_llm("один два три четыре", provider=provider))
    assert out == ["один два три четыре"]
    assert provider.calls == []  # LLM не вызывался


def test_short_query_expanded(monkeypatch):
    """Короткий запрос → LLM вызывается, получаем 3 перефразировки."""
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS", "3")
    provider = _FakeProvider(response='["вариант один", "вариант два", "вариант три"]')
    out = _run(expand_query_llm("краб", provider=provider))
    assert out[0] == "краб"  # original первый
    assert "вариант один" in out
    assert len(out) == 4  # original + 3 rephrases
    assert len(provider.calls) == 1


def test_timeout_fallback(monkeypatch):
    """LLM timeout → fallback на [original]."""
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_TIMEOUT", "0.05")
    provider = _FakeProvider(response='["a"]', sleep_s=0.5)
    out = _run(expand_query_llm("краб", provider=provider))
    assert out == ["краб"]


def test_llm_error_fallback(monkeypatch):
    """LLM бросает exception → fallback на [original]."""
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "1")
    provider = _FakeProvider(raise_exc=RuntimeError("api down"))
    out = _run(expand_query_llm("краб", provider=provider))
    assert out == ["краб"]


def test_malformed_json_fallback(monkeypatch):
    """LLM вернул мусор вместо JSON → fallback."""
    monkeypatch.setenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "1")
    provider = _FakeProvider(response="Привет! Вот варианты: первый, второй, третий.")
    out = _run(expand_query_llm("краб", provider=provider))
    # Fallback: только original.
    assert out == ["краб"]


def test_empty_query():
    """Пустой запрос → пустой список."""
    assert _run(expand_query_llm("")) == []
    assert _run(expand_query_llm("   ")) == []


def test_count_tokens():
    assert _count_tokens("один") == 1
    assert _count_tokens("один два три") == 3
    assert _count_tokens("  ") == 0
    assert _count_tokens("hello, world!") == 2


def test_parse_rephrases():
    """Парсер JSON-массива из LLM-ответа."""
    assert _parse_rephrases('["a", "b", "c"]') == ["a", "b", "c"]
    assert _parse_rephrases('prefix ["x","y"] suffix') == ["x", "y"]
    assert _parse_rephrases("not json") == []
    assert _parse_rephrases("") == []
    # Limit работает.
    assert _parse_rephrases('["a","b","c","d","e"]', limit=2) == ["a", "b"]


def test_min_tokens_default():
    os.environ.pop("KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS", None)
    assert min_tokens() == 3


@pytest.fixture(autouse=True)
def _reset_env():
    keys = [
        "KRAB_RAG_QUERY_EXPANSION_ENABLED",
        "KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS",
        "KRAB_RAG_QUERY_EXPANSION_TIMEOUT",
    ]
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
