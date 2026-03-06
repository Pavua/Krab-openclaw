# -*- coding: utf-8 -*-
"""Unit tests OpenClawClient: semantic guard, fallback и управление сессией."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.cloud_key_probe import CloudProbeResult
from src.core.exceptions import ProviderAuthError, ProviderError
from src.openclaw_client import OpenClawClient


@pytest.fixture
def client() -> OpenClawClient:
    with patch("src.openclaw_client.config") as mock_config:
        mock_config.OPENCLAW_URL = "http://mock-claw"
        mock_config.OPENCLAW_TOKEN = "token"
        mock_config.LM_STUDIO_URL = "http://mock-lm"
        mock_config.LOCAL_FALLBACK_ENABLED = True
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
    route = client.get_last_runtime_route()
    assert route.get("channel") == "openclaw_cloud"
    assert route.get("status") == "ok"


@pytest.mark.asyncio
async def test_send_message_stream_marks_request_lifecycle(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    started = MagicMock()
    finished = MagicMock()

    with patch.object(model_manager, "mark_request_started", new=started):
        with patch.object(model_manager, "mark_request_finished", new=finished):
            with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
                with patch.object(model_manager, "is_local_model", return_value=False):
                    with patch.object(client, "_openclaw_completion_once", new=AsyncMock(return_value="OK")):
                        chunks = []
                        async for chunk in client.send_message_stream("Lifecycle", "chat-lifecycle"):
                            chunks.append(chunk)

    assert "".join(chunks) == "OK"
    assert started.call_count == 1
    assert finished.call_count == 1


@pytest.mark.asyncio
async def test_send_message_stream_text_request_strips_legacy_image_parts(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    client._sessions["chat-with-image"] = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
            ],
        }
    ]
    completion = AsyncMock(return_value="OK")
    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=completion):
                chunks = []
                async for chunk in client.send_message_stream("текст", "chat-with-image"):
                    chunks.append(chunk)

    assert "".join(chunks) == "OK"
    sent_messages = completion.await_args.kwargs["messages_to_send"]
    assert isinstance(sent_messages[0]["content"], str)
    assert "Изображение в контексте пропущено" in sent_messages[0]["content"]


@pytest.mark.asyncio
async def test_send_message_stream_retries_on_lm_empty_stream(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(side_effect=["<EMPTY MESSAGE>", "Нормальный ответ после retry"])
    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=completion):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-retry-empty"):
                    chunks.append(chunk)

    assert "retry" in "".join(chunks).lower()
    assert completion.await_count == 2


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
async def test_local_autoload_failure_switches_to_cloud_candidate(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="local")):
        with patch.object(model_manager, "is_local_model", side_effect=lambda mid: str(mid).startswith("local")):
            with patch.object(model_manager, "ensure_model_loaded", new=AsyncMock(return_value=False)):
                with patch.object(model_manager, "get_best_cloud_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
                    with patch.object(client, "_openclaw_completion_once", new=AsyncMock(return_value="Cloud OK")) as completion:
                        chunks = []
                        async for chunk in client.send_message_stream("Hi", "chat-local-fallback"):
                            chunks.append(chunk)

    assert "".join(chunks) == "Cloud OK"
    assert completion.await_count == 1
    assert completion.await_args.kwargs["model_id"] == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_send_message_stream_passes_text_max_output_tokens(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(return_value="Короткий ответ")
    with patch("src.openclaw_client.config.USERBOT_MAX_OUTPUT_TOKENS", 333):
        with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
            with patch.object(model_manager, "is_local_model", return_value=False):
                with patch.object(client, "_openclaw_completion_once", new=completion):
                    chunks = []
                    async for chunk in client.send_message_stream(
                        "Hi",
                        "chat-max-out-text",
                        max_output_tokens=333,
                    ):
                        chunks.append(chunk)

    assert "".join(chunks) == "Короткий ответ"
    assert completion.await_args.kwargs["max_output_tokens"] == 333


@pytest.mark.asyncio
async def test_send_message_stream_passes_photo_max_output_tokens(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(return_value="Фото-ответ")
    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=completion):
                chunks = []
                async for chunk in client.send_message_stream(
                    "Describe image",
                    "chat-max-out-photo",
                    images=["ZmFrZS1pbWFnZS1iNjQ="],
                    max_output_tokens=222,
                ):
                    chunks.append(chunk)

    assert "".join(chunks) == "Фото-ответ"
    assert completion.await_args.kwargs["max_output_tokens"] == 222


@pytest.mark.asyncio
async def test_auth_error_without_openai_key_falls_back_to_local_not_openai(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(side_effect=["401 Unauthorized: invalid api key", "Локальный ответ"])
    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
        with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
            with patch.object(model_manager, "is_local_model", side_effect=lambda mid: str(mid).startswith("local")):
                with patch.object(model_manager, "get_best_cloud_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
                    with patch.object(client, "_resolve_local_model_for_retry", new=AsyncMock(return_value="local/qwen")):
                        with patch.object(model_manager, "ensure_model_loaded", new=AsyncMock(return_value=True)):
                            with patch.object(client, "_openclaw_completion_once", new=completion):
                                chunks = []
                                async for chunk in client.send_message_stream("Hi", "chat-auth-local-fallback"):
                                    chunks.append(chunk)

    assert "".join(chunks) == "Локальный ответ"
    assert completion.await_count == 2
    assert completion.await_args_list[1].kwargs["model_id"] == "local/qwen"


@pytest.mark.asyncio
async def test_provider_timeout_does_not_use_local_recovery_when_disabled(client: OpenClawClient) -> None:
    """
    Если LOCAL_FALLBACK_ENABLED=0, cloud-ошибка не должна запускать
    автопереход в локальный recovery.
    """
    from src.model_manager import model_manager

    with patch("src.openclaw_client.config.LOCAL_FALLBACK_ENABLED", False):
        with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
            with patch.object(model_manager, "is_local_model", return_value=False):
                with patch.object(
                    client,
                    "_openclaw_completion_once",
                    new=AsyncMock(return_value="provider timeout"),
                ):
                    with patch.object(client, "_resolve_local_model_for_retry", new=AsyncMock(return_value="local/qwen")) as to_local:
                        with patch.object(client, "_direct_lm_fallback", new=AsyncMock(return_value="Локальный ответ")) as direct_local:
                            chunks = []
                            async for chunk in client.send_message_stream("Hi", "chat-no-local-recovery"):
                                chunks.append(chunk)

    text = "".join(chunks).lower()
    assert "облачный сервис" in text
    to_local.assert_not_awaited()
    direct_local.assert_not_awaited()


@pytest.mark.asyncio
async def test_tier_export_contains_required_fields(client: OpenClawClient) -> None:
    export = client.get_tier_state_export()
    assert "active_tier" in export
    assert "last_error_code" in export
    assert "tiers_configured" in export


@pytest.mark.asyncio
async def test_cloud_runtime_check_updates_tier_state_from_probe(client: OpenClawClient) -> None:
    with patch(
        "src.openclaw_client.probe_gemini_key",
        new=AsyncMock(
            side_effect=[
                CloudProbeResult(
                    provider_status="ok",
                    key_source="env:GEMINI_API_KEY_FREE",
                    key_tier="free",
                    semantic_error_code="ok",
                    recovery_action="none",
                    http_status=200,
                    detail="",
                ),
                CloudProbeResult(
                    provider_status="auth",
                    key_source="env:GEMINI_API_KEY_PAID",
                    key_tier="paid",
                    semantic_error_code="auth_invalid",
                    recovery_action="switch_provider_or_key",
                    http_status=401,
                    detail="unauthorized",
                ),
            ]
        ),
    ):
        report = await client.get_cloud_runtime_check()

    assert report["ok"] is True
    state = client.get_tier_state_export()
    assert state["last_provider_status"] == "ok"
    assert state["last_error_code"] is None
    assert state["last_probe_at"] is not None


def test_detect_semantic_error_model_crash(client: OpenClawClient) -> None:
    semantic = client._detect_semantic_error("The model has crashed without additional information")
    assert semantic is not None
    assert semantic["code"] == "lm_model_crash"


def test_detect_semantic_error_model_unloaded(client: OpenClawClient) -> None:
    semantic = client._detect_semantic_error("Model unloaded.")
    assert semantic is not None
    assert semantic["code"] == "model_not_loaded"


def test_detect_semantic_error_tool_response_error_blob(client: OpenClawClient) -> None:
    semantic = client._detect_semantic_error(
        '<|im_start|>user\n<tool_response>{"status": "error"}</tool_response>\n<|im_end|>'
    )
    assert semantic is not None
    assert semantic["code"] == "lm_malformed_response"


def test_detect_semantic_error_vision_addon_missing(client: OpenClawClient) -> None:
    semantic = client._detect_semantic_error(
        "ValueError: Vision add-on is not loaded, but images were provided for processing"
    )
    assert semantic is not None
    assert semantic["code"] == "vision_addon_missing"


def test_detect_semantic_error_unauthorized_returns_canonical_code(client: OpenClawClient) -> None:
    semantic = client._detect_semantic_error("401 Unauthorized: invalid api key")
    assert semantic is not None
    assert semantic["code"] == "openclaw_auth_unauthorized"


def test_semantic_from_provider_auth_exception_uses_canonical_code(client: OpenClawClient) -> None:
    semantic = client._semantic_from_provider_exception(ProviderAuthError(message="401", user_message="auth failed"))
    assert semantic["code"] == "openclaw_auth_unauthorized"


def test_semantic_from_provider_exception_maps_vision_addon_missing(client: OpenClawClient) -> None:
    semantic = client._semantic_from_provider_exception(
        ProviderError(
            message="Error in iterating prediction stream: ValueError: Vision add-on is not loaded, but images were provided for processing",
            user_message="backend error",
        )
    )
    assert semantic["code"] == "vision_addon_missing"


def test_refresh_gateway_token_from_runtime_updates_auth_header(client: OpenClawClient, tmp_path: Path) -> None:
    cfg_path = tmp_path / "openclaw.json"
    cfg_path.write_text(
        '{"gateway":{"auth":{"mode":"token","token":"runtime-token-123"}}}',
        encoding="utf-8",
    )
    client._openclaw_runtime_config_path = cfg_path
    client.token = "stale-token"
    client._http_client.headers = {"Authorization": "Bearer stale-token"}

    refreshed = client._refresh_gateway_token_from_runtime()

    assert refreshed is True
    assert client.token == "runtime-token-123"
    assert client._http_client.headers["Authorization"] == "Bearer runtime-token-123"


@pytest.mark.asyncio
async def test_empty_response_does_not_override_last_auth_error(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
        with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
            with patch.object(model_manager, "is_local_model", side_effect=lambda mid: str(mid).startswith("local")):
                with patch.object(model_manager, "get_best_cloud_model", new=AsyncMock(return_value="google/gemini-2.5-flash")):
                    with patch.object(client, "_resolve_local_model_for_retry", new=AsyncMock(return_value=None)):
                        with patch.object(
                            client,
                            "_openclaw_completion_once",
                            new=AsyncMock(side_effect=ProviderAuthError(message="401", user_message="auth failed")),
                        ):
                            chunks = []
                            async for chunk in client.send_message_stream("Hi", "chat-auth-priority"):
                                chunks.append(chunk)

    text = "".join(chunks).lower()
    assert "ключ" in text
    assert ("авторизац" in text) or ("невалид" in text)
