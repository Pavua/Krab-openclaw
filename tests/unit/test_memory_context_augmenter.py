"""
Тесты для memory_context_augmenter — semantic recall auto-context для !ask.

Покрывает:
  - default выключен (opt-in);
  - включённый режим prepend'ит top-k чанков;
  - фильтрация низких score;
  - graceful на исключениях hybrid_search;
  - override через force_enable (для флагов --with-memory / --no-memory);
  - пустой query / все score ниже порога → original query;
  - `_short_preview` обрезает длинный текст.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class _FakeResult:
    rrf_score: float
    text: str
    chunk_id: str
    sources: list[str]


@pytest.mark.asyncio
async def test_augment_disabled_by_default(monkeypatch):
    """Без MEMORY_AUTO_CONTEXT_ENABLED → original query."""
    monkeypatch.delenv("MEMORY_AUTO_CONTEXT_ENABLED", raising=False)
    from src.core import memory_context_augmenter as m

    ctx = await m.augment_query_with_memory("test")
    assert ctx.augmented_prompt == "test"
    assert ctx.enabled is False
    assert ctx.chunks_used == []


@pytest.mark.asyncio
async def test_augment_disabled_explicit_false(monkeypatch):
    """Явное MEMORY_AUTO_CONTEXT_ENABLED=false → disabled."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "false")
    from src.core import memory_context_augmenter as m

    ctx = await m.augment_query_with_memory("pytest")
    assert ctx.augmented_prompt == "pytest"
    assert ctx.enabled is False


@pytest.mark.asyncio
async def test_augment_enabled_prepends_context(monkeypatch):
    """При enabled=True и совпадениях → prefix формируется."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.8,
                text="past message about pytest best practices",
                chunk_id="c1",
                sources=["fts", "semantic"],
            ),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)

    ctx = await m.augment_query_with_memory("pytest best practices")
    assert "past message about pytest" in ctx.augmented_prompt
    assert "Вопрос: pytest best practices" in ctx.augmented_prompt
    assert "[fts+semantic]" in ctx.augmented_prompt
    assert len(ctx.chunks_used) == 1
    assert ctx.chunks_used[0]["chunk_id"] == "c1"
    assert ctx.enabled is True


@pytest.mark.asyncio
async def test_augment_filters_low_score(monkeypatch):
    """Score ниже min_score → пропускается."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_MIN_SCORE", "0.5")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(rrf_score=0.1, text="weak match", chunk_id="c1", sources=["fts"]),
            _FakeResult(rrf_score=0.2, text="also weak", chunk_id="c2", sources=["fts"]),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)

    ctx = await m.augment_query_with_memory("query")
    # Все chunks ниже порога → augmented == query (пустой chunks_used)
    assert ctx.augmented_prompt == "query"
    assert ctx.chunks_used == []
    assert ctx.enabled is True  # попытка была


@pytest.mark.asyncio
async def test_augment_handles_search_failure(monkeypatch):
    """hybrid_search бросает exception → original query, enabled=True."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _boom(q: str, limit: int = 3):
        raise RuntimeError("db down")

    monkeypatch.setattr(m, "hybrid_search", _boom)

    ctx = await m.augment_query_with_memory("query")
    assert ctx.augmented_prompt == "query"
    assert ctx.enabled is True
    assert ctx.chunks_used == []


@pytest.mark.asyncio
async def test_augment_empty_query_returns_original(monkeypatch):
    """Пустой query → original без augmentation."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ctx = await m.augment_query_with_memory("   ")
    assert ctx.augmented_prompt == "   "
    assert ctx.enabled is False


@pytest.mark.asyncio
async def test_force_enable_overrides_env(monkeypatch):
    """force_enable=True работает даже при env=false."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "false")
    from src.core import memory_context_augmenter as m

    captured = {}

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        captured["called"] = True
        return [
            _FakeResult(rrf_score=0.9, text="fact", chunk_id="c1", sources=["semantic"]),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)

    ctx = await m.augment_query_with_memory("q", force_enable=True)
    assert captured.get("called") is True
    assert ctx.enabled is True
    assert "fact" in ctx.augmented_prompt


@pytest.mark.asyncio
async def test_force_disable_overrides_env(monkeypatch):
    """force_enable=False работает даже при env=true."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3):
        raise AssertionError("should not be called")

    monkeypatch.setattr(m, "hybrid_search", _fake_search)

    ctx = await m.augment_query_with_memory("q", force_enable=False)
    assert ctx.enabled is False
    assert ctx.augmented_prompt == "q"


@pytest.mark.asyncio
async def test_augment_multiple_chunks_numbered(monkeypatch):
    """Несколько chunks → нумерация 1/2/3 в prefix."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(rrf_score=0.9, text="first", chunk_id="c1", sources=["fts"]),
            _FakeResult(rrf_score=0.7, text="second", chunk_id="c2", sources=["semantic"]),
            _FakeResult(rrf_score=0.5, text="third", chunk_id="c3", sources=["fts"]),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)

    ctx = await m.augment_query_with_memory("q")
    assert "1. [fts]" in ctx.augmented_prompt
    assert "2. [semantic]" in ctx.augmented_prompt
    assert "3. [fts]" in ctx.augmented_prompt
    assert len(ctx.chunks_used) == 3


def test_short_preview_truncates():
    """_short_preview режет длинный текст и убирает \\n."""
    from src.core.memory_context_augmenter import _short_preview

    long = "a" * 500
    result = _short_preview(long, max_len=100)
    assert len(result) == 103  # 100 + "..."
    assert result.endswith("...")

    with_newlines = "line1\nline2\nline3"
    assert _short_preview(with_newlines) == "line1 line2 line3"

    assert _short_preview("") == ""
    assert _short_preview(None) == ""  # type: ignore[arg-type]


def test_adapt_retrieval_result_search_result_shape():
    """Адаптация SearchResult из memory_retrieval (score/text_redacted/message_id)."""
    from src.core.memory_context_augmenter import _adapt_retrieval_result

    class _Stub:
        score = 0.42
        text_redacted = "redacted text"
        message_id = "m1"

    adapted = _adapt_retrieval_result(_Stub())
    assert adapted is not None
    assert adapted.rrf_score == 0.42
    assert adapted.text == "redacted text"
    assert adapted.chunk_id == "m1"
    assert adapted.sources == ["hybrid"]  # default fallback


def test_adapt_retrieval_result_none():
    """_adapt_retrieval_result(None) → None."""
    from src.core.memory_context_augmenter import _adapt_retrieval_result

    assert _adapt_retrieval_result(None) is None


def test_parse_ask_memory_flags():
    """Парсер флагов --with-memory / --no-memory."""
    from src.handlers.command_handlers import _parse_ask_memory_flags

    q, force = _parse_ask_memory_flags("--with-memory что это")
    assert q == "что это"
    assert force is True

    q, force = _parse_ask_memory_flags("объясни --no-memory быстро")
    assert q == "объясни быстро"
    assert force is False

    q, force = _parse_ask_memory_flags("без флагов")
    assert q == "без флагов"
    assert force is None

    q, force = _parse_ask_memory_flags("")
    assert q == ""
    assert force is None
