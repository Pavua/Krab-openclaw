# -*- coding: utf-8 -*-
"""
trigger_detector — семантическое определение неявных триггеров обращения к Крабу.

Дополняет `krab_identity.is_krab_mentioned()` эвристиками контекста:
  - Implicit question-at-AI (вопрос «в воздух» на русском)
  - Follow-up к недавнему ответу Краба в группе
  - Обращение по generic AI-алиасу (бот, ии, нейронка…)

Возвращает `TriggerResult` с типом и весом (0.0–1.0).
Конфигурируется через env `KRAB_IMPLICIT_TRIGGER_THRESHOLD` (default 0.4).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

import structlog

if TYPE_CHECKING:
    from .chat_response_policy import ChatResponsePolicyStore
    from .llm_intent_classifier import IntentResult, LLMIntentClassifier

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Типы
# ---------------------------------------------------------------------------


class TriggerType(str, Enum):
    """Тип обнаруженного триггера."""

    EXPLICIT = "explicit"  # явный @mention / «краб» (уже есть в is_krab_mentioned)
    IMPLICIT_QUESTION = "implicit_question"  # вопрос в воздух
    FOLLOWUP_TO_KRAB = "followup_to_krab"  # продолжение разговора с Крабом
    GENERIC_AI = "generic_ai"  # «бот, ии, нейронка…» + вопрос
    NONE = "none"


class TriggerResult(NamedTuple):
    """Результат detect_implicit_mention."""

    trigger_type: TriggerType
    score: float  # вес [0.0, 1.0]
    matched: str = ""  # что именно сработало (для отладки)


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------


def _threshold() -> float:
    """Порог срабатывания неявного триггера (env KRAB_IMPLICIT_TRIGGER_THRESHOLD)."""
    try:
        return float(os.environ.get("KRAB_IMPLICIT_TRIGGER_THRESHOLD", "0.4"))
    except ValueError:
        return 0.4


# ---------------------------------------------------------------------------
# Паттерны
# ---------------------------------------------------------------------------

# Вопросы «в воздух» — кто-то знает, подскажите, помогите разобраться…
_IMPLICIT_QUESTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bкто[-\s]*(?:то\s*)?знает\b", re.IGNORECASE),
    re.compile(r"\bподскажите\b", re.IGNORECASE),
    re.compile(r"\bкто\s*(?:в\s*)?теме\b", re.IGNORECASE),
    re.compile(r"\bкто\s*шарит\b", re.IGNORECASE),
    re.compile(r"\bкак\s*решить\b", re.IGNORECASE),
    re.compile(r"\bпосоветуйте\b", re.IGNORECASE),
    re.compile(r"\bпомогите\s*разобраться\b", re.IGNORECASE),
    re.compile(r"\bкто[-\s]*нибудь\s*(?:помог\w*|поможет|поможе\w*)\b", re.IGNORECASE),
    re.compile(r"\bкто[-\s]*нибудь\b", re.IGNORECASE),  # «кто-нибудь» как самостоятельный запрос
    re.compile(r"\bесть\s*(?:кто[-\s]*(?:то|нибудь)|кто)\s+знает\b", re.IGNORECASE),
    re.compile(r"\bможете\s*подсказать\b", re.IGNORECASE),
    re.compile(r"\bкто\s*может\s*помочь\b", re.IGNORECASE),
]

# Базовый вес вопроса в воздух (40 % — ниже порога «один на один»)
_IMPLICIT_QUESTION_SCORE = 0.4

# Generic AI-алиасы: «бот», «ии», «нейронка» и т.п. рядом с вопросом
_GENERIC_AI_PATTERN = re.compile(
    r"\b(ии|бот|ai|assistant|ассистент|нейронка|помощник|нейросеть|chatgpt|gpt)\b",
    re.IGNORECASE,
)
_QUESTION_MARK_NEARBY = re.compile(r"\?")
_GENERIC_AI_SCORE = 0.55

# Окно follow-up после последнего ответа Краба (секунды)
_FOLLOWUP_WINDOW_SEC: int = 5 * 60  # 5 минут
_FOLLOWUP_SCORE = 0.65


# ---------------------------------------------------------------------------
# Last-Krab-message tracker (in-process, per chat_id)
# ---------------------------------------------------------------------------


@dataclass
class _LastKrabMsgStore:
    """Хранит ts последнего ответа Краба по chat_id."""

    _store: dict[str, float] = field(default_factory=dict)

    def record(self, chat_id: str | int) -> None:
        """Зафиксировать момент ответа Краба."""
        self._store[str(chat_id)] = time.monotonic()

    def seconds_since(self, chat_id: str | int) -> float | None:
        """Вернуть секунды с последнего ответа или None если не было."""
        ts = self._store.get(str(chat_id))
        if ts is None:
            return None
        return time.monotonic() - ts

    def within_window(self, chat_id: str | int, window: int = _FOLLOWUP_WINDOW_SEC) -> bool:
        """True если Краб отвечал в этом чате в пределах window секунд."""
        elapsed = self.seconds_since(chat_id)
        return elapsed is not None and elapsed <= window


# Singleton — импортируется из userbot_bridge/других мест
last_krab_msg = _LastKrabMsgStore()


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------


def detect_implicit_mention(
    text: str,
    chat_id: str | int = "",
    *,
    is_reply_to_explicit_msg: bool = False,
    threshold: float | None = None,
) -> TriggerResult:
    """
    Обнаружить неявное обращение к Крабу.

    Args:
        text:                      Текст сообщения.
        chat_id:                   ID чата (для follow-up проверки).
        is_reply_to_explicit_msg:  True если reply на чужое (не-Краб) сообщение.
                                   В этом случае follow-up не засчитываем.
        threshold:                 Порог срабатывания (None → env/default).

    Returns:
        TriggerResult(trigger_type, score, matched)
    """
    if not text or not text.strip():
        return TriggerResult(TriggerType.NONE, 0.0)

    thresh = threshold if threshold is not None else _threshold()
    text_s = text.strip()

    # 1. Follow-up к недавнему ответу Краба
    if chat_id and not is_reply_to_explicit_msg and last_krab_msg.within_window(chat_id):
        return TriggerResult(TriggerType.FOLLOWUP_TO_KRAB, _FOLLOWUP_SCORE, "followup_window")

    # 2. Implicit question-at-AI
    for pat in _IMPLICIT_QUESTION_PATTERNS:
        m = pat.search(text_s)
        if m:
            if _IMPLICIT_QUESTION_SCORE >= thresh:
                return TriggerResult(
                    TriggerType.IMPLICIT_QUESTION,
                    _IMPLICIT_QUESTION_SCORE,
                    m.group(0),
                )
            # Ниже порога — не срабатываем
            return TriggerResult(TriggerType.NONE, _IMPLICIT_QUESTION_SCORE, m.group(0))

    # 3. Generic AI alias + вопросительный знак поблизости
    ai_match = _GENERIC_AI_PATTERN.search(text_s)
    if ai_match and _QUESTION_MARK_NEARBY.search(text_s):
        if _GENERIC_AI_SCORE >= thresh:
            return TriggerResult(
                TriggerType.GENERIC_AI,
                _GENERIC_AI_SCORE,
                ai_match.group(0),
            )
        return TriggerResult(TriggerType.NONE, _GENERIC_AI_SCORE, ai_match.group(0))

    return TriggerResult(TriggerType.NONE, 0.0)


# ---------------------------------------------------------------------------
# Smart Routing (Phase 5) — 5-stage pipeline
# ---------------------------------------------------------------------------


@dataclass
class SmartTriggerResult:
    """Результат detect_smart_trigger — итог 5-stage pipeline.

    decision_path значения:
      - "hard_gate"          — explicit mention / reply-to-me / command
      - "policy_silent"      — чат в SILENT
      - "regex_high"         — regex score >=0.6
      - "regex_low"          — regex score <0.2 (drop без LLM)
      - "regex_threshold_fallback" — LLM unavailable, regex против policy threshold
      - "llm_yes" / "llm_no" — LLM ответил should_respond=true/false
      - "llm_error_fallback" — LLM error → fallback на regex threshold
    """

    should_respond: bool
    decision_path: str
    confidence: float
    legacy_result: TriggerResult | None = None
    intent_result: "IntentResult | None" = None


async def detect_smart_trigger(
    text: str,
    chat_id: str,
    *,
    is_reply_to_me: bool,
    has_explicit_mention: bool,
    has_command: bool,
    chat_context: list,
    policy_store: "ChatResponsePolicyStore",
    llm_classifier: "LLMIntentClassifier | None" = None,
) -> SmartTriggerResult:
    """5-stage smart routing pipeline (Session 26 Smart Routing).

    Stage 1: hard gates (always respond) — command/mention/reply-to-me.
    Stage 2: per-chat policy — SILENT → drop.
    Stage 3: regex fast filter — score>=0.6 → respond, score<0.2 → drop.
    Stage 4: LLM intent classifier для borderline (0.2-0.6).
    Stage 5: fallback на regex+threshold при отсутствии/ошибке LLM.
    """
    # Lazy import чтобы избежать circular import (chat_response_policy → нет;
    # llm_intent_classifier тоже не импортит нас).
    from .chat_response_policy import ChatMode

    # Stage 1: Hard gates
    if has_command or has_explicit_mention or is_reply_to_me:
        return SmartTriggerResult(
            should_respond=True,
            decision_path="hard_gate",
            confidence=1.0,
        )

    # Stage 2: Per-chat policy
    policy = policy_store.get_policy(chat_id)
    if policy.mode == ChatMode.SILENT:
        return SmartTriggerResult(
            should_respond=False,
            decision_path="policy_silent",
            confidence=1.0,
        )

    # Stage 3: Regex fast filter
    legacy = detect_implicit_mention(
        text,
        chat_id,
        is_reply_to_explicit_msg=False,
    )

    threshold = policy.effective_threshold()

    # High confidence regex score → respond
    if legacy.score >= 0.6 and legacy.trigger_type != TriggerType.NONE:
        return SmartTriggerResult(
            should_respond=True,
            decision_path="regex_high",
            confidence=legacy.score,
            legacy_result=legacy,
        )

    # Very low score → drop без LLM
    if legacy.score < 0.2:
        return SmartTriggerResult(
            should_respond=False,
            decision_path="regex_low",
            confidence=legacy.score,
            legacy_result=legacy,
        )

    # Stage 4: LLM intent (borderline 0.2-0.6)
    if llm_classifier is None:
        return SmartTriggerResult(
            should_respond=(legacy.score >= threshold),
            decision_path="regex_threshold_fallback",
            confidence=legacy.score,
            legacy_result=legacy,
        )

    try:
        intent = await llm_classifier.classify_intent_for_krab(
            text=text,
            chat_context=chat_context,
            chat_id=chat_id,
            policy=policy,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("smart_trigger_llm_error", chat_id=chat_id, error=str(exc))
        return SmartTriggerResult(
            should_respond=(legacy.score >= threshold),
            decision_path="llm_error_fallback",
            confidence=legacy.score,
            legacy_result=legacy,
        )

    if intent.error:
        return SmartTriggerResult(
            should_respond=(legacy.score >= threshold),
            decision_path="llm_error_fallback",
            confidence=legacy.score,
            legacy_result=legacy,
            intent_result=intent,
        )

    final_decision = intent.should_respond and intent.confidence >= threshold
    return SmartTriggerResult(
        should_respond=final_decision,
        decision_path=f"llm_{'yes' if intent.should_respond else 'no'}",
        confidence=intent.confidence,
        legacy_result=legacy,
        intent_result=intent,
    )


def is_implicit_trigger(
    text: str,
    chat_id: str | int = "",
    *,
    is_reply_to_explicit_msg: bool = False,
    threshold: float | None = None,
) -> bool:
    """Shortcut: True если detect_implicit_mention вернул не NONE."""
    result = detect_implicit_mention(
        text,
        chat_id,
        is_reply_to_explicit_msg=is_reply_to_explicit_msg,
        threshold=threshold,
    )
    return result.trigger_type != TriggerType.NONE
