# -*- coding: utf-8 -*-
"""
Тесты для TelegramBridge MCP-контура.

Проверяем:
1. client lifecycle остаётся идемпотентным;
2. `database is locked` переживается через controlled restart и повтор вызова.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_bridge_module():
    """Импортирует telegram_bridge.py напрямую из mcp-servers."""
    module_path = (
        Path(__file__).resolve().parents[2] / "mcp-servers" / "telegram" / "telegram_bridge.py"
    )
    spec = importlib.util.spec_from_file_location("telegram_bridge_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_message(text: str) -> SimpleNamespace:
    """Строит минимальный fake Message для `_msg_to_dict()`."""
    return SimpleNamespace(
        id=501,
        chat=SimpleNamespace(id=123, title="Test Chat", first_name=None),
        from_user=SimpleNamespace(first_name="Tester"),
        text=text,
        caption=None,
        date=SimpleNamespace(isoformat=lambda: "2026-03-27T19:15:00+00:00"),
        media=None,
        reply_to_message_id=None,
    )


class _FakeClient:
    """Минимальный fake Pyrogram client для lifecycle/retry тестов."""

    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def send_message(self, chat_id, text):
        _ = (chat_id, text)
        if self.fail_send:
            raise RuntimeError("database is locked")
        return _fake_message(text)


@pytest.mark.asyncio
async def test_bridge_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторный `start()` не должен пересоздавать клиента поверх уже активного."""
    bridge_module = _load_bridge_module()
    created_clients: list[_FakeClient] = []

    def _fake_make_client():
        client = _FakeClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(bridge_module, "_make_client", _fake_make_client)
    bridge = bridge_module.TelegramBridge()

    await bridge.start()
    await bridge.start()

    assert len(created_clients) == 1
    assert created_clients[0].started == 1


@pytest.mark.asyncio
async def test_send_message_restarts_client_once_on_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session-lock должен переживаться через один controlled restart клиента."""
    bridge_module = _load_bridge_module()
    first = _FakeClient(fail_send=True)
    second = _FakeClient(fail_send=False)
    created_clients = [first, second]

    def _fake_make_client():
        return created_clients.pop(0)

    monkeypatch.setattr(bridge_module, "_make_client", _fake_make_client)
    bridge = bridge_module.TelegramBridge()

    await bridge.start()
    result = await bridge.send_message(123, "hello")

    assert first.started == 1
    assert first.stopped == 1
    assert second.started == 1
    assert result["text"] == "hello"
