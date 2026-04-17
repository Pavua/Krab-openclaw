"""
Memory Context Augmenter — prepends top-k memory chunks to LLM prompt.

При `!ask <query>` (или любом LLM-запросе) шлём query через hybrid retrieval,
получаем top-k chunks и формируем system-prompt prefix:

    [Контекст из твоей памяти:]
    1. [fts+semantic] ...
    2. [fts] ...
    ---
    Вопрос: <original>

Env:
    MEMORY_AUTO_CONTEXT_ENABLED=false  — opt-in по умолчанию
    MEMORY_AUTO_CONTEXT_TOP_K=3
    MEMORY_AUTO_CONTEXT_MIN_SCORE=0.3  — skip chunks с low score

Интеграция с retrieval:
    Модуль пытается импортировать `hybrid_search` из `memory_hybrid_reranker`
    (Wave 7-G). Если модуль отсутствует, fallback на `HybridRetriever.search()`
    из `memory_retrieval` с адаптацией результатов к ожидаемому интерфейсу
    (`rrf_score`, `text`, `chunk_id`, `sources`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .logger import get_logger

logger = get_logger(__name__)


def _env_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in ("true", "1", "yes", "on")


# Runtime-resolved на каждом вызове: позволяет тестам переопределять env через
# monkeypatch без reload модуля.
def _auto_context_enabled() -> bool:
    return _env_bool("MEMORY_AUTO_CONTEXT_ENABLED", "false")


def _default_top_k() -> int:
    try:
        return int(os.environ.get("MEMORY_AUTO_CONTEXT_TOP_K", "3"))
    except ValueError:
        return 3


def _default_min_score() -> float:
    try:
        return float(os.environ.get("MEMORY_AUTO_CONTEXT_MIN_SCORE", "0.3"))
    except ValueError:
        return 0.3


# Совместимость: некоторые тесты могут импортировать старые константы.
AUTO_CONTEXT_ENABLED = _auto_context_enabled()
DEFAULT_TOP_K = _default_top_k()
DEFAULT_MIN_SCORE = _default_min_score()


@dataclass
class AugmentedContext:
    """Результат augmentation: безопасен для передачи прямо в LLM."""

    query: str
    augmented_prompt: str
    chunks_used: list[dict] = field(default_factory=list)
    enabled: bool = False


@dataclass
class _Adapted:
    """Внутренний datatype, совместимый с ожидаемым API hybrid_search."""

    rrf_score: float
    text: str
    chunk_id: str
    sources: list[str]


def _adapt_retrieval_result(r: Any) -> Optional[_Adapted]:
    """
    Приводит любую retrieval-структуру к interface с `rrf_score`, `text`,
    `chunk_id`, `sources`. Duck-typing: если атрибутов уже нет — пытаемся
    достать из известных полей (`score`, `text_redacted`, `message_id`).
    """
    if r is None:
        return None

    # Already-compatible объект — забираем поля напрямую.
    score = (
        getattr(r, "rrf_score", None)
        if getattr(r, "rrf_score", None) is not None
        else getattr(r, "score", None)
    )
    if score is None:
        score = 0.0

    text = getattr(r, "text", None)
    if text is None:
        text = getattr(r, "text_redacted", "") or ""

    chunk_id = getattr(r, "chunk_id", None)
    if chunk_id is None:
        chunk_id = getattr(r, "message_id", "") or ""

    sources = list(getattr(r, "sources", []) or []) or ["hybrid"]

    return _Adapted(
        rrf_score=float(score),
        text=str(text),
        chunk_id=str(chunk_id),
        sources=sources,
    )


def _call_retrieval(query: str, limit: int) -> list[Any]:
    """
    Runtime-resolved retrieval. В первую очередь пробуем `hybrid_search` из
    `memory_hybrid_reranker`. Если модуля нет — fallback на `HybridRetriever`.
    Exception в fallback → пробрасывается в augment для graceful-обработки.
    """
    # 1) Предпочитаемый путь — Wave 7-G модуль.
    try:
        from . import memory_hybrid_reranker  # type: ignore[import-not-found]

        fn = getattr(memory_hybrid_reranker, "hybrid_search", None)
        if fn is not None:
            return list(fn(query, limit=limit))
    except ImportError:
        pass

    # 2) Fallback — существующий HybridRetriever (FTS5-only).
    from .memory_retrieval import HybridRetriever

    retriever = HybridRetriever()
    try:
        results = retriever.search(query, top_k=limit)
    finally:
        retriever.close()
    return [_adapt_retrieval_result(r) for r in results]


async def augment_query_with_memory(
    query: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    force_enable: Optional[bool] = None,
) -> AugmentedContext:
    """
    Запрашивает hybrid_search, формирует augmented prompt.

    Args:
        query: original user query.
        top_k: сколько chunks брать в контекст (default — env).
        min_score: минимальный RRF score, ниже которого chunk отбрасывается.
        force_enable: перекрывает env (True/False). Для флагов
            `--with-memory` / `--no-memory`.

    Returns:
        AugmentedContext. Если retrieval выключен или не дал результатов —
        `augmented_prompt == query` (original).
    """
    # Резолвим параметры runtime — env читается на момент вызова.
    top_k = top_k if top_k is not None else _default_top_k()
    min_score = min_score if min_score is not None else _default_min_score()
    if force_enable is None:
        enabled = _auto_context_enabled()
    else:
        enabled = force_enable

    if not enabled or not query or not query.strip():
        return AugmentedContext(
            query=query,
            augmented_prompt=query,
            enabled=False,
        )

    try:
        # hybrid_search может быть sync — вызываем напрямую. Для long-running
        # в будущем подхватим через asyncio.to_thread в caller.
        results = hybrid_search(query, limit=top_k)
    except ImportError:
        logger.warning("auto_context_hybrid_search_unavailable")
        return AugmentedContext(query=query, augmented_prompt=query, enabled=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto_context_search_failed", error=str(exc))
        return AugmentedContext(query=query, augmented_prompt=query, enabled=True)

    # Нормализуем и фильтруем по score.
    strong_results: list[_Adapted] = []
    for r in results or []:
        adapted = _adapt_retrieval_result(r)
        if adapted is None:
            continue
        if adapted.rrf_score >= min_score:
            strong_results.append(adapted)

    if not strong_results:
        return AugmentedContext(
            query=query,
            augmented_prompt=query,
            chunks_used=[],
            enabled=True,
        )

    # Формируем prefix.
    lines = ["[Контекст из твоей памяти:]"]
    for i, r in enumerate(strong_results, 1):
        text_preview = _short_preview(r.text, max_len=400)
        sources = "+".join(r.sources) if r.sources else "hybrid"
        lines.append(f"{i}. [{sources}] {text_preview}")
    lines.append("---")
    lines.append(f"Вопрос: {query}")

    augmented = "\n".join(lines)
    chunks_meta = [
        {
            "chunk_id": r.chunk_id,
            "score": r.rrf_score,
            "sources": list(r.sources),
        }
        for r in strong_results
    ]

    logger.info(
        "memory_auto_context_applied",
        query_preview=query[:100],
        chunks_count=len(strong_results),
    )

    return AugmentedContext(
        query=query,
        augmented_prompt=augmented,
        chunks_used=chunks_meta,
        enabled=True,
    )


def _short_preview(text: str, max_len: int = 400) -> str:
    """Cleanup text для inline context: убираем переводы строк и обрезаем."""
    if not text:
        return ""
    t = text.replace("\n", " ").strip()
    if len(t) <= max_len:
        return t
    return t[:max_len] + "..."


# Shim-обёртка: позволяет тестам делать monkeypatch.setattr(m, "hybrid_search", ...)
# без необходимости патчить _call_retrieval.
def hybrid_search(query: str, limit: int = 3) -> list[Any]:
    """Shim over retrieval pipeline, патчится в тестах."""
    return _call_retrieval(query, limit=limit)
