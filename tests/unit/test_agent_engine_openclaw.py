# -*- coding: utf-8 -*-
"""Tests for OpenClawAdapter (Wave 17-B, Hermes Phase C).

Покрывает: kind, stream(str chunks), stream(error), health(ok), health(fail),
cancel, health cache invalidation.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.agent_engine_openclaw import OpenClawAdapter
from src.core.agent_engine import StreamChunk, EngineHealth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(chunks: list[str] | None = None, health_ok: bool = True) -> MagicMock:
    """Создаёт mock OpenClawClient с заданными chunk'ами."""
    client = MagicMock()

    async def _fake_stream(**kwargs) -> AsyncIterator[str]:
        for chunk in (chunks or []):
            yield chunk

    client.send_message_stream = _fake_stream
    client.health_check = AsyncMock(return_value=health_ok)
    client.cancel_current_request = MagicMock(return_value=True)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_kind():
    """kind всегда возвращает 'openclaw'."""
    adapter = OpenClawAdapter(_make_client())
    assert adapter.kind == "openclaw"


@pytest.mark.asyncio
async def test_stream_yields_text_chunks():
    """stream() конвертирует str-чанки в StreamChunk(chunk_type='text')."""
    client = _make_client(chunks=["Hello", " world"])
    adapter = OpenClawAdapter(client)

    chunks: list[StreamChunk] = []
    async for chunk in adapter.stream("test prompt"):
        chunks.append(chunk)

    # Первые два — text chunks
    text_chunks = [c for c in chunks if c.chunk_type == "text"]
    assert len(text_chunks) == 2
    assert text_chunks[0].text == "Hello"
    assert text_chunks[1].text == " world"

    # Последний — finish sentinel
    finish_chunks = [c for c in chunks if c.chunk_type == "finish"]
    assert len(finish_chunks) == 1
    assert finish_chunks[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_passes_ctx_to_client():
    """stream() передаёт ctx kwargs в send_message_stream."""
    received_kwargs: dict = {}

    client = MagicMock()

    async def _capture_stream(**kwargs):
        received_kwargs.update(kwargs)
        return
        yield  # make it an async generator

    client.send_message_stream = _capture_stream

    adapter = OpenClawAdapter(client)
    ctx = {
        "chat_id": "42",
        "system_prompt": "Test system",
        "force_cloud": True,
        "max_output_tokens": 512,
    }
    async for _ in adapter.stream("prompt", ctx=ctx):
        pass

    assert received_kwargs.get("chat_id") == "42"
    assert received_kwargs.get("system_prompt") == "Test system"
    assert received_kwargs.get("force_cloud") is True
    assert received_kwargs.get("max_output_tokens") == 512


@pytest.mark.asyncio
async def test_stream_handles_error_gracefully():
    """stream() при ошибке выдаёт finish chunk с finish_reason='error'."""
    client = MagicMock()

    async def _broken_stream(**kwargs):
        raise RuntimeError("gateway down")
        yield  # make it an async generator

    client.send_message_stream = _broken_stream

    adapter = OpenClawAdapter(client)
    chunks: list[StreamChunk] = []
    async for chunk in adapter.stream("test"):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "finish"
    assert chunks[0].finish_reason == "error"
    assert "gateway down" in chunks[0].text


@pytest.mark.asyncio
async def test_health_ok():
    """health() возвращает is_healthy=True при health_check() == True."""
    client = _make_client(health_ok=True)
    adapter = OpenClawAdapter(client)

    health = await adapter.health()
    assert health.engine == "openclaw"
    assert health.is_healthy is True
    assert health.error is None


@pytest.mark.asyncio
async def test_health_fail():
    """health() возвращает is_healthy=False при health_check() == False."""
    client = _make_client(health_ok=False)
    adapter = OpenClawAdapter(client)

    health = await adapter.health()
    assert health.engine == "openclaw"
    assert health.is_healthy is False
    assert health.error is not None


@pytest.mark.asyncio
async def test_health_caches_result():
    """health() не вызывает health_check() повторно в пределах TTL."""
    client = _make_client(health_ok=True)
    adapter = OpenClawAdapter(client)

    await adapter.health()
    await adapter.health()

    # Должен быть вызван только один раз (кэш)
    assert client.health_check.call_count == 1


@pytest.mark.asyncio
async def test_cancel_delegates_to_client():
    """cancel() делегирует cancel_current_request() на client."""
    client = _make_client()
    adapter = OpenClawAdapter(client)

    result = await adapter.cancel("session-123")
    assert result is True
    client.cancel_current_request.assert_called_once()


@pytest.mark.asyncio
async def test_close_is_noop():
    """close() не бросает и не вызывает методы client."""
    client = _make_client()
    adapter = OpenClawAdapter(client)
    await adapter.close()  # должен завершиться без ошибки
