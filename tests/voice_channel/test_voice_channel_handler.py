# -*- coding: utf-8 -*-
"""
Tests for VoiceChannelHandler (VA Phase 1.4).

Coverage:
1) handle_voice_message streams tokens from OpenClaw
2) Session is created and transcript is buffered
3) Memory recall is injected into system prompt (happy path)
4) Memory recall failure is silently swallowed (best-effort)
5) OpenClaw error yields error token, no exception propagates
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List

from src.voice_channel.voice_channel_handler import VoiceChannelHandler
from src.voice_channel.voice_state import VoiceSession

# ─── Stubs ────────────────────────────────────────────────────────────────────


class _StubOpenClaw:
    """Minimal async-generator stub for OpenClawClient."""

    def __init__(self, tokens: List[str] | None = None, raise_on_call: Exception | None = None):
        self._tokens = tokens or ["Привет, ", "как", " дела?"]
        self._raise_on_call = raise_on_call
        self.last_call: dict = {}

    async def send_message_stream(
        self,
        message: str,
        chat_id: str,
        system_prompt: str | None = None,
        preferred_model: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        self.last_call = {
            "message": message,
            "chat_id": chat_id,
            "system_prompt": system_prompt,
            "preferred_model": preferred_model,
        }
        if self._raise_on_call:
            raise self._raise_on_call
        for tok in self._tokens:
            yield tok


class _StubMemory:
    """Minimal stub for MemoryManager."""

    def __init__(self, results: str = "", raise_on_call: bool = False):
        self._results = results
        self._raise_on_call = raise_on_call
        self.recall_calls: list = []

    def recall(self, query: str, n_results: int = 3) -> str:
        self.recall_calls.append((query, n_results))
        if self._raise_on_call:
            raise RuntimeError("memory_broken")
        return self._results


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _collect(handler: VoiceChannelHandler, **kwargs) -> List[str]:
    """Collect all tokens from handle_voice_message into a list."""
    tokens = []
    async for tok in handler.handle_voice_message(**kwargs):
        tokens.append(tok)
    return tokens


def run(coro):
    """Run a coroutine in a fresh event loop (avoids pytest-asyncio loop conflicts).

    Используем `new_event_loop()` вместо `get_event_loop()`: после того как
    `pytest-asyncio` (mode=auto) закрывает per-test цикл, в MainThread больше нет
    «текущего» loop — и `get_event_loop()` в full-suite контексте бросает
    `RuntimeError: There is no current event loop`. Новый цикл — это изоляция на
    каждый тест без зависимости от глобального состояния.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestVoiceChannelHandlerStreaming:
    """handle_voice_message streams tokens correctly."""

    def test_yields_all_tokens(self):
        expected = ["Привет, ", "это ", "Краб."]
        openclaw = _StubOpenClaw(tokens=expected)
        handler = VoiceChannelHandler(openclaw=openclaw, memory=_StubMemory())

        result = run(
            _collect(handler, chat_id="test_session", message_text="Как дела?", language="ru")
        )
        assert result == expected

    def test_passes_preferred_model(self):
        openclaw = _StubOpenClaw(tokens=["ok"])
        handler = VoiceChannelHandler(openclaw=openclaw, memory=_StubMemory())

        run(_collect(handler, chat_id="s1", message_text="test", language="ru"))
        assert openclaw.last_call["preferred_model"] == "qwen3-30b-a3b-instruct-2507"

    def test_language_in_system_prompt(self):
        openclaw = _StubOpenClaw(tokens=["ok"])
        handler = VoiceChannelHandler(openclaw=openclaw, memory=_StubMemory())

        run(_collect(handler, chat_id="s2", message_text="hola", language="es"))
        assert "es" in openclaw.last_call["system_prompt"]


class TestVoiceChannelHandlerSessions:
    """Session management and transcript buffering."""

    def test_session_created_on_first_message(self):
        handler = VoiceChannelHandler(openclaw=_StubOpenClaw(), memory=_StubMemory())
        assert handler.session_count() == 0

        run(_collect(handler, chat_id="abc", message_text="first", language="ru"))

        assert handler.session_count() == 1
        assert isinstance(handler.get_session("abc"), VoiceSession)

    def test_transcript_buffered_in_session(self):
        handler = VoiceChannelHandler(openclaw=_StubOpenClaw(), memory=_StubMemory())

        run(_collect(handler, chat_id="buf_test", message_text="запомни это", language="ru"))

        session = handler.get_session("buf_test")
        assert session is not None
        recent = session.recent_transcripts(1)
        assert len(recent) == 1
        assert recent[0].text == "запомни это"


class TestVoiceChannelHandlerMemory:
    """Memory recall integration."""

    def test_memory_context_appended_to_prompt(self):
        memory = _StubMemory(results="- факт1\n- факт2")
        openclaw = _StubOpenClaw(tokens=["ok"])
        handler = VoiceChannelHandler(openclaw=openclaw, memory=memory)

        run(_collect(handler, chat_id="m1", message_text="вспомни", language="ru"))

        assert "факт1" in openclaw.last_call["system_prompt"]
        assert len(memory.recall_calls) == 1

    def test_memory_failure_does_not_propagate(self):
        """Memory error must be silently swallowed; stream still works."""
        memory = _StubMemory(raise_on_call=True)
        openclaw = _StubOpenClaw(tokens=["ok"])
        handler = VoiceChannelHandler(openclaw=openclaw, memory=memory)

        result = run(_collect(handler, chat_id="m2", message_text="test", language="ru"))
        assert result == ["ok"]


class TestVoiceChannelHandlerErrors:
    """Error handling when OpenClaw fails."""

    def test_openclaw_error_yields_error_token(self):
        openclaw = _StubOpenClaw(raise_on_call=RuntimeError("gateway_down"))
        handler = VoiceChannelHandler(openclaw=openclaw, memory=_StubMemory())

        result = run(_collect(handler, chat_id="err_test", message_text="test", language="ru"))
        assert len(result) == 1
        assert "Ошибка" in result[0] or "gateway_down" in result[0]
