"""Optional LLM re-ranking поверх RRF output (Chado §6 P1).

Концепция: после RRF+MMR у нас top-N кандидатов (score 0.0-1.0). Gemini 3 Pro
оценивает каждого по релевантности к query, возвращает top-K refined.

Opt-in через KRAB_RAG_LLM_RERANK_ENABLED=1. По умолчанию off (cost+latency).

Wave 43-A: Adaptive threshold — LLM rerank вызывается только для borderline запросов.
  * top-1 RRF score > HIGH_CONFIDENCE → skip LLM (очевидный победитель)
  * top-1 RRF score < LOW_CONFIDENCE  → skip LLM (ничего не найдено)
  * иначе → apply LLM rerank (borderline, где LLM даёт max value)
Env override: KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED (default off — legacy path сохранён).

Wave 43-A: LRU cache — повторный запрос с тем же query+top_k в течение 5 минут
возвращает cached результат без LLM-вызова.
  * Ёмкость: 100 записей (KRAB_RERANK_CACHE_MAXSIZE)
  * TTL: 300 секунд (KRAB_RERANK_CACHE_TTL_SEC)

Default provider: `google/gemini-3-pro-preview` (качество важнее latency — user preference).
Translator остаётся flash для sub-second UX; здесь pro.

Cost estimate: ~500 tokens prompt × 50 candidates = ~25k tokens / query.
Latency: ~2-4s pro.

Public:
- async llm_rerank(query, candidates, top_k=10, *, provider=None, timeout=3.0) -> list[Candidate]
- is_enabled() -> bool
- should_apply_llm_rerank(top_rrf_score) -> bool   # adaptive threshold
- make_rerank_cache_key(query, top_k) -> str        # cache key helper
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)

# Максимум кандидатов, которых отправляем в LLM за один раз.
_MAX_CANDIDATES_FOR_LLM = 50

# ---------------------------------------------------------------------------
# Wave 43-A: Adaptive rerank threshold.
# ---------------------------------------------------------------------------

# Если top-1 RRF-score выше порога — результат очевиден, LLM не нужен.
_ADAPTIVE_HIGH_CONFIDENCE: float = float(os.environ.get("KRAB_RERANK_HIGH_CONF", "0.85"))
# Если top-1 RRF-score ниже порога — FTS ничего толком не нашёл, LLM не поможет.
_ADAPTIVE_LOW_CONFIDENCE: float = float(os.environ.get("KRAB_RERANK_LOW_CONF", "0.20"))


def adaptive_rerank_enabled() -> bool:
    """Проверяет KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED (default off)."""
    return os.getenv("KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED", "0") == "1"


def should_apply_llm_rerank(top_rrf_score: float) -> bool:
    """Возвращает True, если score в borderline-диапазоне и LLM нужен.

    Логика:
      * score >= HIGH → явный победитель → skip (False)
      * score <= LOW  → ничего не найдено → skip (False)
      * иначе         → borderline → apply (True)

    При adaptive_rerank_enabled()=False всегда True (legacy behaviour сохранён).
    """
    if not adaptive_rerank_enabled():
        return True  # legacy: всегда применяем LLM rerank
    hi = _ADAPTIVE_HIGH_CONFIDENCE
    lo = _ADAPTIVE_LOW_CONFIDENCE
    if top_rrf_score >= hi:
        logger.debug(
            "memory_llm_rerank_skip_high_conf",
            top_rrf_score=round(top_rrf_score, 3),
            threshold=hi,
        )
        return False
    if top_rrf_score <= lo:
        logger.debug(
            "memory_llm_rerank_skip_low_conf",
            top_rrf_score=round(top_rrf_score, 3),
            threshold=lo,
        )
        return False
    logger.debug(
        "memory_llm_rerank_borderline",
        top_rrf_score=round(top_rrf_score, 3),
        lo=lo,
        hi=hi,
    )
    return True


# ---------------------------------------------------------------------------
# Wave 43-A: LRU result cache.
# ---------------------------------------------------------------------------

_CACHE_MAXSIZE: int = int(os.environ.get("KRAB_RERANK_CACHE_MAXSIZE", "100"))
_CACHE_TTL_SEC: float = float(os.environ.get("KRAB_RERANK_CACHE_TTL_SEC", "300"))

# {cache_key: (timestamp, list[Candidate])}
_rerank_cache: OrderedDict[str, tuple[float, list["Candidate"]]] = OrderedDict()


def make_rerank_cache_key(query: str, top_k: int) -> str:
    """Ключ кэша: query + top_k (достаточно для деdup повторных запросов)."""
    return f"{top_k}:{query}"


def _cache_get(key: str) -> list["Candidate"] | None:
    """LRU hit: перемещаем в конец, проверяем TTL. None при miss/expired."""
    entry = _rerank_cache.get(key)
    if entry is None:
        return None
    ts, candidates = entry
    if time.monotonic() - ts > _CACHE_TTL_SEC:
        # Устарело — удаляем.
        _rerank_cache.pop(key, None)
        return None
    _rerank_cache.move_to_end(key)
    return candidates


def _cache_put(key: str, candidates: list["Candidate"]) -> None:
    """Сохраняем в LRU cache с timestamp. Eviction oldest при overflow."""
    _rerank_cache[key] = (time.monotonic(), list(candidates))
    _rerank_cache.move_to_end(key)
    while len(_rerank_cache) > _CACHE_MAXSIZE:
        _rerank_cache.popitem(last=False)


def clear_rerank_cache() -> None:
    """Очищает весь LRU cache (полезно в тестах и при смене провайдера)."""
    _rerank_cache.clear()


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

    Wave 43-A improvements:
      * Adaptive threshold: если top-1 RRF score за пределами borderline-зоны
        (очень высокий или очень низкий) — LLM rerank пропускается.
        Управляется через KRAB_MEMORY_ADAPTIVE_RERANK_ENABLED=1.
      * LRU cache: повторный query+top_k в течение TTL → cached результат.
        Управляется через KRAB_RERANK_CACHE_MAXSIZE / KRAB_RERANK_CACHE_TTL_SEC.

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

    # Wave 43-A: Adaptive threshold — skip LLM для очевидных случаев.
    top_score = candidates[0].rrf_score if candidates else 0.0
    if not should_apply_llm_rerank(top_score):
        logger.debug(
            "memory_llm_rerank_skip_adaptive",
            top_rrf_score=round(top_score, 3),
        )
        return candidates[:top_k]

    # Wave 43-A: LRU cache — проверяем перед LLM-вызовом.
    cache_key = make_rerank_cache_key(query, top_k)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug(
            "memory_llm_rerank_cache_hit",
            query_len=len(query),
            top_k=top_k,
        )
        return cached

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
    result = merged[:top_k]

    logger.debug(
        "memory_llm_rerank_applied",
        query_len=len(query),
        batch_size=len(batch),
        top_k=top_k,
    )

    # Wave 43-A: сохраняем результат в LRU cache.
    _cache_put(cache_key, result)

    return result
