"""Feedback Tracker — negative learning из реакций оператора.

Smart Routing Phase 3 (Component 3 в `docs/SMART_ROUTING_DESIGN.md`).

Сценарии:
- Owner удалил Krab-сообщение в течение 30 мин → negative_signal в policy_store.
- Owner поставил 👎 / 🤡 / 💩 на Krab-сообщение → negative_signal.
- Owner поставил 👍 / ❤️ / 🔥 на Krab-сообщение → positive_signal.

Wiring (Pyrogram event hooks: MessageDeleted, ReactionAdded) выполняется в Phase 5
в `userbot_bridge.py`. Phase 3 — только tracker logic + API + tests.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog

from .chat_response_policy import ChatResponsePolicyStore
from .chat_response_policy import get_store as get_policy_store
from .user_reaction_memory import UserReactionStore
from .user_reaction_memory import get_store as get_user_reaction_store

logger = structlog.get_logger(__name__)


def _write_response_feedback(
    chat_id: str | int,
    message_id: int,
    *,
    positive_delta: int = 0,
    negative_delta: int = 0,
) -> None:
    """Зеркалит positive/negative сигнал в archive.db response_feedback (Feature A).

    Best-effort: любая ошибка → debug-лог, исключений не пробрасываем
    (feedback tracker не critical path). Импорты внутри функции, чтобы
    не тянуть sqlite3/archive paths при инициализации модуля.
    """
    if positive_delta == 0 and negative_delta == 0:
        return
    try:
        from .memory_archive import (
            ArchivePaths,
            ensure_response_feedback_table,
            open_archive,
            record_response_feedback,
        )

        paths = ArchivePaths.default()
        if not paths.db.exists():
            return
        conn = open_archive(paths=paths, create_if_missing=False)
        try:
            ensure_response_feedback_table(conn)
            record_response_feedback(
                conn,
                str(chat_id),
                str(message_id),
                positive_delta=positive_delta,
                negative_delta=negative_delta,
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - feedback не должен ронять userbot
        logger.debug(
            "response_feedback_write_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            chat_id=str(chat_id),
            message_id=message_id,
        )


# Реакция/delete считается feedback'ом если within 30 min after Krab response
FEEDBACK_WINDOW_SEC = 30 * 60
RECENT_RESPONSES_MAX = 1000  # LRU cap

# Классификация эмодзи реакций
NEGATIVE_REACTIONS = frozenset({"👎", "🤡", "💩", "🖕", "😤", "🤬", "🥱"})
POSITIVE_REACTIONS = frozenset({"👍", "❤️", "🔥", "🎉", "👏", "💯", "🙏", "😊", "🥰"})


@dataclass
class KrabResponse:
    """Метаданные о Krab-ответе для последующего feedback tracking.

    target_user_id (Feature B): user_id автора сообщения, на которое Краб
    отвечал. Используется для per-user reaction memory: реакция/удаление
    Krab-ответа атрибутируется этому пользователю (его контекст спровоцировал
    Краба → если ответ сочли плохим, это сигнал по нему).
    """

    chat_id: str
    message_id: int
    sent_at: float
    decision_path: str  # "hard_gate" | "regex_high" | "llm_yes" | etc.
    confidence: float = 1.0
    target_user_id: str | None = None


class FeedbackTracker:
    """Tracker реакций оператора на ответы Краба для negative learning.

    Использование:
      1. `record_krab_response(KrabResponse)` — caller (userbot_bridge)
         вызывает после каждого успешно отправленного Krab-сообщения.
      2. `on_message_deleted(chat_id, message_id, deleted_by)` — Pyrogram hook.
      3. `on_reaction_added(chat_id, message_id, reaction, user_id)` — Pyrogram hook.
    """

    def __init__(
        self,
        *,
        policy_store: ChatResponsePolicyStore | None = None,
        owner_user_id: int | None = None,
        user_reaction_store: UserReactionStore | None = None,
    ) -> None:
        self._recent: OrderedDict[tuple[str, int], KrabResponse] = OrderedDict()
        self._lock = threading.RLock()
        self._policy_store = policy_store or get_policy_store()
        self._owner_user_id = owner_user_id
        # Per-user reaction memory (Feature B). Опциональный — может быть
        # подменён в тестах; в проде — singleton store.
        self._user_reaction_store = user_reaction_store or get_user_reaction_store()

    def set_owner_id(self, owner_user_id: int) -> None:
        """Lazy setter — owner_id известен только после Pyrogram start()."""
        with self._lock:
            self._owner_user_id = owner_user_id

    def record_krab_response(self, response: KrabResponse) -> None:
        """Зарегистрировать факт отправки Krab-сообщения."""
        key = (str(response.chat_id), int(response.message_id))
        with self._lock:
            self._recent[key] = response
            self._recent.move_to_end(key)
            self._evict_old_locked()

    def _evict_old_locked(self) -> None:
        """Eviction: by age (>WINDOW_SEC) + by count (>MAX). Caller holds lock."""
        now = time.time()
        # Age-based: сначала старые
        while self._recent:
            oldest_key = next(iter(self._recent))
            if now - self._recent[oldest_key].sent_at > FEEDBACK_WINDOW_SEC:
                del self._recent[oldest_key]
            else:
                break
        # Count-based
        while len(self._recent) > RECENT_RESPONSES_MAX:
            self._recent.popitem(last=False)

    async def on_message_deleted(
        self,
        chat_id: str | int,
        message_id: int,
        deleted_by: int | None,
    ) -> bool:
        """Возвращает True если signal записан."""
        if self._owner_user_id is None:
            return False
        # deleted_by может быть None (Pyrogram не всегда отдаёт автора удаления);
        # если задан — должен совпадать с owner_id
        if deleted_by is not None and deleted_by != self._owner_user_id:
            return False
        key = (str(chat_id), int(message_id))
        with self._lock:
            response = self._recent.pop(key, None)
        if response is None:
            return False  # это не отслеживаемое нами Krab-сообщение
        if time.time() - response.sent_at > FEEDBACK_WINDOW_SEC:
            return False  # слишком старое — не считаем feedback
        self._policy_store.record_negative_signal(
            chat_id=str(chat_id),
            reason=f"owner_deleted_krab_reply (decision_path={response.decision_path})",
        )
        # Feature B: per-user reaction memory
        if response.target_user_id:
            try:
                self._user_reaction_store.record_negative(response.target_user_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "feedback_user_reaction_record_failed",
                    user_id=response.target_user_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        # Feature A: per-message archive feedback
        _write_response_feedback(chat_id, message_id, negative_delta=1)
        logger.info(
            "feedback_negative_delete",
            chat_id=str(chat_id),
            message_id=message_id,
            decision_path=response.decision_path,
            target_user_id=response.target_user_id,
        )
        return True

    async def on_reaction_added(
        self,
        chat_id: str | int,
        message_id: int,
        reaction: str,
        user_id: int,
    ) -> bool:
        """Возвращает True если signal записан (negative или positive)."""
        if self._owner_user_id is None or user_id != self._owner_user_id:
            return False
        key = (str(chat_id), int(message_id))
        with self._lock:
            response = self._recent.get(key)
        if response is None:
            return False
        if time.time() - response.sent_at > FEEDBACK_WINDOW_SEC:
            return False
        if reaction in NEGATIVE_REACTIONS:
            self._policy_store.record_negative_signal(
                chat_id=str(chat_id),
                reason=f"owner_reaction_negative_{reaction}",
            )
            # Feature B: per-user reaction memory
            if response.target_user_id:
                try:
                    self._user_reaction_store.record_negative(response.target_user_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "feedback_user_reaction_record_failed",
                        user_id=response.target_user_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
            # Feature A: per-message archive feedback
            _write_response_feedback(chat_id, message_id, negative_delta=1)
            logger.info(
                "feedback_negative_reaction",
                chat_id=str(chat_id),
                message_id=message_id,
                reaction=reaction,
                target_user_id=response.target_user_id,
            )
            return True
        if reaction in POSITIVE_REACTIONS:
            self._policy_store.record_positive_signal(
                chat_id=str(chat_id),
                reason=f"owner_reaction_positive_{reaction}",
            )
            # Feature B: per-user reaction memory
            if response.target_user_id:
                try:
                    self._user_reaction_store.record_positive(response.target_user_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "feedback_user_reaction_record_failed",
                        user_id=response.target_user_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
            # Feature A: per-message archive feedback
            _write_response_feedback(chat_id, message_id, positive_delta=1)
            logger.info(
                "feedback_positive_reaction",
                chat_id=str(chat_id),
                message_id=message_id,
                reaction=reaction,
                target_user_id=response.target_user_id,
            )
            return True
        return False  # neutral / unknown reaction

    def stats(self) -> dict:
        """Diagnostic snapshot."""
        with self._lock:
            return {
                "tracked_responses": len(self._recent),
                "owner_id_set": self._owner_user_id is not None,
                "negative_reactions": sorted(NEGATIVE_REACTIONS),
                "positive_reactions": sorted(POSITIVE_REACTIONS),
                "feedback_window_sec": FEEDBACK_WINDOW_SEC,
            }


# Singleton
_default_tracker: FeedbackTracker | None = None


def get_tracker() -> FeedbackTracker:
    global _default_tracker
    if _default_tracker is None:
        _default_tracker = FeedbackTracker()
    return _default_tracker


def reset_tracker_for_tests() -> None:
    """Test-only helper для сброса singleton между тестами."""
    global _default_tracker
    _default_tracker = None
