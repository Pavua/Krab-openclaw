# -*- coding: utf-8 -*-
"""
ChatWindowManager — скользящее окно активности чата (Chado Wave 16).

Отслеживает последние сообщения и статистику по каждому чату.
LRU-eviction при превышении capacity.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class ChatWindow:
    """Скользящее окно сообщений одного чата."""

    def __init__(self, chat_id: str, max_messages: int = 50) -> None:
        self.chat_id = chat_id
        self.max_messages = max_messages
        self._messages: list[dict[str, Any]] = []
        self._last_active: float = time.monotonic()

    def touch(self) -> None:
        """Обновить метку активности."""
        self._last_active = time.monotonic()

    def append_message(self, role: str, text: str) -> None:
        """Добавить сообщение в окно; evict старые при переполнении."""
        self._messages.append({"role": role, "text": text, "ts": time.monotonic()})
        if len(self._messages) > self.max_messages:
            self._messages.pop(0)
        self.touch()

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def last_active(self) -> float:
        return self._last_active

    def snapshot(self) -> list[dict[str, Any]]:
        """Копия буфера сообщений."""
        return list(self._messages)


class ChatWindowManager:
    """Менеджер окон активности по chat_id с LRU-eviction."""

    def __init__(self, capacity: int = 100, max_messages_per_window: int = 50) -> None:
        self._capacity = capacity
        self._max_messages = max_messages_per_window
        self._windows: OrderedDict[str, ChatWindow] = OrderedDict()

    def get_or_create(self, chat_id: str) -> ChatWindow:
        """Вернуть существующее окно или создать новое (LRU-evict если нужно)."""
        if chat_id in self._windows:
            # Переместить в конец (most-recently-used)
            self._windows.move_to_end(chat_id)
            return self._windows[chat_id]

        # Evict LRU если capacity превышен
        if len(self._windows) >= self._capacity:
            self._windows.popitem(last=False)

        window = ChatWindow(chat_id, max_messages=self._max_messages)
        self._windows[chat_id] = window
        return window

    def peek(self, chat_id: str) -> ChatWindow | None:
        """Вернуть окно без изменения порядка LRU. None если нет."""
        return self._windows.get(chat_id)

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
