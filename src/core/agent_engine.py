# -*- coding: utf-8 -*-
"""AgentEngine — abstract interface для оркестрации между OpenClaw и Hermes.

Wave 16-B (Hermes Phase B): только foundation Protocol + dataclasses +
OpenClaw wrapper. Real routing — Phase C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

if TYPE_CHECKING:
    pass

# Тип движка: openclaw (дефолт), hermes (ACP subprocess), auto (выбор по health)
EngineKind = Literal["openclaw", "hermes", "auto"]


@dataclass
class StreamChunk:
    """Унифицированный chunk для обоих engines.

    Шейп подобран под существующий OpenClaw streaming output.
    """

    text: str = ""
    role: str = "assistant"
    chunk_type: Literal["text", "tool_call", "tool_progress", "finish"] = "text"
    tool_name: str = ""
    tool_args: dict[str, Any] | None = None
    tool_result: Any = None
    finish_reason: str | None = None
    metadata: dict[str, Any] | None = field(default=None)


@dataclass
class EngineHealth:
    """Состояние здоровья одного engine."""

    engine: EngineKind
    is_healthy: bool
    latency_ms: float | None = None
    last_check_at: str | None = None
    error: str | None = None


class AgentEngineClient:  # pragma: no cover — Protocol, не инстанцируется напрямую
    """Protocol implementations должны иметь.

    Используй structural subtyping (isinstance не нужен).
    """

    @property
    def kind(self) -> EngineKind:
        """Идентификатор движка."""
        ...

    async def stream(
        self,
        prompt: str,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Стримит ответ на prompt. ctx — доп. контекст (chat_id, room, etc.)."""
        ...

    async def cancel(self, session_id: str) -> bool:
        """Отменяет активную сессию. False если не поддерживается."""
        ...

    async def health(self) -> EngineHealth:
        """Проверяет доступность движка."""
        ...

    async def close(self) -> None:
        """Освобождает ресурсы (subprocess, соединения, etc.)."""
        ...
