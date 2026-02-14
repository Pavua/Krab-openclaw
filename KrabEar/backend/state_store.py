# -*- coding: utf-8 -*-
"""
Минимальное in-memory хранилище состояния для IPC shim KrabEar.

Этот модуль не заменяет реальный Krab Ear backend.
Он нужен для совместимости cross-project тестов в репозитории Krab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StateStore:
    """Простое in-memory хранилище состояния call-assist."""

    db_path: str = ":memory:"
    _state: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value

