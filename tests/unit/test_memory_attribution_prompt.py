"""
Тесты усиленной LLM-инструкции атрибуции в memory_context_augmenter.

Проверяет что:
- Промпт содержит критическую инструкцию про chat_title vs имена в тексте
- Пример с Анной/Дашей присутствует для one-shot обучения LLM
- Chunks с именем X в chat_title vs X только в тексте корректно атрибутируются
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest


@dataclass
class _FakeResult:
    """Имитирует SearchResult из memory_retrieval."""

    rrf_score: float
    text: str
    chunk_id: str
    sources: list[str]
    chat_id: str = ""
    timestamp: Any = None


# ---------------------------------------------------------------------------
# Проверка содержимого критической инструкции в prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_contains_critical_attribution_rule(monkeypatch):
    """Augmented prompt содержит критическое правило про chat_title vs имена в тексте."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.9,
                text="текст сообщения",
                chunk_id="c1",
                sources=["fts"],
                chat_id="-100111",
                timestamp=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-100111": "Тест"})

    ctx = await m.augment_query_with_memory("что я писал?")
    prompt = ctx.augmented_prompt

    # Критическое правило присутствует
    assert "КРИТИЧНО про [MEMORY] блоки" in prompt
    # Правило про chat_title
    assert "ПОЛНЫЙ собеседник-контекст" in prompt
    # Правило против вывода по тексту
    assert "НЕ делай выводы" in prompt
    # Правило про фильтрацию по chat_title при запросе "с X"
    assert "chat_title содержит X" in prompt


@pytest.mark.asyncio
async def test_prompt_contains_anna_dasha_example(monkeypatch):
    """Prompt содержит конкретный пример Анна/Даша для one-shot LLM обучения."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(rrf_score=0.8, text="текст", chunk_id="c1", sources=["fts"], chat_id="-1")
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-1": "Чат"})

    ctx = await m.augment_query_with_memory("тест")
    prompt = ctx.augmented_prompt

    # One-shot пример с Анной и Дашей
    assert "Анна" in prompt
    assert "Даша" in prompt
    assert "НЕПРАВИЛЬНО" in prompt
    assert "ПРАВИЛЬНО" in prompt


@pytest.mark.asyncio
async def test_chunk_from_anna_chat_mentions_dasha(monkeypatch):
    """
    Fixture: chunk из чата с Анной содержит упоминание Даши.
    Prompt должен явно показывать chat_title='Анна', а не путать с Дашей.
    """
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ts = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.95,
                text="Анна, у меня Даша просит испанский номер для AppStore, как лучше?",
                chunk_id="c1",
                sources=["fts"],
                chat_id="-100111",
                timestamp=ts,
            )
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-100111": "Анна 🌸"})

    ctx = await m.augment_query_with_memory("что я писал про AppStore?")
    prompt = ctx.augmented_prompt

    # chat_title явно в блоке
    assert 'в чате "Анна 🌸"' in prompt
    # Текст с Дашей присутствует
    assert "Даша просит испанский номер" in prompt
    # chunk_meta показывает правильный chat_title
    assert ctx.chunks_used[0]["chat_title"] == "Анна 🌸"


@pytest.mark.asyncio
async def test_chunk_from_dasha_chat_prioritized_by_title(monkeypatch):
    """
    Два chunks: один из чата с Дашей, один из чата с Аней (где упоминается Даша).
    При запросе 'что я писал с Дашкой' — оба попадают в prompt с явными chat_title.
    """
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ts = datetime(2026, 4, 1, tzinfo=timezone.utc)

    def _fake_search(q: str, limit: int = 5) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.95,
                text="Дашка, привет! Как дела с телефоном?",
                chunk_id="c_dasha",
                sources=["fts"],
                chat_id="-100dasha",
                timestamp=ts,
            ),
            _FakeResult(
                rrf_score=0.7,
                text="Аня, Дашка просила передать привет",
                chunk_id="c_anya",
                sources=["fts"],
                chat_id="-100anya",
                timestamp=ts,
            ),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(
        m,
        "_resolve_chat_titles",
        lambda ids: {"-100dasha": "Дашка", "-100anya": "Аня"},
    )

    ctx = await m.augment_query_with_memory("о чём я писал с Дашкой?")
    prompt = ctx.augmented_prompt

    # Оба чата явно помечены — LLM видит разницу
    assert 'в чате "Дашка"' in prompt
    assert 'в чате "Аня"' in prompt

    # chunk_meta правильно атрибутированы
    titles = {c["chat_title"] for c in ctx.chunks_used}
    assert "Дашка" in titles
    assert "Аня" in titles


@pytest.mark.asyncio
async def test_attribution_instruction_precedes_memory_blocks(monkeypatch):
    """Инструкция атрибуции идёт ПЕРЕД [MEMORY] блоками, не после."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(rrf_score=0.8, text="chunk text", chunk_id="c1", sources=["fts"], chat_id="-1")
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-1": "Чат"})

    ctx = await m.augment_query_with_memory("тест")
    prompt = ctx.augmented_prompt

    # Индекс инструкции < индекса первого [MEMORY] блока
    instruction_pos = prompt.index("КРИТИЧНО")
    memory_pos = prompt.index("[MEMORY]")
    assert instruction_pos < memory_pos, "Инструкция должна идти перед [MEMORY] блоками"


@pytest.mark.asyncio
async def test_query_appended_after_memory_blocks(monkeypatch):
    """Оригинальный запрос добавляется после блоков памяти."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    original_query = "о чём я писал с Дашкой про Apple ID?"

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(rrf_score=0.8, text="chunk", chunk_id="c1", sources=["fts"], chat_id="-1")
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-1": "Чат"})

    ctx = await m.augment_query_with_memory(original_query)
    prompt = ctx.augmented_prompt

    # Запрос есть в конце
    assert original_query in prompt
    # Запрос идёт после [MEMORY] блока
    assert prompt.index("[MEMORY]") < prompt.index(original_query)


@pytest.mark.asyncio
async def test_instruction_present_with_multiple_chunks(monkeypatch):
    """Инструкция присутствует независимо от количества chunks (3 chunks)."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ts = datetime(2026, 4, 22, tzinfo=timezone.utc)

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(rrf_score=0.9, text=f"текст {i}", chunk_id=f"c{i}", sources=["fts"], chat_id=f"-10{i}", timestamp=ts)
            for i in range(3)
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(
        m, "_resolve_chat_titles", lambda ids: {f"-10{i}": f"Чат{i}" for i in range(3)}
    )

    ctx = await m.augment_query_with_memory("тест множества chunks")
    prompt = ctx.augmented_prompt

    assert "КРИТИЧНО про [MEMORY] блоки" in prompt
    # Prompt содержит минимум 3 [MEMORY] блока данных (+ возможны в инструкции)
    assert prompt.count("[MEMORY]") >= 3
    for i in range(3):
        assert f'в чате "Чат{i}"' in prompt


@pytest.mark.asyncio
async def test_no_augmentation_when_disabled(monkeypatch):
    """При force_enable=False — augmented_prompt == original query, инструкции нет."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "false")
    from src.core import memory_context_augmenter as m

    ctx = await m.augment_query_with_memory("привет", force_enable=False)
    assert ctx.augmented_prompt == "привет"
    assert "КРИТИЧНО" not in ctx.augmented_prompt
    assert not ctx.enabled
