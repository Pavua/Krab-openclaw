# -*- coding: utf-8 -*-
"""
proactive_dispatcher.py — Wave 39-B-1: ядро проактивного детектора событий.

Feature gate: KRAB_PROACTIVE_ENABLED=0 (default) → полный noop.
Когда включено + чат opted-in — Krab проактивно реагирует на события без
явного упоминания (join / media без caption / AI-alias вопросы).

7 gate-ов в порядке вычисления:
  1. Global gate      — KRAB_PROACTIVE_ENABLED env
  2. Existing trigger — если уже сработал обычный триггер
  3. Detect event     — join / media / ai_alias / none
  4. Chat policy gate — SILENT mode → skip
  5. Per-chat opt-in  — policy.proactive_joins/media/ai_alias
  6. Quota gate       — дневной лимит (joins=1, media=5, ai_alias=3)
  7. Burst gate       — cooldown 5 мин между proactive в одном чате
  8. Backoff gate     — 3+ dismiss реакций за 24h → skip

Счётчики хранятся in-memory (FIFO dict per chat, daily reset в midnight UTC).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Дневные квоты по типу события
_QUOTA_JOINS_DAY = 1
_QUOTA_MEDIA_DAY = 5
_QUOTA_AI_ALIAS_DAY = 3

# Burst cooldown между любыми proactive в одном чате (секунды)
_BURST_COOLDOWN_SEC = 5 * 60

# Порог dismiss-реакций для backoff
_DISMISS_BACKOFF_THRESHOLD = 3

# ---------------------------------------------------------------------------
# AI-alias паттерны (расширены относительно trigger_detector._GENERIC_AI_PATTERN)
# Намеренно ищем вопросительные паттерны / глагольные оценки "кто шарит".
# ---------------------------------------------------------------------------

_AI_ALIAS_PATTERNS = [
    # "кто-нибудь шарит / разбирается / подскажет"
    re.compile(
        r"\bкто[-\s]*нибудь\s+(?:шарит|разбирается|разберётся|подскажет|объяснит|поможет)\b",
        re.IGNORECASE,
    ),
    # "может бот / ии / нейронка ответить / подсказать / помочь"
    re.compile(
        r"\b(?:может|умеет)\s+(?:бот|ии|ai|нейронка|нейросеть|ассистент)\s+(?:ответить|подсказать|помочь|объяснить|разобраться)\b",
        re.IGNORECASE,
    ),
    # "может ии подскажет / ответит" — инвертированный порядок
    re.compile(
        r"\b(?:может)\s+(?:ии|бот|нейронка|ai)\s+(?:подскажет|ответит|поможет|объяснит)\b",
        re.IGNORECASE,
    ),
    # "ии может?" — короткая форма
    re.compile(
        r"\bии\s+может\b",
        re.IGNORECASE,
    ),
    # "боту/боты/бота шарит / знают / поможет"
    re.compile(
        r"\bбот[ауы]?\s+(?:знают|шарит|поможет|ответит|подскажет)\b",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# ProactiveDecision
# ---------------------------------------------------------------------------


@dataclass
class ProactiveDecision:
    """Результат dispatch: нужно ли реагировать + метаданные."""

    should_respond: bool
    event_type: str  # "join" / "media" / "ai_alias" / "none"
    reason: str  # "global_disabled" / "chat_opt_out" / ... / "match_join" / etc.
    suggested_prompt_hint: str = ""  # hint для LLM context при формировании ответа


# ---------------------------------------------------------------------------
# Внутреннее состояние per-chat
# ---------------------------------------------------------------------------


@dataclass
class _ChatCounters:
    """Счётчики квоты и cooldown-состояние для одного чата."""

    joins_today: int = 0
    media_today: int = 0
    ai_alias_today: int = 0
    last_reset_utc: float = field(default_factory=time.time)
    last_response_ts: float = 0.0  # timestamp последнего proactive-ответа
    consecutive_dismisses: int = 0  # override dismiss-счётчик из внешнего tracker


# ---------------------------------------------------------------------------
# ProactiveDispatcher
# ---------------------------------------------------------------------------


class ProactiveDispatcher:
    """Wave 39-B: tiered gates + quota enforcement для проактивных реакций.

    policy_store — объект с методом get_policy(chat_id) → ChatResponsePolicy-like.
    feedback_tracker — объект с методом get_consecutive_dismisses(chat_id) → int.
    """

    def __init__(
        self,
        *,
        policy_store: Any,
        feedback_tracker: Any,
    ) -> None:
        self._policy_store = policy_store
        self._feedback_tracker = feedback_tracker
        # {str(chat_id): _ChatCounters}
        self._counters: dict[str, _ChatCounters] = {}

    # ------------------------------------------------------------------ #
    # Публичный API                                                        #
    # ------------------------------------------------------------------ #

    def dispatch_sync(
        self,
        message: Any,
        *,
        chat_id: str | int,
        existing_trigger_decision_was_none: bool,
    ) -> ProactiveDecision:
        """Синхронная версия dispatch — применяет 8 gate-ов."""
        chat_key = str(chat_id)

        # Gate 1: глобальный feature-flag
        if not _proactive_enabled():
            return _no("none", "global_disabled")

        # Gate 2: существующий триггер уже сработал
        if not existing_trigger_decision_was_none:
            return _no("none", "existing_trigger")

        # Gate 3: определяем тип события
        event_type = self._detect_event(message)
        if event_type == "none":
            return _no("none", "no_event_detected")

        # Gate 4: SILENT mode → пропуск
        policy = self._policy_store.get_policy(chat_key)
        if getattr(policy, "mode", "normal") == "silent":
            return _no(event_type, "silent_mode")

        # Gate 5: per-chat opt-in
        opt_out_reason = self._check_opt_in(policy, event_type)
        if opt_out_reason:
            return _no(event_type, opt_out_reason)

        # Инициализируем/сбрасываем счётчики
        counters = self._get_counters(chat_key)

        # Gate 6: дневная квота
        if self._quota_exhausted(counters, event_type):
            return _no(event_type, "quota_exhausted")

        # Gate 7: burst cooldown (5 мин)
        if counters.last_response_ts > 0:
            elapsed = time.time() - counters.last_response_ts
            if elapsed < _BURST_COOLDOWN_SEC:
                return _no(event_type, "burst_cooldown")

        # Gate 8: dismiss backoff
        dismisses = self._get_dismisses(chat_key, counters)
        if dismisses >= _DISMISS_BACKOFF_THRESHOLD:
            return _no(event_type, "dismiss_backoff")

        # Все gate-ы пройдены
        hint = self._build_hint(message, event_type)
        return ProactiveDecision(
            should_respond=True,
            event_type=event_type,
            reason=f"match_{event_type}",
            suggested_prompt_hint=hint,
        )

    def record_response(
        self,
        chat_id: str | int,
        event_type: str,
        ts: float | None = None,
    ) -> None:
        """Записываем успешный proactive-ответ — инкремент счётчика + cooldown."""
        chat_key = str(chat_id)
        counters = self._get_counters(chat_key)
        now = ts if ts is not None else time.time()
        counters.last_response_ts = now
        if event_type == "join":
            counters.joins_today += 1
        elif event_type == "media":
            counters.media_today += 1
        elif event_type == "ai_alias":
            counters.ai_alias_today += 1

    def record_dismiss_reaction(
        self,
        chat_id: str | int,
        ts: float | None = None,  # noqa: ARG002 — зарезервировано для будущего expire
    ) -> None:
        """User отреагировал негативно — инкрементируем внутренний dismiss-счётчик."""
        chat_key = str(chat_id)
        counters = self._get_counters(chat_key)
        counters.consecutive_dismisses += 1

    def get_chat_stats(self, chat_id: str | int) -> dict[str, Any]:
        """Возвращает статистику счётчиков для чата (удобно для тестов/отладки)."""
        chat_key = str(chat_id)
        c = self._get_counters(chat_key)
        return {
            "joins_today": c.joins_today,
            "media_today": c.media_today,
            "ai_alias_today": c.ai_alias_today,
            "last_response_ts": c.last_response_ts,
            "consecutive_dismisses": c.consecutive_dismisses,
        }

    def _set_last_reset_for_test(self, chat_id: str | int, ts: float) -> None:
        """Хук для тестов: принудительно ставим last_reset_utc в прошлое."""
        chat_key = str(chat_id)
        counters = self._get_counters(chat_key)
        counters.last_reset_utc = ts

    # ------------------------------------------------------------------ #
    # Внутренние методы                                                    #
    # ------------------------------------------------------------------ #

    def _get_counters(self, chat_key: str) -> _ChatCounters:
        """Получить / создать счётчики для чата, автоматически сбросив при смене дня."""
        if chat_key not in self._counters:
            self._counters[chat_key] = _ChatCounters()
        counters = self._counters[chat_key]
        # Сброс при смене UTC-дня
        if _is_new_day(counters.last_reset_utc):
            counters.joins_today = 0
            counters.media_today = 0
            counters.ai_alias_today = 0
            counters.consecutive_dismisses = 0
            counters.last_reset_utc = time.time()
        return counters

    def _detect_event(self, message: Any) -> str:
        """Определяем тип события: join > media > ai_alias > none."""
        # Join: новый участник вступил
        if _detect_join(message):
            return "join"
        # Media без caption
        if _detect_media_without_caption(message):
            return "media"
        # AI alias в тексте
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        if text and _detect_ai_alias(str(text)):
            return "ai_alias"
        return "none"

    def _check_opt_in(self, policy: Any, event_type: str) -> str | None:
        """Проверяем per-chat opt-in. Возвращает reason если надо skip, иначе None."""
        if event_type == "join" and not getattr(policy, "proactive_joins", True):
            return "opt_out_join"
        if event_type == "media" and not getattr(policy, "proactive_media", True):
            return "opt_out_media"
        if event_type == "ai_alias" and not getattr(policy, "proactive_ai", True):
            return "opt_out_ai_alias"
        return None

    def _quota_exhausted(self, counters: _ChatCounters, event_type: str) -> bool:
        """True если дневной лимит по данному типу события исчерпан."""
        if event_type == "join":
            return counters.joins_today >= _QUOTA_JOINS_DAY
        if event_type == "media":
            return counters.media_today >= _QUOTA_MEDIA_DAY
        if event_type == "ai_alias":
            return counters.ai_alias_today >= _QUOTA_AI_ALIAS_DAY
        return False

    def _get_dismisses(self, chat_key: str, counters: _ChatCounters) -> int:
        """Получаем dismiss-счётчик: приоритет у внутреннего (record_dismiss_reaction),
        иначе — из feedback_tracker.
        """
        if counters.consecutive_dismisses > 0:
            return counters.consecutive_dismisses
        try:
            return self._feedback_tracker.get_consecutive_dismisses(chat_key)
        except Exception:  # noqa: BLE001 — tracker не критичный path
            return 0

    def _build_hint(self, message: Any, event_type: str) -> str:
        """Формируем подсказку для LLM о контексте события."""
        if event_type == "join":
            members = getattr(message, "new_chat_members", None) or []
            names = []
            for m in members:
                uname = getattr(m, "username", None)
                fname = getattr(m, "first_name", None)
                names.append(f"@{uname}" if uname else (fname or "пользователь"))
            joined = ", ".join(names) if names else "новый пользователь"
            return f"Новый участник {joined} вступил в чат"
        if event_type == "media":
            return "В чат отправлено медиа без подписи"
        if event_type == "ai_alias":
            text = getattr(message, "text", "") or ""
            preview = text[:80] + ("…" if len(text) > 80 else "")
            return f"Вопрос с AI-упоминанием: {preview}"
        return ""


# ---------------------------------------------------------------------------
# Утилиты (модульные функции — проще тестировать отдельно)
# ---------------------------------------------------------------------------


def _proactive_enabled() -> bool:
    """Читаем env каждый раз — позволяет менять флаг без рестарта."""
    return os.environ.get("KRAB_PROACTIVE_ENABLED", "0").strip() == "1"


def _detect_join(message: Any) -> bool:
    """True если сообщение сигнализирует о новом участнике чата."""
    members = getattr(message, "new_chat_members", None)
    return bool(members)


def _detect_media_without_caption(message: Any) -> bool:
    """True если сообщение содержит медиа (фото/видео/голос/стикер) без caption."""
    has_media = any(
        getattr(message, attr, None) is not None for attr in ("photo", "video", "voice", "sticker")
    )
    if not has_media:
        return False
    caption = getattr(message, "caption", None)
    # Пустая строка тоже считается "без caption"
    return not caption


def _detect_ai_alias(text: str) -> bool:
    """True если текст содержит вопросительный паттерн с AI-алиасом.

    Намеренно не матчим информационные фразы вроде
    "у меня есть бот для напоминалок" — там нет глагольного вопроса.
    """
    for pattern in _AI_ALIAS_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_new_day(last_reset_ts: float) -> bool:
    """True если дата last_reset и текущая дата различаются (UTC)."""
    last_dt = datetime.fromtimestamp(last_reset_ts, tz=timezone.utc)
    now_dt = datetime.now(tz=timezone.utc)
    return last_dt.date() < now_dt.date()


def _no(event_type: str, reason: str) -> ProactiveDecision:
    """Создаёт решение «не реагировать»."""
    return ProactiveDecision(
        should_respond=False,
        event_type=event_type,
        reason=reason,
        suggested_prompt_hint="",
    )
