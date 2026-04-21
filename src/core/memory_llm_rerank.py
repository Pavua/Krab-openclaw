"""Optional LLM re-ranking поверх RRF output (Chado §6 P1).

Концепция: после RRF+MMR у нас top-N кандидатов (score 0.0-1.0). Gemini 3 Pro
оценивает каждого по релевантности к query, возвращает top-K refined.

Opt-in через KRAB_RAG_LLM_RERANK_ENABLED=1. По умолчанию off (cost+latency).

Default provider: `google/gemini-3-pro-preview` (качество важнее latency — user preference).
Translator остаётся flash для sub-second UX; здесь pro.

Cost estimate: ~500 tokens prompt × 50 candidates = ~25k tokens / query.
Latency: ~2-4s pro.

Public:
- async llm_rerank(query, candidates, top_k=10, *, provider=None, timeout=3.0) -> list[Candidate]
- is_enabled() -> bool
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)

# Максимум кандидатов, которых отправляем в LLM за один раз.
_MAX_CANDIDATES_FOR_LLM = 50


@dataclass
class Candidate:
    chunk_id: str
    text: str
    rrf_score: float
    llm_score: float | None = None


def is_enabled() -> bool:
    """Проверяет env-флаг KRAB_RAG_LLM_RERANK_ENABLED."""
    return os.getenv("KRAB_RAG_LLM_RERANK_ENABLED", "0") == "1"


def _build_prompt(query: str, candidates: list[Candidate]) -> str:
    """Строит батч-промпт для оценки релевантности.

    Формат: один кандидат на строку — LLM возвращает одно целое число (0-10)
    на каждую пронумерованную строку.
    """
    lines = [
        f"Rate each chunk 0-10 for relevance to the query. "
        f"Output ONLY a JSON array of integers (one per chunk, same order).\n"
        f"Query: {query}\n"
    ]
    for i, c in enumerate(candidates):
        # Обрезаем текст до 300 символов — экономим токены.
        snippet = c.text[:300].replace("\n", " ")
        lines.append(f"[{i}] {snippet}")
    return "\n".join(lines)


def _parse_scores(raw: str, expected_count: int) -> list[float | None]:
    """Парсит JSON-массив из ответа LLM. Robust: ищет первый [...] блок."""
    import json
    import re

    # Ищем первый JSON-массив в ответе.
    match = re.search(r"\[[\d,\s]+\]", raw)
    if not match:
        return [None] * expected_count
    try:
        values = json.loads(match.group())
    except json.JSONDecodeError:
        return [None] * expected_count
    if not isinstance(values, list):
        return [None] * expected_count
    # Нормализуем в [0.0, 1.0] и дополняем/обрезаем до нужной длины.
    result: list[float | None] = []
    for i in range(expected_count):
        if i < len(values):
            try:
                result.append(float(values[i]) / 10.0)
            except (TypeError, ValueError):
                result.append(None)
        else:
            result.append(None)
    return result


async def llm_rerank(
    query: str,
    candidates: list[Candidate],
    *,
    top_k: int = 10,
    provider: Any = None,  # injected LLM provider for testability
    timeout_sec: float = 3.0,
) -> list[Candidate]:
    """Возвращает top-K кандидатов, пересортированных по llm_score (desc).

    Если is_enabled() is False, provider is None, или timeout hit —
    возвращает неизменённый срез candidates[:top_k].

    Args:
        query: исходный запрос пользователя.
        candidates: список Candidate (уже отсортированный по rrf_score desc).
        top_k: сколько лучших вернуть.
        provider: LLM-провайдер с методом `async generate(prompt: str) -> str`.
                  None → no-op (cost-free fallback).
        timeout_sec: максимум на весь LLM-вызов. При превышении — fallback.

    Returns:
        list[Candidate] с заполненным llm_score у тех, кого оценил LLM.
    """
    if not candidates:
        return []

    # Быстрый путь: выключен или нет провайдера.
    if not is_enabled() or provider is None:
        logger.debug("memory_llm_rerank_skip", reason="disabled_or_no_provider")
        return candidates[:top_k]

    # Берём не более _MAX_CANDIDATES_FOR_LLM кандидатов для LLM.
    batch = candidates[:_MAX_CANDIDATES_FOR_LLM]

    try:
        prompt = _build_prompt(query, batch)
        raw_response: str = await asyncio.wait_for(
            provider.generate(prompt),
            timeout=timeout_sec,
        )
        scores = _parse_scores(raw_response, len(batch))
    except asyncio.TimeoutError:
        logger.warning(
            "memory_llm_rerank_timeout",
            timeout_sec=timeout_sec,
            fallback="rrf_order",
        )
        return candidates[:top_k]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "memory_llm_rerank_error",
            error=str(exc),
            error_type=type(exc).__name__,
            fallback="rrf_order",
        )
        return candidates[:top_k]

    # Применяем llm_score к кандидатам.
    scored_batch: list[Candidate] = []
    for cand, llm_s in zip(batch, scores):
        scored_batch.append(
            Candidate(
                chunk_id=cand.chunk_id,
                text=cand.text,
                rrf_score=cand.rrf_score,
                llm_score=llm_s,
            )
        )

    # Кандидаты вне batch (если candidates > _MAX_CANDIDATES_FOR_LLM) —
    # оставляем без llm_score.
    remainder = candidates[_MAX_CANDIDATES_FOR_LLM:]

    # Сортируем: сначала по llm_score (если есть), затем по rrf_score как tiebreak.
    def sort_key(c: Candidate) -> tuple[float, float]:
        return (c.llm_score if c.llm_score is not None else -1.0, c.rrf_score)

    scored_batch.sort(key=sort_key, reverse=True)

    # Остаток — без llm_score, добавляем в хвост.
    merged = scored_batch + remainder
    logger.debug(
        "memory_llm_rerank_applied",
        query_len=len(query),
        batch_size=len(batch),
        top_k=top_k,
    )
    return merged[:top_k]
