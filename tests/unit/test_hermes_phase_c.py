# -*- coding: utf-8 -*-
"""Wave 16-Q (Hermes Phase C): ACP wiring + queue-based streaming."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def event_client():
    from src.integrations.hermes_acp_bridge import _HermesEventClient

    return _HermesEventClient()


def test_event_client_creates_queue_lazily(event_client):
    """get_queue first call → new Queue; second call same session → same instance."""
    q1 = event_client.get_queue("session-1")
    q2 = event_client.get_queue("session-1")
    assert q1 is q2
    q3 = event_client.get_queue("session-2")
    assert q3 is not q1


def test_event_client_drop_queue(event_client):
    """drop_queue убирает session из internal dict."""
    event_client.get_queue("temp")
    assert "temp" in event_client._queues
    event_client.drop_queue("temp")
    assert "temp" not in event_client._queues
    # Drop ещё раз — не падает
    event_client.drop_queue("temp")


@pytest.mark.asyncio
async def test_session_update_text_chunk_lands_in_queue(event_client):
    """agent_message_chunk update → StreamChunk(text=...) в queue."""
    from src.integrations.hermes_acp_bridge import StreamChunk

    update = MagicMock()
    update.sessionUpdate = "agent_message_chunk"
    update.content = MagicMock()
    update.content.text = "hello world"

    notif = MagicMock()
    notif.session_id = "s1"
    notif.update = update

    await event_client.session_update(notif)

    q = event_client.get_queue("s1")
    assert q.qsize() == 1
    chunk = q.get_nowait()
    assert isinstance(chunk, StreamChunk)
    assert chunk.text == "hello world"
    assert chunk.chunk_type == "text"


@pytest.mark.asyncio
async def test_session_update_end_of_turn_emits_finish_sentinel(event_client):
    """end_of_turn update → _STREAM_FINISH_SENTINEL в queue."""
    from src.integrations.hermes_acp_bridge import _STREAM_FINISH_SENTINEL

    update = MagicMock()
    update.sessionUpdate = "end_of_turn"

    notif = MagicMock()
    notif.session_id = "s1"
    notif.update = update

    await event_client.session_update(notif)

    q = event_client.get_queue("s1")
    chunk = q.get_nowait()
    assert chunk is _STREAM_FINISH_SENTINEL


@pytest.mark.asyncio
async def test_session_update_ignores_missing_session_id(event_client):
    """Empty session_id — no-op (defensive)."""
    update = MagicMock()
    update.sessionUpdate = "agent_message_chunk"

    notif = MagicMock()
    notif.session_id = ""
    notif.update = update

    await event_client.session_update(notif)
    # Никаких queue не создано
    assert event_client._queues == {}


@pytest.mark.asyncio
async def test_stream_returns_engine_unavailable_when_no_binary(monkeypatch):
    """Без бинаря health.is_healthy=False → finish chunk engine_unavailable."""
    from src.integrations.hermes_acp_bridge import HermesACPBridge

    bridge = HermesACPBridge(binary="/nonexistent/hermes-bin")
    chunks = [c async for c in bridge.stream("test prompt")]
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "finish"
    assert chunks[0].finish_reason == "engine_unavailable"


@pytest.mark.asyncio
async def test_cancel_returns_false_when_no_connection():
    """Без активной connection — cancel возвращает False."""
    from src.integrations.hermes_acp_bridge import HermesACPBridge

    bridge = HermesACPBridge()
    assert await bridge.cancel("any-session") is False
    assert await bridge.cancel("") is False


@pytest.mark.asyncio
async def test_close_idempotent_when_never_started():
    """close() безопасно вызывать без started subprocess."""
    from src.integrations.hermes_acp_bridge import HermesACPBridge

    bridge = HermesACPBridge()
    await bridge.close()  # не падает
    assert bridge._proc is None
    assert bridge._connection is None


@pytest.mark.asyncio
async def test_stream_full_path_with_mocked_connection(monkeypatch, tmp_path):
    """End-to-end stream() через mocked connect_to_agent + queue → chunks."""
    from src.integrations.hermes_acp_bridge import (
        HermesACPBridge,
        StreamChunk,
        _STREAM_FINISH_SENTINEL,
    )

    # Создаём fake binary file (executable) — проходит binary_available()
    fake_bin = tmp_path / "hermes"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    bridge = HermesACPBridge(binary=str(fake_bin))

    # Mock subprocess
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.pid = 99
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()

    async def fake_spawn(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    # Mock acp connection
    fake_conn = MagicMock()
    fake_conn.initialize = AsyncMock(return_value=MagicMock())
    fake_conn.new_session = AsyncMock(
        return_value=MagicMock(session_id="acp-sess-42", sessionId="acp-sess-42")
    )

    # prompt() симулирует агента: пушит 2 text chunks + finish
    async def fake_prompt(req):
        await asyncio.sleep(0)  # дать stream() начать consumer'ить
        q = bridge._event_client.get_queue("acp-sess-42")
        await q.put(StreamChunk(text="hello ", chunk_type="text"))
        await q.put(StreamChunk(text="world", chunk_type="text"))
        await q.put(_STREAM_FINISH_SENTINEL)

    fake_conn.prompt = AsyncMock(side_effect=fake_prompt)
    fake_conn.close = AsyncMock()

    import acp  # noqa: PLC0415

    monkeypatch.setattr(acp, "connect_to_agent", lambda *a, **kw: fake_conn)

    chunks = [c async for c in bridge.stream("test", ctx={"logical_id": "chat-1"})]
    # Ожидаем 2 text chunks + 1 finish
    assert len(chunks) == 3
    assert chunks[0].text == "hello "
    assert chunks[1].text == "world"
    assert chunks[2].chunk_type == "finish"
    assert chunks[2].finish_reason == "end_of_turn"

    # Session был cached
    assert bridge._sessions["chat-1"] == "acp-sess-42"
