# -*- coding: utf-8 -*-
"""
Тесты устойчивости Local Stream Client (Phase 17.8).

Проверяем:
1. Типизированные причины сбоев StreamFailure.
2. Срабатывание guardrails на reasoning/content loop и лимиты.
3. Таймауты и connection-error поведение.
"""

import json
from typing import Iterable

import aiohttp
import pytest

from src.core.stream_client import OpenClawStreamClient, StreamFailure


def _sse_bytes(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class _FakeResponse:
    """Простой async-context response для подмены aiohttp."""

    def __init__(self, status: int, lines: Iterable[bytes], text_payload: str = "") -> None:
        self.status = status
        self.content = self._iter_lines(list(lines))
        self._text_payload = text_payload

    async def text(self) -> str:
        return self._text_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _iter_lines(self, lines: list[bytes]):
        for line in lines:
            yield line


class _FakeSession:
    """Простой async-context session для подмены aiohttp.ClientSession."""

    def __init__(self, response: _FakeResponse | None = None, post_error: Exception | None = None) -> None:
        self._response = response
        self._post_error = post_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        if self._post_error:
            raise self._post_error
        return self._response


@pytest.mark.asyncio
async def test_stream_chat_raises_reasoning_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenClawStreamClient("http://localhost:1234")
    lines = [
        _sse_bytes({"choices": [{"delta": {"reasoning_content": "Я думаю одинаково"}}]}),
        _sse_bytes({"choices": [{"delta": {"reasoning_content": "Я думаю одинаково"}}]}),
        _sse_bytes({"choices": [{"delta": {"reasoning_content": "Я думаю одинаково"}}]}),
    ]
    fake_response = _FakeResponse(status=200, lines=lines)
    monkeypatch.setattr(
        "src.core.stream_client.aiohttp.ClientSession",
        lambda *args, **kwargs: _FakeSession(response=fake_response),
    )

    with pytest.raises(StreamFailure) as exc:
        async for _ in client.stream_chat({"model": "local-test"}):
            pass
    assert exc.value.reason == "reasoning_loop"


@pytest.mark.asyncio
async def test_stream_chat_raises_reasoning_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenClawStreamClient("http://localhost:1234")
    lines = [
        _sse_bytes({"choices": [{"delta": {"reasoning_content": "0123456789"}}]}),
        _sse_bytes({"choices": [{"delta": {"reasoning_content": "ABCDEF"}}]}),
    ]
    fake_response = _FakeResponse(status=200, lines=lines)
    monkeypatch.setattr(
        "src.core.stream_client.aiohttp.ClientSession",
        lambda *args, **kwargs: _FakeSession(response=fake_response),
    )

    with pytest.raises(StreamFailure) as exc:
        async for _ in client.stream_chat(
            {"model": "local-test", "_krab_max_reasoning_chars": 12}
        ):
            pass
    assert exc.value.reason == "reasoning_limit"


@pytest.mark.asyncio
async def test_stream_chat_raises_content_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenClawStreamClient("http://localhost:1234")
    lines = [
        _sse_bytes({"choices": [{"delta": {"content": "повторяемый-фрагмент"}}]}),
        _sse_bytes({"choices": [{"delta": {"content": "повторяемый-фрагмент"}}]}),
        _sse_bytes({"choices": [{"delta": {"content": "повторяемый-фрагмент"}}]}),
    ]
    fake_response = _FakeResponse(status=200, lines=lines)
    monkeypatch.setattr(
        "src.core.stream_client.aiohttp.ClientSession",
        lambda *args, **kwargs: _FakeSession(response=fake_response),
    )

    with pytest.raises(StreamFailure) as exc:
        async for _ in client.stream_chat({"model": "local-test"}):
            pass
    assert exc.value.reason == "content_loop"


@pytest.mark.asyncio
async def test_stream_chat_raises_content_loop_on_repeated_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ловим цикл, когда абзац повторяется, но chunk-границы разные."""
    client = OpenClawStreamClient("http://localhost:1234")
    repeated = "Это означает, что вы описываете своего спасителя как хорошего человека. "
    lines = []
    for _ in range(8):
        lines.append(_sse_bytes({"choices": [{"delta": {"content": repeated[:40]}}]}))
        lines.append(_sse_bytes({"choices": [{"delta": {"content": repeated[40:]}}]}))
    fake_response = _FakeResponse(status=200, lines=lines)
    monkeypatch.setattr(
        "src.core.stream_client.aiohttp.ClientSession",
        lambda *args, **kwargs: _FakeSession(response=fake_response),
    )

    with pytest.raises(StreamFailure) as exc:
        async for _ in client.stream_chat({"model": "local-test"}):
            pass
    assert exc.value.reason == "content_loop"


@pytest.mark.asyncio
async def test_stream_chat_raises_stream_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenClawStreamClient("http://localhost:1234")
    lines = [_sse_bytes({"choices": [{"delta": {"content": "длинный ответ"}}]})]
    fake_response = _FakeResponse(status=200, lines=lines)
    monkeypatch.setattr(
        "src.core.stream_client.aiohttp.ClientSession",
        lambda *args, **kwargs: _FakeSession(response=fake_response),
    )

    monotonic_values = iter([0.0, 999.0, 999.0, 999.0])

    def fake_monotonic() -> float:
        try:
            return next(monotonic_values)
        except StopIteration:
            return 999.0

    monkeypatch.setattr("src.core.stream_client.time.monotonic", fake_monotonic)

    with pytest.raises(StreamFailure) as exc:
        async for _ in client.stream_chat(
            {"model": "local-test", "_krab_total_timeout_seconds": 0.5}
        ):
            pass
    assert exc.value.reason == "stream_timeout"


@pytest.mark.asyncio
async def test_stream_chat_raises_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenClawStreamClient("http://localhost:1234")
    monkeypatch.setattr(
        "src.core.stream_client.aiohttp.ClientSession",
        lambda *args, **kwargs: _FakeSession(
            response=None,
            post_error=aiohttp.ClientConnectionError("network down"),
        ),
    )

    with pytest.raises(StreamFailure) as exc:
        async for _ in client.stream_chat({"model": "local-test"}):
            pass
    assert exc.value.reason == "connection_error"
