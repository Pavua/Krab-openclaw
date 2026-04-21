"""Gemini 3 Pro provider adapter для memory_llm_rerank (Chado §6 P1 wiring).

Использует google/gemini-3-pro-preview (user preference: pro не flash).
Opt-in через env KRAB_RAG_LLM_RERANK_ENABLED=1; провайдер читается
из model routing через cloud_gateway или direct Gemini API.

Public:
- class GeminiRerankProvider:
    async def score_batch(query: str, chunks: list[str]) -> list[float]
    async def generate(prompt: str) -> str  # совместим с memory_llm_rerank
- default_provider() -> GeminiRerankProvider | None
  (returns None if provider unavailable — e.g. no API key)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Модель для реранкинга: pro, не flash (пользовательский приоритет).
_RERANK_MODEL = "gemini-3-pro-preview"
_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_DEFAULT_TIMEOUT = 5.0


class GeminiRerankProvider:
    """Провайдер LLM-реранкинга через Google Gemini 3 Pro.

    Совместим с интерфейсом memory_llm_rerank:
        async generate(prompt: str) -> str

    Дополнительно предоставляет высокоуровневый:
        async score_batch(query: str, chunks: list[str]) -> list[float]
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = _RERANK_MODEL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Низкоуровневый API — совместим с memory_llm_rerank.generate().
    # ------------------------------------------------------------------

    async def generate(self, prompt: str) -> str:
        """Отправляет prompt в Gemini и возвращает текстовый ответ.

        Throws: httpx.HTTPError, asyncio.TimeoutError — пусть обрабатывает caller.
        """
        url = f"{_GEMINI_API_BASE}/{self._model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            # Ограничиваем вывод — нам нужен только JSON-массив.
            "generationConfig": {
                "maxOutputTokens": 256,
                "temperature": 0.0,
            },
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, params={"key": self._api_key}, json=body)
            resp.raise_for_status()
            data = resp.json()

        # Gemini response: candidates[0].content.parts[0].text
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("gemini_rerank_parse_response_failed", extra={"error": str(exc)})
            return ""
        return text

    # ------------------------------------------------------------------
    # Высокоуровневый API.
    # ------------------------------------------------------------------

    async def score_batch(self, query: str, chunks: list[str]) -> list[float]:
        """Оценивает чанки по релевантности к query. Возвращает 0-1 скоры.

        При ошибке парсинга или сетевой ошибке — возвращает [].
        """
        if not chunks:
            return []

        prompt = _build_score_batch_prompt(query, chunks)
        try:
            raw = await asyncio.wait_for(self.generate(prompt), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "gemini_rerank_timeout",
                extra={"model": self._model, "n_chunks": len(chunks)},
            )
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "gemini_rerank_error",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return []

        scores = _parse_score_response(raw, expected_count=len(chunks))
        return scores


# ---------------------------------------------------------------------------
# Утилиты.
# ---------------------------------------------------------------------------


def _build_score_batch_prompt(query: str, chunks: list[str]) -> str:
    """Строит промпт: numbered chunks → JSON array of 0-10 scores."""
    lines = [
        "Rate each chunk 0-10 for relevance to the query. "
        "Output ONLY a JSON array of numbers (one per chunk, same order). "
        "No other text.\n",
        f"Query: {query}\n",
    ]
    for i, chunk in enumerate(chunks):
        snippet = chunk[:300].replace("\n", " ")
        lines.append(f"[{i}] {snippet}")
    return "\n".join(lines)


def _parse_score_response(raw: str, expected_count: int) -> list[float]:
    """Парсит JSON-массив из ответа. При ошибке — возвращает [].

    Нормализует значения 0-10 → 0.0-1.0.
    """
    if not raw:
        logger.warning("gemini_rerank_empty_response")
        return []

    match = re.search(r"\[[-\d.,\s]+\]", raw)
    if not match:
        logger.warning("gemini_rerank_no_json_array", extra={"raw_snippet": raw[:120]})
        return []

    try:
        values = json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning("gemini_rerank_json_decode_failed", extra={"error": str(exc)})
        return []

    if not isinstance(values, list):
        logger.warning("gemini_rerank_not_a_list")
        return []

    if len(values) != expected_count:
        logger.warning(
            "gemini_rerank_count_mismatch",
            extra={"expected": expected_count, "got": len(values)},
        )
        # Допускаем частичный ответ — пусть caller решает.
        # Если меньше — возвращаем [] (безопаснее, чем смещённые скоры).
        if len(values) < expected_count:
            return []

    result: list[float] = []
    for i in range(expected_count):
        try:
            v = float(values[i])
            # Clamp в [0, 10] и нормализуем.
            v = max(0.0, min(10.0, v))
            result.append(v / 10.0)
        except (TypeError, ValueError):
            logger.warning("gemini_rerank_bad_value", extra={"index": i, "value": values[i]})
            return []

    return result


# ---------------------------------------------------------------------------
# Фабрика.
# ---------------------------------------------------------------------------


def default_provider(
    *,
    model: str = _RERANK_MODEL,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[GeminiRerankProvider]:
    """Возвращает GeminiRerankProvider если доступен API-ключ, иначе None.

    Приоритет ключей: GEMINI_API_KEY_PAID (если GEMINI_PAID_KEY_ENABLED=1),
    затем GEMINI_API_KEY_FREE, затем GEMINI_API_KEY.
    """
    paid_enabled = os.getenv("GEMINI_PAID_KEY_ENABLED", "0").strip().lower() in ("1", "true", "yes")
    api_key: Optional[str] = None

    if paid_enabled:
        api_key = os.getenv("GEMINI_API_KEY_PAID")

    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY_FREE") or os.getenv("GEMINI_API_KEY")

    if not api_key:
        logger.debug("gemini_rerank_no_api_key_available")
        return None

    return GeminiRerankProvider(api_key=api_key, model=model, timeout=timeout)
