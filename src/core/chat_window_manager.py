# -*- coding: utf-8 -*-
"""
chat_window_manager — менеджер активных окон чатов.

ChatWindow хранит скользящее окно последних N сообщений чата
для быстрого context-lookup без обращения к archive.db.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import structlog

_log = structlog.get_logger(__name__)

_DEFAULT_CAPACITY = 50  # сообщений per window


@dataclass
class ChatWindow:
    """Окно сообщений одного чата."""

    chat_id: str
    capacity: int = _DEFAULT_CAPACITY
    messages: deque = field(default_factory=lambda: deque(maxlen=_DEFAULT_CAPACITY))

    def __post_init__(self) -> None:
        # Пересоздаём deque с нужным maxlen если capacity задан нестандартный
        self.messages = deque(maxlen=self.capacity)

    def push(self, message: dict) -> None:
        """Добавить сообщение в окно."""
        self.messages.append(message)

    def recent(self, n: int | None = None) -> list[dict]:
        """Последние n сообщений (или все если n=None)."""
        msgs = list(self.messages)
        if n is not None:
            msgs = msgs[-n:]
        return msgs

    def clear(self) -> None:
        """Очистить окно."""
        self.messages.clear()


class ChatWindowManager:
    """Глобальный менеджер ChatWindow'ов."""

    def __init__(self) -> None:
        self._windows: dict[str, ChatWindow] = {}

    def get_or_create(self, chat_id: str | int, capacity: int = _DEFAULT_CAPACITY) -> ChatWindow:
        """Вернуть существующее окно или создать новое."""
        key = str(chat_id)
        if key not in self._windows:
            self._windows[key] = ChatWindow(chat_id=key, capacity=capacity)
            _log.debug("chat_window_created", chat_id=key, capacity=capacity)
        return self._windows[key]

    def get(self, chat_id: str | int) -> ChatWindow | None:
        """Вернуть окно или None."""
        return self._windows.get(str(chat_id))

    def remove(self, chat_id: str | int) -> None:
        """Удалить окно."""
        self._windows.pop(str(chat_id), None)

    def stats(self) -> dict:
        """Статистика для Prometheus.

        Returns:
            {
                "active_windows": N,
                "capacity": суммарная ёмкость,
                "total_messages": суммарное количество сообщений в памяти,
            }
        """
        active = len(self._windows)
        capacity = sum(w.capacity for w in self._windows.values())
        total_msgs = sum(len(w.messages) for w in self._windows.values())
        return {
            "active_windows": active,
            "capacity": capacity,
            "total_messages": total_msgs,
        }

    def all_windows(self) -> dict[str, ChatWindow]:
        """Все активные окна."""
        return dict(self._windows)


# Синглтон
chat_window_manager = ChatWindowManager()
