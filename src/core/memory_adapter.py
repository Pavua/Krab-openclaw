"""
Адаптер между Track B (основной Krab) и Track E (Memory Layer).

Этот модуль — stub/facade. До мержа Track E он возвращает пустые результаты,
но API контракт уже зафиксирован и вся логика downstream (llm_flow,
swarm_research_pipeline, `!archive` команда) может быть написана заранее.

После merge Track E: просто импортируется реальный HybridRetriever из
`src.core.memory_retrieval` и заменяется stub в `_get_retriever()`.

## API контракт (зафиксирован в Session 8)

```python
class HybridRetriever:
    def search(
        self,
        query: str,
        chat_id: Optional[str] = None,
        top_k: int = 10,
        with_context: int = 2,
        decay_mode: str = 'auto',  # 'none' | 'gentle' | 'aggressive' | 'auto'
        owner_only: bool = True,
    ) -> list[SearchResult]: ...

@dataclass(frozen=True)
class SearchResult:
    message_id: str
    chat_id: str
    text_redacted: str  # ОБЯЗАТЕЛЬНО уже redacted (PII scrubber прошёл)
    timestamp: datetime
    score: float  # 0-1 после RRF + decay
    context_before: list[str]
    context_after: list[str]
```

## Использование

```python
from src.core.memory_adapter import search_archive, is_memory_layer_available

results = search_archive("когда мы обсуждали dashboard", top_k=5)
if results:
    for r in results:
        print(r.text_redacted, r.score)
else:
    # Memory Layer ещё не запущен, fallback на другие источники
    ...
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from structlog import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# API types (stable, will be same after Track E merge)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    """Результат hybrid retrieval (FTS5 + Model2Vec) с RRF ranking и decay."""

    message_id: str
    chat_id: str
    text_redacted: str  # PII-clean уже в момент построения
    timestamp: datetime
    score: float  # 0-1 после RRF + decay
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Retriever facade
# ---------------------------------------------------------------------------


class _StubRetriever:
    """
    Заглушка пока Track E не смержен. Возвращает пустой список.

    После merge Track E: `_get_retriever()` возвращает настоящий
    `src.core.memory_retrieval.HybridRetriever` с полной семантикой.
    """

    def search(
        self,
        query: str,
        chat_id: Optional[str] = None,
        top_k: int = 10,
        with_context: int = 2,
        decay_mode: str = "auto",
        owner_only: bool = True,
    ) -> list[SearchResult]:
        """Пустой ответ: до мержа Track E real retrieval недоступен."""
        logger.debug(
            "memory_adapter_stub_search",
            query=query[:80],
            chat_id=chat_id,
            top_k=top_k,
        )
        return []


_retriever_singleton: Any | None = None


def _get_retriever() -> Any:
    """
    Возвращает активный retriever. Lazy init, singleton.

    Пытается импортировать `src.core.memory_retrieval.HybridRetriever`.
    Если модуль не существует (Track E не смержен) — возвращает stub.
    """
    global _retriever_singleton
    if _retriever_singleton is not None:
        return _retriever_singleton

    try:
        # Попытка импорта реального retriever после Track E merge:
        from src.core.memory_retrieval import HybridRetriever  # type: ignore[import-not-found]

        _retriever_singleton = HybridRetriever()
        logger.info("memory_adapter_real_retriever_initialized")
    except ImportError:
        _retriever_singleton = _StubRetriever()
        logger.info("memory_adapter_stub_used", reason="track_e_not_merged")
    except Exception as exc:  # noqa: BLE001 — retriever init не должен ронять процесс.
        _retriever_singleton = _StubRetriever()
        logger.warning(
            "memory_adapter_retriever_init_failed",
            error=str(exc),
            fallback="stub",
        )
    return _retriever_singleton


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_archive(
    query: str,
    chat_id: Optional[str] = None,
    top_k: int = 10,
    with_context: int = 2,
    decay_mode: str = "auto",
    owner_only: bool = True,
) -> list[SearchResult]:
    """
    Гибридный поиск по Telegram-архиву.

    Args:
        query: Текст запроса (ru/en/es).
        chat_id: Ограничить выдачу конкретным чатом (None = global search).
        top_k: Сколько результатов вернуть.
        with_context: Сколько сообщений контекста до/после.
        decay_mode:
            'none' — без decay (для исторических запросов)
            'gentle' — 1/(1+0.01·age_days), halflife ~100 дней
            'aggressive' — 1/(1+0.05·age_days), halflife ~20 дней
            'auto' — детектор по query markers, default 'gentle'
        owner_only: Только owner-scoped сообщения (безопасность).

    Returns:
        Список SearchResult с уже PII-redacted text. Пустой при отсутствии
        Track E или при отсутствии совпадений.
    """
    if not query or not query.strip():
        return []
    retriever = _get_retriever()
    try:
        return retriever.search(
            query=query,
            chat_id=chat_id,
            top_k=top_k,
            with_context=with_context,
            decay_mode=decay_mode,
            owner_only=owner_only,
        )
    except Exception as exc:  # noqa: BLE001 — retrieval не должен ронять userbot.
        logger.error(
            "memory_adapter_search_failed",
            query=query[:80],
            error=str(exc),
        )
        return []


def is_memory_layer_available() -> bool:
    """Возвращает True если реальный Track E retriever активен (не stub)."""
    retriever = _get_retriever()
    return not isinstance(retriever, _StubRetriever)


def get_memory_layer_status() -> dict[str, Any]:
    """Статус для owner panel / health checks."""
    retriever = _get_retriever()
    is_real = not isinstance(retriever, _StubRetriever)
    return {
        "available": is_real,
        "retriever_class": retriever.__class__.__name__,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
