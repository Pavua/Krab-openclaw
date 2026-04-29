# -*- coding: utf-8 -*-
"""
LLM-driven pre-classifier для implicit trigger decisions.

Opt-in через env KRAB_PROACTIVITY_LLM_CLASSIFIER=1.

Перед тем как implicit trigger сработает — короткий call к cheap LLM:
  «Стоит ли AI-ассистенту откликаться на это сообщение? Yes/No + 1 sentence why.»

Характеристики:
  - Cheap model: gemini-3-flash-preview (или любая модель через KRAB_CLASSIFIER_MODEL)
  - Timeout 2 секунды, fallback to regex heuristic при таймауте
  - Rate-limit: max 1 LLM classifier call per chat per 30 sec (избегаем цикла)
  - Всегда возвращает "YES" | "NO" | "UNCLEAR"

Публичный API:
  is_enabled()                                          -> bool
  classify(text, chat_id, context_hint)                 -> ClassifierResult
  classify_async(text, chat_id, context_hint)           -> ClassifierResult
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass

from .logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

_ENV_ENABLED = "KRAB_PROACTIVITY_LLM_CLASSIFIER"
_ENV_MODEL = "KRAB_CLASSIFIER_MODEL"
_DEFAULT_MODEL = "google/gemini-3-flash-preview"
_CLASSIFIER_TIMEOUT = float(os.environ.get("KRAB_CLASSIFIER_TIMEOUT", "2.0"))
_RATE_LIMIT_SEC = float(os.environ.get("KRAB_CLASSIFIER_RATE_LIMIT_SEC", "30.0"))


def is_enabled() -> bool:
    """True если LLM-классификатор включён через env."""
    return os.environ.get(_ENV_ENABLED, "0").strip() in ("1", "true", "yes")


def _classifier_model() -> str:
    return os.environ.get(_ENV_MODEL, _DEFAULT_MODEL).strip()


# ---------------------------------------------------------------------------
# Rate-limiter (per-chat)
# ---------------------------------------------------------------------------

_last_call_ts: dict[str, float] = {}  # chat_id -> monotonic ts


def _is_rate_limited(chat_id: str | int) -> bool:
    """True если вызов из этого чата был менее RATE_LIMIT_SEC назад."""
    key = str(chat_id)
    ts = _last_call_ts.get(key)
    if ts is None:
        return False
    return (time.monotonic() - ts) < _RATE_LIMIT_SEC


def _mark_call(chat_id: str | int) -> None:
    _last_call_ts[str(chat_id)] = time.monotonic()


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------


@dataclass
class ClassifierResult:
    """Результат LLM-классификатора."""

    verdict: str  # "YES" | "NO" | "UNCLEAR"
    reason: str = ""
    source: str = "llm"  # "llm" | "heuristic" | "rate_limited" | "disabled"
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

# Паттерны для быстрого regex-фоллбека
_YES_PATTERNS = [
    re.compile(
        r"\b(помогите|помоги|подскажите|подскажи|объясни|как\s+\w+\?|почему\s+\w+\?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\?\s*$"),  # вопрос в конце
    re.compile(r"\b(ии|бот|assistant|нейронка)\b.*\?", re.IGNORECASE),
]
_NO_PATTERNS = [
    re.compile(
        r"^(ха[\w]*|лол|lol|lmao|кек|gg|👍|ok|окей|ок|понял|ясно|спс|супер|👌)\s*$", re.IGNORECASE
    ),
    re.compile(r"^[\U0001F000-\U0001FFFF\s]+$"),  # только emoji
]


def _heuristic_classify(text: str) -> ClassifierResult:
    """Быстрый regex-фоллбек — работает <1 мс."""
    t = (text or "").strip()

    for pat in _NO_PATTERNS:
        if pat.match(t):
            return ClassifierResult("NO", "heuristic_no_pattern", source="heuristic")

    for pat in _YES_PATTERNS:
        if pat.search(t):
            return ClassifierResult("YES", "heuristic_yes_pattern", source="heuristic")

    return ClassifierResult("UNCLEAR", "heuristic_unclear", source="heuristic")


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_CLASSIFIER_PROMPT = """\
Ты — быстрый классификатор сообщений для AI-ассистента.

Контекст: сообщение в группе без явного обращения к боту.
Задача: определить, стоит ли AI-ассистенту отвечать.

Ответь СТРОГО в формате:
VERDICT: YES/NO/UNCLEAR
REASON: (1 предложение)

Сообщение:
{text}
"""


def _parse_llm_response(raw: str) -> tuple[str, str]:
    """Парсит ответ LLM в (verdict, reason)."""
    verdict = "UNCLEAR"
    reason = ""
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.upper().startswith("VERDICT:"):
            v = line.split(":", 1)[1].strip().upper()
            if v in ("YES", "NO", "UNCLEAR"):
                verdict = v
        elif line.upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return verdict, reason


async def _call_llm_async(text: str, context_hint: str = "") -> tuple[str, str]:
    """Вызов LLM через OpenClaw client (async). Возвращает (verdict, reason)."""
    try:
        from ..openclaw_client import OpenClawClient  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("openclaw_client not available")

    prompt_text = _CLASSIFIER_PROMPT.format(text=text[:500])
    if context_hint:
        prompt_text += f"\nКонтекст чата: {context_hint[:200]}"

    client = OpenClawClient()
    model = _classifier_model()

    response = await asyncio.wait_for(
        client.chat_async(
            messages=[{"role": "user", "content": prompt_text}],
            model=model,
            max_tokens=64,
        ),
        timeout=_CLASSIFIER_TIMEOUT,
    )

    raw = ""
    if isinstance(response, dict):
        raw = response.get("content", "") or response.get("text", "") or ""
    elif isinstance(response, str):
        raw = response

    return _parse_llm_response(raw)


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------


def classify(
    text: str,
    chat_id: str | int = "",
    context_hint: str = "",
) -> ClassifierResult:
    """
    Синхронный классификатор — запускает async в новом event loop или через asyncio.

    Всегда возвращает ClassifierResult (никогда не кидает).
    """
    if not is_enabled():
        return ClassifierResult("UNCLEAR", "classifier_disabled", source="disabled")

    if _is_rate_limited(chat_id):
        logger.debug("llm_classifier_rate_limited", chat_id=chat_id)
        return ClassifierResult("UNCLEAR", "rate_limited", source="rate_limited")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Уже в async-контексте — нельзя вызвать run_until_complete
            # Возвращаем heuristic в синхронном режиме
            return _heuristic_classify(text)
        result = loop.run_until_complete(classify_async(text, chat_id, context_hint))
        return result
    except Exception:  # noqa: BLE001
        return _heuristic_classify(text)


async def classify_async(
    text: str,
    chat_id: str | int = "",
    context_hint: str = "",
) -> ClassifierResult:
    """
    Async классификатор. Предпочтительный вариант в async-контексте.

    Таймаут = KRAB_CLASSIFIER_TIMEOUT (default 2s).
    При ошибке/таймауте — fallback к heuristic.
    """
    if not is_enabled():
        return ClassifierResult("UNCLEAR", "classifier_disabled", source="disabled")

    if _is_rate_limited(chat_id):
        logger.debug("llm_classifier_rate_limited", chat_id=chat_id)
        return ClassifierResult("UNCLEAR", "rate_limited", source="rate_limited")

    t0 = time.monotonic()
    _mark_call(chat_id)

    try:
        verdict, reason = await _call_llm_async(text, context_hint)
        latency = (time.monotonic() - t0) * 1000
        logger.debug(
            "llm_classifier_result",
            chat_id=chat_id,
            verdict=verdict,
            latency_ms=f"{latency:.1f}",
        )
        return ClassifierResult(verdict, reason, source="llm", latency_ms=latency)

    except asyncio.TimeoutError:
        logger.debug("llm_classifier_timeout", chat_id=chat_id, timeout=_CLASSIFIER_TIMEOUT)
        fallback = _heuristic_classify(text)
        fallback.latency_ms = (time.monotonic() - t0) * 1000
        return fallback

    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_classifier_error", chat_id=chat_id, error=str(exc))
        return _heuristic_classify(text)
