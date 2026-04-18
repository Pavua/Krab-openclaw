# -*- coding: utf-8 -*-
"""
voice_routes.py — FastAPI router for Voice Channel (VA Phase 1.4).

Endpoints:
  POST /v1/voice/message  — принять транскрипт, вернуть SSE stream токенов
  GET  /v1/voice/status   — здоровье и статистика voice channel

Voice Gateway (Krab Ear) POSTает сюда результат STT.
Ответ — Server-Sent Events (text/event-stream).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from structlog import get_logger

if TYPE_CHECKING:
    from .voice_channel_handler import VoiceChannelHandler

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/voice", tags=["voice"])

# Глобальный обработчик — инициализируется в startup (userbot_bridge или main).
_handler: Optional["VoiceChannelHandler"] = None


def set_handler(handler: "VoiceChannelHandler") -> None:
    """Инъекция зависимости: вызывается при старте FastAPI приложения."""
    global _handler  # noqa: PLW0603
    _handler = handler


def get_handler() -> "VoiceChannelHandler":
    """Возвращает текущий обработчик; кидает 503 если не инициализирован."""
    if _handler is None:
        raise HTTPException(status_code=503, detail="voice_channel_not_initialized")
    return _handler


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------


class VoiceMessageRequest(BaseModel):
    """Тело запроса от Voice Gateway."""

    chat_id: str = Field(..., description="Session ID (например, 'session_xyz')")
    text: str = Field(..., description="Транскрибированный текст от Krab Ear")
    language: str = Field(default="ru", description="Целевой язык ответа (ru/es/en)")


class VoiceStatusResponse(BaseModel):
    """Статус голосового канала."""

    status: str
    active_sessions: int
    handler_initialized: bool


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/message")
async def voice_message(request_body: VoiceMessageRequest, request: Request) -> StreamingResponse:
    """
    Принять транскрипт от Voice Gateway, вернуть SSE stream с ответом LLM.

    Response: text/event-stream
    Format per line: data: <token>\\n\\n
    End-of-stream: data: [DONE]\\n\\n
    """
    handler = get_handler()

    if not request_body.text.strip():
        raise HTTPException(status_code=422, detail="text field is empty")

    logger.info(
        "voice_route_message",
        chat_id=request_body.chat_id,
        language=request_body.language,
        text_preview=request_body.text[:80],
    )

    async def sse_generator():
        try:
            async for token in handler.handle_voice_message(
                chat_id=request_body.chat_id,
                message_text=request_body.text,
                language=request_body.language,
            ):
                # SSE format: data: <payload>\n\n
                payload = json.dumps({"token": token}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.error("voice_sse_error", error=str(exc))
            error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"data: {error_payload}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status", response_model=VoiceStatusResponse)
async def voice_status() -> VoiceStatusResponse:
    """Возвращает статус голосового канала и количество активных сессий."""
    initialized = _handler is not None
    active = _handler.session_count() if initialized else 0
    return VoiceStatusResponse(
        status="ok" if initialized else "not_initialized",
        active_sessions=active,
        handler_initialized=initialized,
    )
