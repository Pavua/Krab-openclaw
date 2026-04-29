"""
LLM-based query expansion через Gemini 2.5 Flash для коротких запросов.

Отличается от `memory_query_expansion.py` (synonym-based): здесь — полноценные
LLM-перефразировки для коротких запросов (< 3 токенов), где synonym-словарь
часто промахивается.

Логика:
    1. Если query короче KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS (default 3) —
       вызвать Gemini Flash, получить 3 перефразировки.
    2. Caller выполняет hybrid retrieval над union (original + rephrased),
       затем RRF, затем MMR.
    3. Если LLM недоступен или таймаут > 2s — вернуть [original] (fallback).

Config:
    KRAB_RAG_QUERY_EXPANSION_ENABLED=0 — opt-in, default off.
    KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS=3 — порог для триггера.
    KRAB_RAG_QUERY_EXPANSION_TIMEOUT=2.0 — жёсткий timeout на LLM call.

Public API:
    async expand_query_llm(query, *, provider=None) -> list[str]
        — возвращает [original, rephrase_1, rephrase_2, rephrase_3] или [original].
    is_enabled() -> bool
    min_tokens() -> int
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Protocol

from structlog import get_logger

logger = get_logger(__name__)

# Flash для скорости — cost ~50-100 tokens против pro-реранкинга.
_EXPANSION_MODEL = "gemini-2.5-flash"
_DEFAULT_TIMEOUT = 2.0
_DEFAULT_MIN_TOKENS = 3


# ---------------------------------------------------------------------------
# Конфигурация.
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Query expansion включён? Default OFF (opt-in)."""
    return os.getenv("KRAB_RAG_QUERY_EXPANSION_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def min_tokens() -> int:
    """Минимальный порог токенов для триггера expansion."""
    raw = os.getenv("KRAB_RAG_QUERY_EXPANSION_MIN_TOKENS")
    if not raw:
        return _DEFAULT_MIN_TOKENS
    try:
        val = int(raw)
        return max(1, val)
    except (TypeError, ValueError):
        return _DEFAULT_MIN_TOKENS


def timeout_s() -> float:
    """Timeout на LLM-вызов (с)."""
    raw = os.getenv("KRAB_RAG_QUERY_EXPANSION_TIMEOUT")
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# Provider protocol (минимальный — generate() совместим с Gemini*Provider).
# ---------------------------------------------------------------------------


class _LLMProvider(Protocol):
    async def generate(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Основная функция.
# ---------------------------------------------------------------------------


def _count_tokens(query: str) -> int:
    """Простая токенизация по whitespace/punct — достаточно для порога."""
    return len([t for t in re.findall(r"\w+", query) if t])


def _build_prompt(query: str) -> str:
    """Промпт для Gemini Flash: 3 перефразировки, JSON-массив."""
    return (
        "Сгенерируй 3 перефразировки следующего короткого поискового запроса. "
        "Цель — расширить покрытие за счёт синонимов и смежных формулировок. "
        "Ответ — ТОЛЬКО JSON-массив из 3 строк, без пояснений.\n\n"
        f"Запрос: {query}\n\n"
        'Пример вывода: ["вариант 1", "вариант 2", "вариант 3"]'
    )


def _parse_rephrases(raw: str, limit: int = 3) -> list[str]:
    """Парсит JSON-массив из ответа LLM. При ошибке — []."""
    if not raw:
        return []
    # Находим первый JSON-массив в ответе.
    match = re.search(r"\[.*?\]", raw, flags=re.DOTALL)
    if not match:
        return []
    try:
        values = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for v in values[:limit]:
        if isinstance(v, str):
            s = v.strip()
            if s:
                result.append(s)
    return result


async def expand_query_llm(
    query: str,
    *,
    provider: _LLMProvider | None = None,
) -> list[str]:
    """
    Расширяет короткий query через LLM. Возвращает [original, ...rephrases].

    Fallback: при любой ошибке/таймауте/disabled-flag — [query].
    Порядок гарантирует: первый элемент всегда — оригинал.
    """
    q = (query or "").strip()
    if not q:
        return []

    # Базовый fallback — только оригинал.
    baseline = [q]

    if not is_enabled():
        return baseline

    if _count_tokens(q) >= min_tokens():
        # Запрос "достаточно длинный" — expansion не нужен.
        return baseline

    # Late-bind provider из gemini_rerank_provider если не передан.
    if provider is None:
        try:
            from src.core.gemini_rerank_provider import (
                GeminiRerankProvider,
                default_provider,
            )
        except (ImportError, ModuleNotFoundError):
            # ImportError — это bug: модуль должен быть в проекте.
            # Не маскируем, а пробрасываем с явным логом для диагностики.
            logger.error(
                "memory_query_expansion_provider_import_failed",
                module="src.core.gemini_rerank_provider",
            )
            raise

        try:
            # default_provider возвращает Pro-версию; для expansion нужен Flash.
            # Переопределяем модель через прямую конструкцию, если API-ключ доступен.
            base_provider = default_provider()
            if base_provider is None:
                logger.debug("memory_query_expansion_no_provider")
                return baseline
            # base_provider._api_key — приватное поле, но мы в том же проекте.
            api_key = getattr(base_provider, "_api_key", None)
            if not api_key:
                return baseline
            provider = GeminiRerankProvider(
                api_key=api_key,
                model=_EXPANSION_MODEL,
                timeout=timeout_s(),
            )
        except Exception as exc:  # noqa: BLE001 - provider init best-effort
            logger.warning(
                "memory_query_expansion_provider_init_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return baseline

    prompt = _build_prompt(q)
    try:
        raw = await asyncio.wait_for(provider.generate(prompt), timeout=timeout_s())
    except asyncio.TimeoutError:
        logger.warning("memory_query_expansion_timeout", query_len=len(q))
        return baseline
    except (ConnectionError, OSError) as exc:
        # Известные сетевые ошибки — ожидаемый fallback-кейс.
        logger.warning(
            "memory_query_expansion_network_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return baseline
    except (json.JSONDecodeError, ValueError) as exc:
        # LLM вернул мусор / провайдер не смог распарсить — ok, fallback.
        logger.warning(
            "memory_query_expansion_parse_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return baseline
    except Exception as exc:  # noqa: BLE001 - last-resort fallback
        # Неожиданная ошибка — WARN (не debug!), чтобы было видно в логах.
        logger.warning(
            "memory_query_expansion_llm_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return baseline

    rephrases = _parse_rephrases(raw, limit=3)
    if not rephrases:
        return baseline

    # Dedup: не дублируем оригинал, если LLM вернул его дословно.
    seen = {q.lower()}
    out = [q]
    for r in rephrases:
        if r.lower() not in seen:
            seen.add(r.lower())
            out.append(r)

    logger.debug(
        "memory_query_expansion_ok",
        original_tokens=_count_tokens(q),
        variants=len(out),
    )
    return out
