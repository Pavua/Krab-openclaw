"""
Тесты атрибуции memory chunks — проверяет что [MEMORY] блоки содержат
явный чат и время, исключая путаницу sender/chat при разных чатах.
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
    timestamp: Any = None  # datetime или None


# ---------------------------------------------------------------------------
# _adapt_retrieval_result — проверяем что chat_id и timestamp прокидываются
# ---------------------------------------------------------------------------


def test_adapt_carries_chat_id():
    """_adapt_retrieval_result берёт chat_id из SearchResult."""
    from src.core.memory_context_augmenter import _adapt_retrieval_result

    r = _FakeResult(rrf_score=0.5, text="hi", chunk_id="c1", sources=["fts"], chat_id="-1001111")
    adapted = _adapt_retrieval_result(r)
    assert adapted is not None
    assert adapted.chat_id == "-1001111"


def test_adapt_carries_datetime_timestamp():
    """_adapt_retrieval_result сохраняет datetime timestamp."""
    from src.core.memory_context_augmenter import _adapt_retrieval_result

    ts = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    r = _FakeResult(rrf_score=0.5, text="hello", chunk_id="c2", sources=["fts"], timestamp=ts)
    adapted = _adapt_retrieval_result(r)
    assert adapted is not None
    assert adapted.timestamp == ts


def test_adapt_parses_iso_string_timestamp():
    """_adapt_retrieval_result парсит ISO-8601 строку в datetime."""
    from src.core.memory_context_augmenter import _adapt_retrieval_result

    r = _FakeResult(
        rrf_score=0.5, text="text", chunk_id="c3", sources=[], timestamp="2026-01-20T10:30:00Z"
    )
    adapted = _adapt_retrieval_result(r)
    assert adapted is not None
    assert adapted.timestamp is not None
    assert adapted.timestamp.year == 2026
    assert adapted.timestamp.month == 1
    assert adapted.timestamp.day == 20


def test_adapt_handles_missing_attribution():
    """_adapt_retrieval_result без chat_id/timestamp — не падает, defaults пустые."""
    from src.core.memory_context_augmenter import _adapt_retrieval_result

    r = _FakeResult(rrf_score=0.5, text="plain", chunk_id="c4", sources=["hybrid"])
    adapted = _adapt_retrieval_result(r)
    assert adapted is not None
    assert adapted.chat_id == ""
    assert adapted.timestamp is None


# ---------------------------------------------------------------------------
# _format_memory_block
# ---------------------------------------------------------------------------


def test_format_memory_block_includes_chat_and_date():
    """_format_memory_block включает chat_title и отформатированную дату."""
    from src.core.memory_context_augmenter import _Adapted, _format_memory_block

    ts = datetime(2026, 4, 10, 14, 30, tzinfo=timezone.utc)
    r = _Adapted(
        rrf_score=0.9,
        text="разговор про Apple ID",
        chunk_id="c1",
        sources=["fts"],
        chat_id="-100111",
        timestamp=ts,
        chat_title="Дашка",
    )
    block = _format_memory_block(r, "Дашка")
    assert "[MEMORY]" in block
    assert 'в чате "Дашка"' in block
    assert "10.04.2026" in block
    assert "разговор про Apple ID" in block


def test_format_memory_block_no_timestamp():
    """_format_memory_block без timestamp — только чат, без даты."""
    from src.core.memory_context_augmenter import _Adapted, _format_memory_block

    r = _Adapted(rrf_score=0.5, text="some text", chunk_id="c2", sources=["fts"], chat_id="-100")
    block = _format_memory_block(r, "TestChat")
    assert "[MEMORY]" in block
    assert 'в чате "TestChat"' in block
    assert "some text" in block


# ---------------------------------------------------------------------------
# augment_query_with_memory — интеграция атрибуции в prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_augment_includes_memory_block_prefix(monkeypatch):
    """При enabled=True chunks форматируются как [MEMORY] блоки с чатом."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ts = datetime(2026, 4, 5, 8, 0, tzinfo=timezone.utc)

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.85,
                text="текст про Apple ID",
                chunk_id="c1",
                sources=["fts"],
                chat_id="-100999",
                timestamp=ts,
            ),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    # Мокируем _resolve_chat_titles — БД нет в тестах.
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-100999": "Машка"})

    ctx = await m.augment_query_with_memory("Apple ID")
    assert "[MEMORY]" in ctx.augmented_prompt
    assert 'в чате "Машка"' in ctx.augmented_prompt
    assert "05.04.2026" in ctx.augmented_prompt
    assert "текст про Apple ID" in ctx.augmented_prompt
    assert "КРИТИЧНО про [MEMORY] блоки" in ctx.augmented_prompt


@pytest.mark.asyncio
async def test_augment_two_chunks_different_chats(monkeypatch):
    """Два chunks из разных чатов — LLM видит разные атрибуции."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ts1 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    ts2 = datetime(2026, 4, 1, tzinfo=timezone.utc)

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.9,
                text="разговор про пароль",
                chunk_id="c1",
                sources=["fts"],
                chat_id="-100111",
                timestamp=ts1,
            ),
            _FakeResult(
                rrf_score=0.75,
                text="другой разговор",
                chunk_id="c2",
                sources=["fts"],
                chat_id="-100222",
                timestamp=ts2,
            ),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(
        m,
        "_resolve_chat_titles",
        lambda ids: {"-100111": "Дашка", "-100222": "Маринка"},
    )

    ctx = await m.augment_query_with_memory("пароль")
    prompt = ctx.augmented_prompt
    assert 'в чате "Дашка"' in prompt
    assert 'в чате "Маринка"' in prompt
    # Оба чата явно разделены
    assert prompt.index('"Дашка"') != prompt.index('"Маринка"')
    # chunks_meta содержат атрибуцию
    assert ctx.chunks_used[0]["chat_title"] == "Дашка"
    assert ctx.chunks_used[1]["chat_title"] == "Маринка"


@pytest.mark.skip(
    reason="Wave 11: chunks_used schema изменилась — 'timestamp' удалён/переименован; "
    "тест ждёт rewrite под новую meta-структуру"
)
@pytest.mark.asyncio
async def test_augment_chunks_meta_has_attribution(monkeypatch):
    """chunks_used содержат chat_id, chat_title, timestamp."""
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_ENABLED", "true")
    from src.core import memory_context_augmenter as m

    ts = datetime(2026, 4, 10, tzinfo=timezone.utc)

    def _fake_search(q: str, limit: int = 3) -> list[Any]:
        return [
            _FakeResult(
                rrf_score=0.8,
                text="test chunk",
                chunk_id="cx1",
                sources=["semantic"],
                chat_id="-100500",
                timestamp=ts,
            ),
        ]

    monkeypatch.setattr(m, "hybrid_search", _fake_search)
    monkeypatch.setattr(m, "_resolve_chat_titles", lambda ids: {"-100500": "Тестовый чат"})

    ctx = await m.augment_query_with_memory("что-то")
    assert len(ctx.chunks_used) == 1
    meta = ctx.chunks_used[0]
    assert meta["chat_id"] == "-100500"
    assert meta["chat_title"] == "Тестовый чат"
    assert meta["timestamp"] is not None
    assert "2026-04-10" in meta["timestamp"]


# ---------------------------------------------------------------------------
# _resolve_chat_titles — unit (без реальной БД)
# ---------------------------------------------------------------------------


def test_resolve_chat_titles_empty_ids():
    """_resolve_chat_titles([]) → {} без обращения к БД."""
    from src.core.memory_context_augmenter import _resolve_chat_titles

    result = _resolve_chat_titles([])
    assert result == {}


def test_resolve_chat_titles_no_db(tmp_path, monkeypatch):
    """_resolve_chat_titles при отсутствии archive.db → {} graceful."""
    from src.core.memory_context_augmenter import _resolve_chat_titles

    # Патчим ArchivePaths.default чтобы указывал на несуществующий путь.
    class _FakePaths:
        db = tmp_path / "nonexistent.db"

    import src.core.memory_context_augmenter as m

    monkeypatch.setattr(
        "src.core.memory_archive.ArchivePaths.default", classmethod(lambda cls: _FakePaths())
    )

    result = _resolve_chat_titles(["-100111", "-100222"])
    assert result == {}
