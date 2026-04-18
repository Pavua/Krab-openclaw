"""
ChatWindow manager с поддержкой env-конфигурации.

Env-переменные:
  CHAT_WINDOW_CAPACITY — макс кол-во окон в памяти (default: 100)
  CHAT_WINDOW_MESSAGE_CAP — макс сообщений в одном окне (default: 20)
  CHAT_WINDOW_IDLE_SEC — timeout для evict idle окон в сек (default: 3600)
"""

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Env-конфигурация (с defaults)
CAPACITY = int(os.environ.get("CHAT_WINDOW_CAPACITY", "100"))
MESSAGE_CAP_PER_WINDOW = int(os.environ.get("CHAT_WINDOW_MESSAGE_CAP", "20"))
IDLE_EVICTION_SEC = int(os.environ.get("CHAT_WINDOW_IDLE_SEC", "3600"))


@dataclass
class Message:
    """Одно сообщение в окне."""
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ChatWindow:
    """Окно контекста для одного чата."""
    chat_id: str
    messages: List[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)

    def append_message(self, role: str, content: str) -> None:
        """Добавить сообщение, обрезать до MESSAGE_CAP_PER_WINDOW."""
        self.messages.append(Message(role=role, content=content))
        self.last_activity_at = time.time()

        # Обрезаем до размера
        if len(self.messages) > MESSAGE_CAP_PER_WINDOW:
            self.messages = self.messages[-MESSAGE_CAP_PER_WINDOW:]

    def to_dict(self) -> dict:
        """Сериализация для API."""
        return {
            "chat_id": self.chat_id,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            "age_sec": time.time() - self.created_at,
            "idle_sec": time.time() - self.last_activity_at,
        }


class ChatWindowManager:
    """Менеджер контекстных окон чатов."""

    def __init__(self, capacity: Optional[int] = None):
        self._capacity = capacity or CAPACITY
        self._windows: Dict[str, ChatWindow] = {}

    def get_or_create(self, chat_id: str) -> ChatWindow:
        """Получить или создать окно."""
        if chat_id not in self._windows:
            # Если переполнен, выгонить самое старое
            if len(self._windows) >= self._capacity:
                oldest_id = min(
                    self._windows.keys(),
                    key=lambda cid: self._windows[cid].last_activity_at,
                )
                del self._windows[oldest_id]

            self._windows[chat_id] = ChatWindow(chat_id=chat_id)

        return self._windows[chat_id]

    def peek(self, chat_id: str) -> Optional[ChatWindow]:
        """Получить окно без создания."""
        return self._windows.get(chat_id)

    def evict_idle(self, timeout_sec: Optional[int] = None) -> int:
        """Выгнать окна, которые не активны дольше timeout_sec."""
        timeout = timeout_sec or IDLE_EVICTION_SEC
        now = time.time()
        to_remove = [
            cid for cid, win in self._windows.items()
            if now - win.last_activity_at > timeout
        ]

        for cid in to_remove:
            del self._windows[cid]

        return len(to_remove)

    def list_windows(self) -> List[dict]:
        """Список всех окон с метаданными."""
        return [win.to_dict() for win in self._windows.values()]

    def clear_all(self) -> int:
        """Очистить все окна."""
        count = len(self._windows)
        self._windows.clear()
        return count


# Глобальный синглтон
chat_window_manager = ChatWindowManager()
