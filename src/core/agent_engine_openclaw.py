# -*- coding: utf-8 -*-
"""OpenClaw адаптер для AgentEngine Protocol.

Делает existing OpenClawClient доступным через единый interface, чтобы
llm_flow.py мог выбирать между OpenClaw и Hermes без знания конкретики.

Wave 17-B (Hermes Phase C): live wiring.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, AsyncIterator

from .agent_engine import EngineHealth, EngineKind, StreamChunk
from .logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# TTL кэша health probe (секунды)
_HEALTH_CACHE_TTL = 60.0


class OpenClawAdapter:
    """Adapter: OpenClawClient → AgentEngineClient Protocol.

    НЕ обёртывает конкретные runtime methods (route_query etc) — passthrough
    к existing client'у. send_message_stream → AsyncIterator[str] конвертируется
    в AsyncIterator[StreamChunk] для унифицированного interface.

    Жизненный цикл: singleton, created once per app lifetime (как сам openclaw_client).
    """

    def __init__(self, openclaw_client: Any) -> None:
        self._client = openclaw_client
        # (timestamp_monotonic, EngineHealth) — кэш последнего health probe
        self._healthy_cache: tuple[float, EngineHealth] | None = None

    @property
    def kind(self) -> EngineKind:
        """Идентификатор движка."""
        return "openclaw"

    async def stream(
        self,
        prompt: str,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Делегирует send_message_stream на OpenClaw client.

        ctx может содержать:
            chat_id: str — передаётся в send_message_stream
            system_prompt: str
            images: list[str]
            force_cloud: bool
            preferred_model: str | None
            max_output_tokens: int | None
            disable_tools: bool

        Конвертирует AsyncIterator[str] → AsyncIterator[StreamChunk].
        Финальный StreamChunk имеет chunk_type="finish".
        """
        ctx = ctx or {}
        chat_id: str = str(ctx.get("chat_id") or "_engine_stream_")

        kwargs: dict[str, Any] = {
            "message": prompt,
            "chat_id": chat_id,
        }
        # Передаём только те ключи, которые действительно есть в ctx
        _optional_str = ("system_prompt", "preferred_model")
        _optional_list = ("images",)
        _optional_bool = ("force_cloud", "disable_tools")
        _optional_int = ("max_output_tokens",)

        for key in _optional_str:
            if ctx.get(key) is not None:
                kwargs[key] = ctx[key]
        for key in _optional_list:
            if ctx.get(key) is not None:
                kwargs[key] = ctx[key]
        for key in _optional_bool:
            if ctx.get(key) is not None:
                kwargs[key] = ctx[key]
        for key in _optional_int:
            if ctx.get(key) is not None:
                kwargs[key] = ctx[key]

        last_text = ""
        try:
            async for chunk in self._client.send_message_stream(**kwargs):
                # OpenClaw stream выдаёт str-чанки
                text = str(chunk) if chunk is not None else ""
                last_text = text
                if text:
                    yield StreamChunk(text=text, chunk_type="text")
        except Exception as exc:  # noqa: BLE001
            logger.warning("openclaw_adapter_stream_error", error=str(exc))
            yield StreamChunk(
                text=f"[OpenClaw stream error: {exc}]",
                chunk_type="finish",
                finish_reason="error",
            )
            return

        # Финальный sentinel chunk
        yield StreamChunk(text=last_text, chunk_type="finish", finish_reason="stop")

    async def cancel(self, session_id: str) -> bool:
        """Отменяет текущий запрос через OpenClaw client (если поддерживается)."""
        try:
            if hasattr(self._client, "cancel_current_request"):
                return bool(self._client.cancel_current_request())
        except Exception:  # noqa: BLE001
            pass
        return False

    async def health(self) -> EngineHealth:
        """Health через OpenClaw health_check().

        Кэширует на 60 секунд, чтобы не долбить gateway при каждом запросе.

        Wave 245: при KRAB_OPENCLAW_BYPASS_ENABLED=1 health всё равно
        репортуем настоящий статус gateway — это диагностика, а не
        блокировка трафика. Bypass влияет только на routing send_message_stream.
        """
        now = time.monotonic()
        if self._healthy_cache is not None:
            cache_ts, cached = self._healthy_cache
            if (now - cache_ts) < _HEALTH_CACHE_TTL:
                return cached

        t0 = time.monotonic()
        try:
            ok = await self._client.health_check()
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            health = EngineHealth(
                engine="openclaw",
                is_healthy=bool(ok),
                latency_ms=latency_ms if ok else None,
                last_check_at=_now_iso(),
                error=None if ok else "health_check returned False",
            )
        except Exception as exc:  # noqa: BLE001
            health = EngineHealth(
                engine="openclaw",
                is_healthy=False,
                last_check_at=_now_iso(),
                error=str(exc),
            )

        self._healthy_cache = (now, health)
        return health

    async def close(self) -> None:
        """OpenClaw client управляется централизованно — не закрываем здесь."""
        pass


def _now_iso() -> str:
    """Текущее UTC время в ISO 8601."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
