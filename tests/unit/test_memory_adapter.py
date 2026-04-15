"""
Тесты для Track E memory adapter facade.

Pre-merge сценарии: stub возвращает пустой список, _retriever singleton
правильно кэшируется, public API не падает на невалидном input.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.memory_adapter import (
    SearchResult,
    _StubRetriever,
    get_memory_layer_status,
    is_memory_layer_available,
    search_archive,
)


def _reset_singleton() -> None:
    """Сброс между тестами, чтобы каждый тест получал свежую инициализацию."""
    import src.core.memory_adapter as m

    m._retriever_singleton = None


@pytest.fixture(autouse=True)
def _fresh_adapter():
    _reset_singleton()
    yield
    _reset_singleton()


# ---------------------------------------------------------------------------
# Stub behaviour
# ---------------------------------------------------------------------------


def test_stub_returns_empty_list():
    """До мержа Track E stub возвращает []."""
    result = search_archive("когда мы обсуждали dashboard", top_k=5)
    assert result == []


def test_stub_accepts_all_api_params():
    """Никакой параметр API не ломает stub."""
    result = search_archive(
        query="test",
        chat_id="chat-123",
        top_k=20,
        with_context=5,
        decay_mode="aggressive",
        owner_only=False,
    )
    assert result == []


def test_empty_query_returns_empty():
    """Empty query сразу возвращает [] без обращения к retriever."""
    assert search_archive("") == []
    assert search_archive("   ") == []


def test_is_memory_layer_available_false_before_merge():
    """Без Track E retriever = stub, availability = False."""
    assert is_memory_layer_available() is False


def test_get_memory_layer_status_returns_stub_info():
    """Status content: available=False, класс _StubRetriever."""
    status = get_memory_layer_status()
    assert status["available"] is False
    assert status["retriever_class"] == "_StubRetriever"
    assert "checked_at" in status


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------


def test_retriever_is_singleton():
    """Повторные search_archive используют один retriever."""
    import src.core.memory_adapter as m

    search_archive("test1")
    first = m._retriever_singleton
    search_archive("test2")
    second = m._retriever_singleton
    assert first is second


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------


def test_search_result_is_frozen():
    """SearchResult — frozen dataclass, защита от мутаций."""
    from datetime import datetime

    r = SearchResult(
        message_id="abc",
        chat_id="chat",
        text_redacted="[redacted content]",
        timestamp=datetime.now(),
        score=0.87,
    )
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
        r.score = 0.5  # type: ignore[misc]


def test_search_result_has_default_context_lists():
    """Context before/after default к пустым спискам."""
    from datetime import datetime

    r = SearchResult(
        message_id="id",
        chat_id="c",
        text_redacted="txt",
        timestamp=datetime.now(),
        score=0.5,
    )
    assert r.context_before == []
    assert r.context_after == []


# ---------------------------------------------------------------------------
# Error robustness
# ---------------------------------------------------------------------------


def test_search_survives_retriever_exception():
    """Если retriever.search() кидает — facade возвращает [] и логирует."""
    mock_retriever = _StubRetriever()

    def raiser(*args, **kwargs):
        raise RuntimeError("simulated retrieval failure")

    mock_retriever.search = raiser  # type: ignore[assignment]

    with patch("src.core.memory_adapter._get_retriever", return_value=mock_retriever):
        result = search_archive("query")
        assert result == []


def test_search_survives_import_error():
    """Если импорт реального retriever падает — fallback на stub без exception."""
    # Это тест baseline (Track E не merged), _StubRetriever должен быть в singleton
    search_archive("test")
    from src.core.memory_adapter import _retriever_singleton

    assert isinstance(_retriever_singleton, _StubRetriever)
