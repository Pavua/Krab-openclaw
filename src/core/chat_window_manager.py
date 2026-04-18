"""
Per-chat ChatWindow with LRU eviction.

Architecture (Chado-inspired):
- Each chat has dedicated ChatWindow (in-memory context + asyncio state)
- Active chats держат state
- LRU capacity ~50 chats (env CHATWINDOW_CAPACITY)
- Evicted windows persist metadata (last_seen) — на return re-hydrate from archive.db
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from structlog import get_logger

logger = get_logger(__name__)

DEFAULT_CAPACITY = int(os.environ.get("CHATWINDOW_CAPACITY", "50"))
DEFAULT_IDLE_TIMEOUT_SEC = int(os.environ.get("CHATWINDOW_IDLE_TIMEOUT", "3600"))  # 1h


@dataclass
class ChatWindow:
    """Per-chat context window."""

    chat_id: str
    messages: list[dict] = field(default_factory=list)  # Буфер последних сообщений
    last_activity_at: float = field(default_factory=time.time)
    message_count: int = 0
    mode: str = "active"  # "active" | "mention-only" | "muted"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self):
        """Пометить окно как недавно активное."""
        self.last_activity_at = time.time()
        self.message_count += 1

    def is_idle(self, timeout_sec: int = DEFAULT_IDLE_TIMEOUT_SEC) -> bool:
        """Вернуть True если окно не активно дольше timeout_sec."""
        return (time.time() - self.last_activity_at) > timeout_sec

    def append_message(self, role: str, content: str):
        """Добавить сообщение в буфер; cap at 20, старое уходит в archive."""
        self.messages.append({"role": role, "content": content, "ts": time.time()})
        # Держим не более 20 последних сообщений в памяти
        if len(self.messages) > 20:
            self.messages = self.messages[-20:]
        self.touch()


class ChatWindowManager:
    """LRU manager для per-chat windows."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._windows: OrderedDict[str, ChatWindow] = OrderedDict()
        self._capacity = capacity
        self._evictions_total = 0

    def get_or_create(self, chat_id: str) -> ChatWindow:
        """Вернуть существующее окно или создать новое. Обновляет LRU-порядок."""
        chat_id = str(chat_id)
        if chat_id in self._windows:
            # Переместить в конец (most recently used)
            self._windows.move_to_end(chat_id)
            return self._windows[chat_id]

        # Evict LRU если достигнут capacity
        while len(self._windows) >= self._capacity:
            evicted_id, evicted_window = self._windows.popitem(last=False)
            self._evictions_total += 1
            logger.info(
                "chat_window_evicted",
                chat_id=evicted_id,
                message_count=evicted_window.message_count,
                idle_sec=int(time.time() - evicted_window.last_activity_at),
            )

        window = ChatWindow(chat_id=chat_id)
        self._windows[chat_id] = window
        logger.debug("chat_window_created", chat_id=chat_id)
        return window

    def peek(self, chat_id: str) -> Optional[ChatWindow]:
        """Получить окно без обновления LRU-порядка."""
        return self._windows.get(str(chat_id))

    def remove(self, chat_id: str) -> bool:
        """Явное удаление окна (например, !reset --all)."""
        return self._windows.pop(str(chat_id), None) is not None

    def evict_idle(self, timeout_sec: int = DEFAULT_IDLE_TIMEOUT_SEC) -> int:
        """Вытеснить все окна, простаивающие дольше timeout_sec. Возвращает количество."""
        to_evict = [
            chat_id
            for chat_id, w in self._windows.items()
            if w.is_idle(timeout_sec)
        ]
        for chat_id in to_evict:
            self._windows.pop(chat_id, None)
            self._evictions_total += 1
        if to_evict:
            logger.info("chat_windows_idle_evicted", count=len(to_evict))
        return len(to_evict)

    def stats(self) -> dict[str, Any]:
        """Вернуть сводку состояния менеджера."""
        return {
            "active_windows": len(self._windows),
            "capacity": self._capacity,
            "evictions_total": self._evictions_total,
            "top_active": [
                {
                    "chat_id": w.chat_id,
                    "messages": w.message_count,
                    "idle_sec": int(time.time() - w.last_activity_at),
                    "mode": w.mode,
                }
                for w in sorted(
                    self._windows.values(), key=lambda x: -x.message_count
                )[:10]
            ],
        }

    def set_mode(self, chat_id: str, mode: str) -> None:
        """Установить режим чата: active | mention-only | muted."""
        if mode not in ("active", "mention-only", "muted"):
            raise ValueError(f"invalid mode: {mode!r}")
        window = self.get_or_create(chat_id)
        window.mode = mode

    def list_chats(self, mode: Optional[str] = None) -> list[str]:
        """Вернуть список chat_id, опционально отфильтрованных по mode."""
        if mode:
            return [cid for cid, w in self._windows.items() if w.mode == mode]
        return list(self._windows.keys())


# Singleton — используется из userbot и web_app
chat_window_manager = ChatWindowManager()

# TODO Session 13.X: integrate в userbot_bridge._process_message для replace
# current session tracking. Каждый входящий message должен вызывать
# chat_window_manager.get_or_create(chat_id).append_message(role, content)
# и lock per-window перед обращением к LLM.
