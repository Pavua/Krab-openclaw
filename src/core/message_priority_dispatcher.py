"""
Message Priority Dispatcher — классификация и маршрутизация по приоритету.

Chado blueprint: каждое входящее сообщение получает приоритет
(CRITICAL > HIGH > NORMAL > LOW) для logging/metrics.
Реальный dispatch (отдельные очереди) — Phase 2; сейчас только classify.

Priority rules:
  CRITICAL — owner DM с командой или упоминанием
  HIGH     — DM от owner / reply на self / явный mention
  NORMAL   — обычный DM / group command
  LOW      — group non-command без mention (monitor-only)
"""
from __future__ import annotations

import threading
from enum import IntEnum
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


# ---------------------------------------------------------------------------
# classify_priority — pure function, no side-effects
# ---------------------------------------------------------------------------

def classify_priority(
    *,
    message_text: str,
    chat_type: str,               # "ChatType.PRIVATE" / "ChatType.GROUP" etc.
    is_dm: bool,
    is_reply_to_self: bool,
    has_mention: bool,
    chat_mode: str = "active",    # "active" | "mention-only" | "muted"
    is_command: Optional[bool] = None,
) -> tuple[Priority, str]:
    """
    Классифицировать приоритет сообщения.

    Возвращает (Priority, reason_str).
    """
    text = message_text or ""
    _is_command = (
        is_command
        if is_command is not None
        else (text[:1] in ("!", "/", ".") if text else False)
    )

    # CRITICAL: DM + command (owner interaction)
    if is_dm and _is_command:
        return Priority.CRITICAL, "dm_command"

    # HIGH: DM (any), explicit mention, reply to self
    if is_dm:
        return Priority.HIGH, "dm_message"

    if has_mention:
        return Priority.HIGH, "has_mention"

    if is_reply_to_self:
        return Priority.HIGH, "reply_to_self"

    # NORMAL: group command (always processed regardless of filter)
    if _is_command:
        return Priority.NORMAL, "group_command"

    # LOW: group без mention (monitor-only или pass-through в active mode)
    return Priority.LOW, "group_no_mention"


# ---------------------------------------------------------------------------
# PriorityDispatcher — background worker (Phase 2 placeholder)
# ---------------------------------------------------------------------------

class PriorityDispatcher:
    """
    Priority dispatcher singleton.

    Session 13.X: только start/stop + classify. Real queues — Phase 2.
    """

    def __init__(self) -> None:
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        logger.info("priority_dispatcher_started")

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        logger.info("priority_dispatcher_stopped")

    @property
    def is_started(self) -> bool:
        return self._started

    def classify(
        self,
        *,
        message_text: str,
        chat_type: str,
        is_dm: bool,
        is_reply_to_self: bool,
        has_mention: bool,
        chat_mode: str = "active",
    ) -> tuple[Priority, str]:
        """Делегирует к classify_priority."""
        return classify_priority(
            message_text=message_text,
            chat_type=chat_type,
            is_dm=is_dm,
            is_reply_to_self=is_reply_to_self,
            has_mention=has_mention,
            chat_mode=chat_mode,
        )

    def stats(self) -> dict:
        return {"started": self._started}


# Singleton
priority_dispatcher = PriorityDispatcher()
