# -*- coding: utf-8 -*-
"""Runtime dispatch: chat/room → engine instance + health gate + fallback.

Wave 17-B (Hermes Phase C).

Логика:
  1. resolve_engine() определяет запрошенный engine (openclaw/hermes/auto)
  2. Если hermes/auto — проверяем health, при unhealthy fallback → openclaw
  3. Возвращает (engine_instance, requested_kind, actual_kind)

Все health-probes кэшируются внутри адаптеров — этот модуль не хранит state.
"""

from __future__ import annotations

import os
from typing import Any

from .agent_engine import AgentEngineClient, EngineKind
from .agent_engine_router import resolve_engine
from .logger import get_logger

logger = get_logger(__name__)


def _dispatch_enabled() -> bool:
    """ENV gate: KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1 включает Phase C.

    По умолчанию OFF — нулевой риск для production.
    Включить явно: export KRAB_AGENT_ENGINE_DISPATCH_ENABLED=1
    """
    val = os.environ.get("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0").strip()
    return val in {"1", "true", "yes"}


async def get_engine_for_route(
    *,
    chat_id: int | str | None = None,
    room: str | None = None,
    openclaw_client: Any,
) -> tuple[AgentEngineClient, EngineKind, EngineKind]:
    """Возвращает (engine_instance, requested_kind, actual_kind).

    actual_kind может отличаться от requested_kind если произошёл fallback
    (Hermes unhealthy → переключаемся на OpenClaw).

    Если dispatch выключен (ENV gate OFF) — всегда возвращает OpenClawAdapter.
    Это гарантирует нулевое изменение поведения в production.
    """
    from .agent_engine_openclaw import OpenClawAdapter

    # Если dispatch отключён — всегда OpenClaw (zero risk)
    if not _dispatch_enabled():
        return OpenClawAdapter(openclaw_client), "openclaw", "openclaw"

    requested: EngineKind = resolve_engine(chat_id=chat_id, room=room)

    # openclaw — прямо возвращаем
    if requested == "openclaw":
        return OpenClawAdapter(openclaw_client), requested, requested

    # hermes или auto — проверяем health перед использованием
    if requested in ("hermes", "auto"):
        try:
            from ..integrations.hermes_acp_bridge import get_hermes_bridge

            # Wave 16-P: get_hermes_bridge async (asyncio.Lock + double-checked locking)
            bridge = await get_hermes_bridge()
            health = await bridge.health()
            if health.is_healthy:
                logger.info(
                    "agent_engine_dispatched_hermes",
                    requested=requested,
                    chat_id=str(chat_id) if chat_id else None,
                    room=room,
                )
                return bridge, requested, "hermes"

            # Hermes недоступен — fallback на OpenClaw
            logger.info(
                "agent_engine_fallback_to_openclaw",
                requested=requested,
                reason=health.error or "hermes_unhealthy",
                chat_id=str(chat_id) if chat_id else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent_engine_hermes_bridge_error",
                error=str(exc),
                requested=requested,
            )

        return OpenClawAdapter(openclaw_client), requested, "openclaw"

    # Неизвестный engine — safety fallback
    logger.warning("agent_engine_unknown_kind", requested=requested)
    return OpenClawAdapter(openclaw_client), requested, "openclaw"
