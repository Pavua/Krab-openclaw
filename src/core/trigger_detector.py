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

# Wave 40-T: ironic mentions — упоминание Краба в риторическом вопросе.
# Примеры: "ну где же Краб?", "куда пропал Краб?", "что молчит Краб?",
# "почему Краб не отвечает?". Score = 0.7 — выше threshold 0.6, чтобы
# проходить smart routing Stage 3 regex_high → respond.
_IRONIC_KRAB_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:ну\s+)?где\s+(?:же\s+)?(?:краб|kraab|нагато)\b", re.IGNORECASE),
    re.compile(
        r"\bкуда\s+(?:пропал|делся|подевал[ас]я|исчез|спрятал[ас]я)\s+(?:краб|kraab)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bчто\s+молч\w+\s+(?:краб|kraab)\b", re.IGNORECASE),
    re.compile(
        r"\bпочему\s+(?:краб|kraab)\s+(?:не\s+)?(?:отвеч\w+|молч\w+|спит)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bкраб\s+(?:где|куда|почему\s+молч)\b", re.IGNORECASE),
]

# Compound mentions: Краб упомянут рядом с @username — расширяет explicit
# detection на случаи где Krab не main subject но part of multi-target call.
# Pattern допускает Krab + @user в любом порядке в пределах 50 символов.
_COMPOUND_MENTION_PATTERN = re.compile(
    r"(?:@[A-Za-z][A-Za-z0-9_]{2,}.{0,50}?\b(?:краб|kraab)\b)"
    r"|(?:\b(?:краб|kraab)\b.{0,50}?@[A-Za-z][A-Za-z0-9_]{2,})",
    re.IGNORECASE,
)

_IRONIC_KRAB_SCORE = 0.7

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
# Implicit question detection (Wave 26-B)
# ---------------------------------------------------------------------------

# Расширенное окно для IMPLICIT_QUESTION — 10 минут после ответа Краба
_IMPLICIT_QUESTION_WINDOW_SEC: int = 10 * 60  # 10 минут

# Score IMPLICIT_QUESTION — выше threshold 0.6, сразу срабатывает на Stage 3 (regex_high)
_IMPLICIT_QUESTION_CTX_SCORE = 0.65

# ENV gate — по умолчанию включено; выключить если много ложных срабатываний
KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED: bool = os.environ.get(
    "KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED", "1"
).strip() in {"1", "true", "yes"}

# Префиксные эвристики для вопросов (нижний регистр)
_IQ_PREFIX_PATTERNS: tuple[str, ...] = (
    "а ",
    "а если",
    "а что",
    "а как",
    "а когда",
    "а почему",
    "и что",
    "и как",
    "и если",
    "ну как",
    "ну а ",
    "ну и ",
    "почему ",
    "когда ",
    "где ",
    "куда ",
    "кто ",
    "что думаешь",
    "что скажешь",
    "что считаешь",
    "как думаешь",
    "как считаешь",
    "как тебе",
    "как по-твоему",
    "как ты",
    "зачем ",
    "откуда ",
)

# Короткие standalone вопросы (ровно эти тексты после strip().lower())
_IQ_STANDALONE: frozenset[str] = frozenset(
    {
        "?",
        "продолжай",
        "и что?",
        "ну и?",
        "а дальше?",
        "дальше?",
        "интересно",
        "ну как?",
        "ну?",
    }
)


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
# Функция detect_implicit_question (Wave 26-B)
# ---------------------------------------------------------------------------


def detect_implicit_question(
    text: str,
    chat_id: str | int = "",
    *,
    in_window: bool | None = None,
) -> bool:
    """Определить, является ли сообщение неявным вопросом к Крабу по контексту.

    Работает поверх существующей логики follow-up: если Краб недавно отвечал
    (в пределах 10 минут) — анализируем эвристики вопросительного текста.

    Args:
        text:      Текст сообщения (или caption).
        chat_id:   ID чата для проверки окна last_krab_msg.
        in_window: Переопределить проверку окна (True/False) — для тестов.
                   None (default) — использовать реальный last_krab_msg.

    Returns:
        True если сообщение является неявным вопросом к Крабу.
    """
    # ENV gate
    if not KRAB_IMPLICIT_QUESTION_DETECTION_ENABLED:
        return False

    stripped = (text or "").strip()
    if not stripped:
        return False

    low = stripped.lower()

    # Проверка вопросительных эвристик
    is_question = (
        # Заканчивается на "?"
        low.endswith("?")
        # Standalone короткие реплики
        or low in _IQ_STANDALONE
        # Начинается с одного из вопросительных префиксов
        or any(low.startswith(pfx) for pfx in _IQ_PREFIX_PATTERNS)
    )
    if not is_question:
        return False

    # Проверка временного окна после ответа Краба
    if in_window is None:
        # Реальная проверка через singleton
        window_ok = last_krab_msg.within_window(chat_id, window=_IMPLICIT_QUESTION_WINDOW_SEC)
    else:
        window_ok = in_window

    if not window_ok:
        return False

    return True


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

    # 1. Follow-up к недавнему ответу Краба (5 мин окно)
    if chat_id and not is_reply_to_explicit_msg and last_krab_msg.within_window(chat_id):
        return TriggerResult(TriggerType.FOLLOWUP_TO_KRAB, _FOLLOWUP_SCORE, "followup_window")

    # 1.5. Implicit question в расширенном окне 10 мин (Wave 26-B):
    # Проверяем ПОСЛЕ follow-up (5 мин), так как 5-10 мин попадает только сюда.
    # detect_implicit_question учитывает ENV gate и _IMPLICIT_QUESTION_WINDOW_SEC.
    if chat_id and not is_reply_to_explicit_msg and detect_implicit_question(text_s, chat_id):
        if _IMPLICIT_QUESTION_CTX_SCORE >= thresh:
            return TriggerResult(
                TriggerType.IMPLICIT_QUESTION,
                _IMPLICIT_QUESTION_CTX_SCORE,
                "implicit_question_ctx",
            )

    # 2.0. Wave 40-T: ironic Krab mentions ("ну где же Краб?", "куда пропал Краб")
    # — high score, чтобы пройти smart routing threshold даже без hard gate.
    for pat in _IRONIC_KRAB_PATTERNS:
        m = pat.search(text_s)
        if m:
            return TriggerResult(
                TriggerType.IMPLICIT_QUESTION,
                _IRONIC_KRAB_SCORE,
                f"ironic:{m.group(0)}",
            )

    # 2.1. Wave 40-T: compound mentions — Краб + @username в одном сообщении.
    cm = _COMPOUND_MENTION_PATTERN.search(text_s)
    if cm:
        return TriggerResult(
            TriggerType.IMPLICIT_QUESTION,
            _IRONIC_KRAB_SCORE,
            f"compound:{cm.group(0)[:60]}",
        )

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
      - "media_present"      — фото/видео/video_note/animation/sticker без caption
    """

    should_respond: bool
    decision_path: str
    confidence: float
    legacy_result: TriggerResult | None = None
    intent_result: "IntentResult | None" = None


# Bug 11 (Session 28): media-aware bumps — photo/video/video_note/animation/sticker
# в group chats без caption и mention silent дропались Stage 3 regex_low (text="").
# Bump поднимает confidence до floor, чтобы media хотя бы попадало в LLM stage.
_MEDIA_PRESENT_FLOOR = 0.55  # без caption — выше NORMAL threshold (0.5) → respond
_MEDIA_WITH_CAPTION_FLOOR = 0.4  # с caption — borderline → LLM решит


def _emit_smart_routing_metric(result: SmartTriggerResult) -> SmartTriggerResult:
    """Wave 73: emit krab_smart_routing_decisions_total для каждого SmartTriggerResult.

    Безопасно для hot path — record_smart_routing_decision fail-safe.
    Возвращает result без изменений (для inline-обёртки на return).
    """
    try:
        from .prometheus_metrics import map_smart_routing_path, record_smart_routing_decision

        stage, outcome = map_smart_routing_path(result.decision_path, result.should_respond)
        record_smart_routing_decision(stage, outcome)
    except Exception:  # noqa: BLE001 — observability не должна ломать routing
        pass
    return result


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
    has_media: bool = False,
    user_id: str | int | None = None,
) -> SmartTriggerResult:
    """5-stage smart routing pipeline (Session 26 Smart Routing).

    Stage 1: hard gates (always respond) — command/mention/reply-to-me.
    Stage 2: per-chat policy — SILENT → drop.
    Stage 3: regex fast filter — score>=0.6 → respond, score<0.2 → drop.
    Stage 4: LLM intent classifier для borderline (0.2-0.6).
    Stage 5: fallback на regex+threshold при отсутствии/ошибке LLM.

    has_media: True если message несёт photo/video/video_note/animation/sticker.
        Bug 11 fix (Session 28): media без caption ранее silent дропалось на
        Stage 3 (text="" → score 0.0 → regex_low). Теперь:
          - has_media=True + пустой/короткий text → confidence floor 0.55,
            decision_path="media_present" → respond (выше NORMAL threshold);
          - has_media=True + caption (есть текст) → floor 0.4 (borderline) →
            обычный pipeline (regex/LLM) с поднятым нижним порогом.
    """
    # Lazy import чтобы избежать circular import (chat_response_policy → нет;
    # llm_intent_classifier тоже не импортит нас).
    from .chat_response_policy import ChatMode

    # Stage 1: Hard gates
    if has_command or has_explicit_mention or is_reply_to_me:
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=True,
                decision_path="hard_gate",
                confidence=1.0,
            )
        )

    # Stage 2: Per-chat policy
    policy = policy_store.get_policy(chat_id)
    if policy.mode == ChatMode.SILENT:
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=False,
                decision_path="policy_silent",
                confidence=1.0,
            )
        )

    # Stage 3: Regex fast filter
    legacy = detect_implicit_mention(
        text,
        chat_id,
        is_reply_to_explicit_msg=False,
    )

    # Feature B: per-user threshold modifier из user_reaction_memory
    threshold = policy.effective_threshold()
    user_modifier = 0.0
    if user_id is not None:
        try:
            from .user_reaction_memory import get_store as _get_user_store

            user_modifier = _get_user_store().get_threshold_modifier(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "user_reaction_modifier_failed",
                user_id=str(user_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            user_modifier = 0.0
    if user_modifier:
        # Clamp в [0.0, 1.1] — 1.1 это эффективно «никогда» (как SILENT mode).
        threshold = max(0.0, min(1.1, threshold + user_modifier))

    # High confidence regex score → respond
    if legacy.score >= 0.6 and legacy.trigger_type != TriggerType.NONE:
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=True,
                decision_path="regex_high",
                confidence=legacy.score,
                legacy_result=legacy,
            )
        )

    # Bug 11 (Session 28): media-aware short-circuit для media без caption.
    # До fix: photo/video в группе без caption → text="" → regex_low → drop.
    # Логика:
    #   - media + пустой/короткий caption → floor 0.55, путь "media_present" →
    #     respond сразу (NORMAL threshold 0.5 < 0.55, CAUTIOUS 0.7 > 0.55 уважаем).
    #   - media + полноценный caption → floor 0.4, обычный pipeline (LLM/threshold).
    has_caption_text = bool(text and text.strip())
    if has_media and not has_caption_text:
        media_confidence = max(legacy.score, _MEDIA_PRESENT_FLOOR)
        # Уважаем per-chat threshold: CAUTIOUS (0.7) → media floor 0.55 не пройдёт.
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=(media_confidence >= threshold),
                decision_path="media_present",
                confidence=media_confidence,
                legacy_result=legacy,
            )
        )

    # Very low score → drop без LLM
    # Media + caption: поднимаем floor до 0.4, чтобы попасть в LLM stage,
    # а не упасть в regex_low.
    effective_low_score = legacy.score
    if has_media and has_caption_text:
        effective_low_score = max(effective_low_score, _MEDIA_WITH_CAPTION_FLOOR)

    if effective_low_score < 0.2:
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=False,
                decision_path="regex_low",
                confidence=legacy.score,
                legacy_result=legacy,
            )
        )

    # Stage 4: LLM intent (borderline 0.2-0.6)
    if llm_classifier is None:
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=(legacy.score >= threshold),
                decision_path="regex_threshold_fallback",
                confidence=legacy.score,
                legacy_result=legacy,
            )
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
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=(legacy.score >= threshold),
                decision_path="llm_error_fallback",
                confidence=legacy.score,
                legacy_result=legacy,
            )
        )

    if intent.error:
        return _emit_smart_routing_metric(
            SmartTriggerResult(
                should_respond=(legacy.score >= threshold),
                decision_path="llm_error_fallback",
                confidence=legacy.score,
                legacy_result=legacy,
                intent_result=intent,
            )
        )

    final_decision = intent.should_respond and intent.confidence >= threshold
    return _emit_smart_routing_metric(
        SmartTriggerResult(
            should_respond=final_decision,
            decision_path=f"llm_{'yes' if intent.should_respond else 'no'}",
            confidence=intent.confidence,
            legacy_result=legacy,
            intent_result=intent,
        )
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
