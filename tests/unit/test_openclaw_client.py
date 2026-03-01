# -*- coding: utf-8 -*-
"""Unit tests OpenClawClient: semantic guard, fallback и управление сессией."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.openclaw_client import OpenClawClient


@pytest.fixture
def client() -> OpenClawClient:
    with patch("src.openclaw_client.config") as mock_config:
        mock_config.OPENCLAW_URL = "http://mock-claw"
        mock_config.OPENCLAW_TOKEN = "token"
        mock_config.LM_STUDIO_URL = "http://mock-lm"
        mock_config.HISTORY_WINDOW_MESSAGES = 20
        mock_config.HISTORY_WINDOW_MAX_CHARS = None
        inst = OpenClawClient()
        inst._http_client = AsyncMock()
        return inst


@pytest.mark.asyncio
async def test_health_check_success(client: OpenClawClient) -> None:
    resp = MagicMock()
    resp.status_code = 200
    client._http_client.get.return_value = resp
    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_health_check_failure(client: OpenClawClient) -> None:
    resp = MagicMock()
    resp.status_code = 500
    client._http_client.get.return_value = resp
    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_send_message_stream_success_buffered(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=AsyncMock(return_value="Hello World")):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-1"):
                    chunks.append(chunk)

    assert "".join(chunks) == "Hello World"
    assert len(client._sessions["chat-1"]) == 2
    assert client._sessions["chat-1"][1]["content"] == "Hello World"


@pytest.mark.asyncio
async def test_session_management_respects_window(client: OpenClawClient) -> None:
    client._sessions["chat-1"] = [{"role": "user", "content": "1"}] * 25

    from src.model_manager import model_manager
    with patch("src.openclaw_client.config.HISTORY_WINDOW_MESSAGES", 20):
        with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
            with patch.object(model_manager, "is_local_model", return_value=False):
                with patch.object(client, "_openclaw_completion_once", new=AsyncMock(return_value="OK")):
                    async for _ in client.send_message_stream("New", "chat-1"):
                        pass

    assert len(client._sessions["chat-1"]) == 20


def test_clear_session(client: OpenClawClient) -> None:
    client._sessions["chat-1"] = []
    client.clear_session("chat-1")
    assert "chat-1" not in client._sessions


@pytest.mark.asyncio
async def test_semantic_error_returns_user_message_when_force_cloud(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=AsyncMock(return_value="400 No models loaded. Please load a model")):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-1", force_cloud=True):
                    chunks.append(chunk)

    assert "модель" in "".join(chunks).lower()


@pytest.mark.asyncio
async def test_tier_export_contains_required_fields(client: OpenClawClient) -> None:
    export = client.get_tier_state_export()
    assert "active_tier" in export
    assert "last_error_code" in export
    assert "tiers_configured" in export
