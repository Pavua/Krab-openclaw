# -*- coding: utf-8 -*-
"""
chat_filter_config — конфигурация фильтра чатов.

Хранит per-chat режим обработки: active (обычная обработка),
mention-only (только при упоминании), muted (игнорировать).
Используется командами !chatmute / !chatban + Prometheus metrics.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

import structlog

_log = structlog.get_logger(__name__)

FilterMode = Literal["active", "mention-only", "muted"]

_VALID_MODES: frozenset[str] = frozenset({"active", "mention-only", "muted"})


class ChatFilterConfig:
    """In-memory конфиг режима фильтрации per-chat."""

    def __init__(self) -> None:
        # chat_id (str) → mode
        self._modes: dict[str, FilterMode] = {}

    def set_mode(self, chat_id: str | int, mode: FilterMode) -> None:
        """Установить режим для чата."""
        key = str(chat_id)
        if mode not in _VALID_MODES:
            raise ValueError(f"Неверный режим: {mode!r}. Допустимые: {sorted(_VALID_MODES)}")
        prev = self._modes.get(key, "active")
        self._modes[key] = mode
        if prev != mode:
            _log.info("chat_filter_mode_changed", chat_id=key, prev=prev, mode=mode)

    def get_mode(self, chat_id: str | int) -> FilterMode:
        """Режим чата (по умолчанию — active)."""
        return self._modes.get(str(chat_id), "active")

    def remove(self, chat_id: str | int) -> None:
        """Сбросить режим чата к active."""
        self._modes.pop(str(chat_id), None)

    def stats(self) -> dict:
        """Агрегированная статистика для Prometheus.

        Returns:
            {
                "total_chats": N,
                "by_mode": {"active": N, "mention-only": N, "muted": N},
            }
        """
        by_mode: dict[str, int] = defaultdict(int)
        for mode in self._modes.values():
            by_mode[mode] += 1
        # active-чаты не хранятся явно, но счётчик должен быть
        return {
            "total_chats": len(self._modes),
            "by_mode": dict(by_mode),
        }

    def all_modes(self) -> dict[str, FilterMode]:
        """Полная карта chat_id → mode."""
        return dict(self._modes)


# Синглтон
chat_filter_config = ChatFilterConfig()
