# -*- coding: utf-8 -*-
"""Wave 150: placeholder ``swarm_loop_guard`` singleton.

Контракт описан в ``swarm_router.swarm_delegations_active`` и
``tests/unit/test_swarm_router.py``: модуль экспортирует singleton с методами:

  - ``active_chains_snapshot() -> list[dict]`` — snapshot текущих цепочек
    делегирования (read-only копия).
  - ``blocked_counters() -> dict`` — агрегированные счётчики:
    ``{"loops": int, "timeouts": int, ...}``.
  - ``_max_hops: int`` — потолок глубины цепочки.
  - ``_timeout_sec: int`` — таймаут на цепочку.

Текущая реализация — заглушка: возвращает пустые данные без побочных
эффектов. Это безопасный default до того как Wave 1XX-X завезёт реальный
loop-guard для делегаций свёрма. Endpoint ``/api/swarm/delegations/active``
теперь возвращает 200 со стабильным payload вместо 500
(``ModuleNotFoundError`` до Wave 150).

Если в будущем понадобится поведение с реальным трекингом — расширить
``SwarmLoopGuard`` методами ``register_chain``/``mark_blocked`` и подключить
в delegation pipeline. Tests уже инжектят свой SimpleNamespace, так что
контракт описан в ``test_swarm_delegations_active``.
"""

from __future__ import annotations

import os
from typing import Any


class SwarmLoopGuard:
    """Singleton loop-guard для swarm delegation chains (placeholder)."""

    def __init__(self) -> None:
        # Параметры из env с разумными default'ами. Подчёркивание сохранено
        # для совместимости с router (он читает ``_max_hops`` / ``_timeout_sec``
        # напрямую, такой же интерфейс ждут тесты).
        self._max_hops: int = self._read_int_env("KRAB_SWARM_LOOP_MAX_HOPS", 5)
        self._timeout_sec: int = self._read_int_env("KRAB_SWARM_LOOP_TIMEOUT_SEC", 300)
        # Активные цепочки и счётчики хранятся локально; placeholder не пишет в них,
        # но методы возвращают копии чтобы внешний код не мутировал внутреннее
        # состояние (см. convention в chat_ban_cache).
        self._active_chains: list[dict[str, Any]] = []
        self._blocked: dict[str, int] = {"loops": 0, "timeouts": 0, "hop_limit": 0}

    @staticmethod
    def _read_int_env(key: str, default: int) -> int:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def active_chains_snapshot(self) -> list[dict[str, Any]]:
        """Возвращает копию списка активных цепочек."""
        return [dict(chain) for chain in self._active_chains]

    def blocked_counters(self) -> dict[str, int]:
        """Возвращает копию счётчиков заблокированных делегаций."""
        return dict(self._blocked)


# Module-level singleton — pattern совпадает с chat_ban_cache / silence_mode.
swarm_loop_guard = SwarmLoopGuard()
