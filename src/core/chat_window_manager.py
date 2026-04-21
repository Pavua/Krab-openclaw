# -*- coding: utf-8 -*-
"""
ChatWindowManager — скользящее окно активности чата (Chado Wave 16).

Отслеживает последние сообщения и статистику по каждому чату.
LRU-eviction при превышении capacity.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

# Константы с поддержкой env-override
CAPACITY: int = int(os.environ.get("CHAT_WINDOW_CAPACITY", "100"))
MESSAGE_CAP_PER_WINDOW: int = int(os.environ.get("CHAT_WINDOW_MESSAGE_CAP", "50"))
IDLE_EVICTION_SEC: int = int(os.environ.get("CHAT_WINDOW_IDLE_SEC", "3600"))


@dataclass
class ChatMessage:
    """Одно сообщение в окне."""

    role: str
    content: str
    ts: float


class ChatWindow:
    """Скользящее окно сообщений одного чата."""

    def __init__(self, chat_id: str, max_messages: int | None = None) -> None:
        self.chat_id = chat_id
        self._max_messages = max_messages if max_messages is not None else MESSAGE_CAP_PER_WINDOW
        self._messages: list[ChatMessage] = []
        self._created_at: float = time.time()
        self.last_activity_at: float = time.time()

    def touch(self) -> None:
        """Обновить метку активности."""
        self.last_activity_at = time.time()

    def append_message(self, role: str, content: str) -> None:
        """Добавить сообщение в окно; evict старые при переполнении."""
        self._messages.append(ChatMessage(role=role, content=content, ts=time.time()))
        if len(self._messages) > self._max_messages:
            self._messages.pop(0)
        self.touch()

    @property
    def messages(self) -> list[ChatMessage]:
        """Копия буфера сообщений."""
        return list(self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def snapshot(self) -> list[dict[str, Any]]:
        """Копия буфера как список dict."""
        return [{"role": m.role, "content": m.content, "ts": m.ts} for m in self._messages]

    def to_dict(self) -> dict[str, Any]:
        """Сериализация в dict для API/отчётов."""
        now = time.time()
        return {
            "chat_id": self.chat_id,
            "message_count": self.message_count,
            "created_at": self._created_at,
            "idle_sec": now - self.last_activity_at,
        }


class ChatWindowManager:
    """Менеджер окон активности по chat_id с LRU-eviction."""

    def __init__(
        self, capacity: int | None = None, max_messages_per_window: int | None = None
    ) -> None:
        self._capacity = capacity if capacity is not None else CAPACITY
        self._max_messages = (
            max_messages_per_window
            if max_messages_per_window is not None
            else MESSAGE_CAP_PER_WINDOW
        )
        self._windows: OrderedDict[str, ChatWindow] = OrderedDict()
        self._evicted_counts: dict[str, int] = {"lru": 0, "idle": 0}

    def get_or_create(self, chat_id: str) -> ChatWindow:
        """Вернуть существующее окно или создать новое (LRU-evict если нужно)."""
        if chat_id in self._windows:
            self._windows.move_to_end(chat_id)
            return self._windows[chat_id]

        # Evict LRU если capacity превышен
        if len(self._windows) >= self._capacity:
            self._windows.popitem(last=False)
            self._evicted_counts["lru"] += 1

        window = ChatWindow(chat_id, max_messages=self._max_messages)
        self._windows[chat_id] = window
        return window

    def peek(self, chat_id: str) -> ChatWindow | None:
        """Вернуть окно без изменения порядка LRU. None если нет."""
        return self._windows.get(chat_id)

    def evict_idle(self, timeout_sec: int | None = None) -> int:
        """Удалить окна, неактивные более timeout_sec секунд. Возвращает число удалённых."""
        threshold = timeout_sec if timeout_sec is not None else IDLE_EVICTION_SEC
        now = time.time()
        idle_ids = [cid for cid, w in self._windows.items() if now - w.last_activity_at > threshold]
        for cid in idle_ids:
            del self._windows[cid]
        self._evicted_counts["idle"] += len(idle_ids)
        return len(idle_ids)

    def list_windows(self) -> list[dict[str, Any]]:
        """Список всех окон в виде dict."""
        return [w.to_dict() for w in self._windows.values()]

    def remove(self, chat_id: int) -> bool:
        """Удалить окно для указанного чата. Возвращает True если окно существовало."""
        if chat_id in self._windows:
            del self._windows[chat_id]
            return True
        return False

    def clear_all(self) -> int:
        """Очистить все окна. Возвращает число удалённых."""
        count = len(self._windows)
        self._windows.clear()
        return count

    def get_eviction_counts(self) -> dict[str, int]:
        """Счётчики eviction по причине: lru и idle."""
        return dict(self._evicted_counts)

    @property
    def active_count(self) -> int:
        """Количество активных окон."""
        return len(self._windows)

    def stats(self) -> dict[str, Any]:
        """Статистика менеджера окон."""
        return {
            "active_windows": self.active_count,
            "capacity": self._capacity,
            "total_messages": sum(w.message_count for w in self._windows.values()),
        }


# Глобальный синглтон для импорта из ecosystem_health и других модулей
chat_window_manager = ChatWindowManager()
