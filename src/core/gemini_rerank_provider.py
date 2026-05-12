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
# Wave 62-E (2026-05-11): был "gemini-3-pro-preview" — 404 в AI Studio v1beta direct API.
# Wave 66 (2026-05-12): переключение AI Studio paid → Vertex AI (bonus credits).
# До Wave 66: rerank делал per-chat-message запросы в paid AI Studio endpoint
# `generativelanguage.googleapis.com/v1beta/models/...` с `GEMINI_API_KEY_PAID`.
# Накатало ~€40 за неделю (Memory Phase 2 hybrid retrieval RRF+MMR fire'ит rerank
# на каждое сообщение). Решение: использовать google.genai SDK в Vertex mode
# (vertexai=True, project=caramel-anvil-492816-t5) → бонусный баланс €848 до 2027-03.
_RERANK_MODEL = "gemini-2.5-pro"
_DEFAULT_TIMEOUT = 5.0
_VERTEX_PROJECT_ENV = "KRAB_VERTEX_PROJECT"
_VERTEX_LOCATION_ENV = "KRAB_VERTEX_REGION"
_VERTEX_DEFAULT_PROJECT = "caramel-anvil-492816-t5"
_VERTEX_DEFAULT_LOCATION = "global"
# Legacy AI Studio direct endpoint — используется только если Vertex disabled
# через `KRAB_GEMINI_RERANK_VERTEX_ENABLED=0` или Vertex unavailable (ADC missing).
_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


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

        Wave 66 (2026-05-12): preferred path — Vertex AI (bonus credits через ADC).
        Fallback на AI Studio paid API только если Vertex disabled или unavailable.

        Throws: httpx.HTTPError, asyncio.TimeoutError, RuntimeError — caller handles.
        """
        # Wave 66: try Vertex first (bonus credits).
        vertex_enabled = os.environ.get(
            "KRAB_GEMINI_RERANK_VERTEX_ENABLED", "1"
        ).strip().lower() in ("1", "true", "yes", "on")
        if vertex_enabled:
            try:
                return await self._generate_via_vertex(prompt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "gemini_rerank_vertex_failed_falling_back_to_ai_studio",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                )
        # Fallback: legacy AI Studio paid API path.
        return await self._generate_via_ai_studio(prompt)

    async def _generate_via_vertex(self, prompt: str) -> str:
        """Вызов через google.genai SDK в Vertex mode (bonus credits).

        Использует ADC + project=caramel-anvil-492816-t5, location=global.
        """
        import asyncio as _asyncio  # noqa: PLC0415

        try:
            from google import genai  # type: ignore  # noqa: PLC0415
            from google.genai import types as genai_types  # type: ignore  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(f"google.genai SDK not available: {exc}") from exc

        proj = os.environ.get(_VERTEX_PROJECT_ENV) or _VERTEX_DEFAULT_PROJECT
        loc = os.environ.get(_VERTEX_LOCATION_ENV) or _VERTEX_DEFAULT_LOCATION

        def _blocking_call() -> str:
            # vertexai=True + project + location → Vertex AI (НЕ AI Studio)
            client = genai.Client(vertexai=True, project=proj, location=loc)
            config = genai_types.GenerateContentConfig(
                max_output_tokens=256,
                temperature=0.0,
            )
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
            try:
                return response.text or ""
            except Exception:  # noqa: BLE001
                parts: list[str] = []
                try:
                    for candidate in response.candidates or []:
                        for part in candidate.content.parts or []:
                            if hasattr(part, "text") and part.text:
                                parts.append(part.text)
                except Exception:  # noqa: BLE001
                    pass
                return "".join(parts)

        return await _asyncio.wait_for(
            _asyncio.to_thread(_blocking_call),
            timeout=self._timeout,
        )

    async def _generate_via_ai_studio(self, prompt: str) -> str:
        """Fallback: legacy AI Studio paid API.

        Используется только если Vertex disabled через env или Vertex SDK init упал.
        """
        url = f"{_GEMINI_API_BASE}/{self._model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 256,
                "temperature": 0.0,
            },
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, params={"key": self._api_key}, json=body)
            resp.raise_for_status()
            data = resp.json()

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
