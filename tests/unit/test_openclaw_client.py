# -*- coding: utf-8 -*-
"""Unit tests OpenClawClient: semantic guard, fallback и управление сессией."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.cloud_key_probe import CloudProbeResult
from src.core.exceptions import ProviderAuthError, ProviderError
from src.openclaw_client import OpenClawClient


class _FakeStreamResponse:
    """Минимальный async stream response для unit-тестов OpenClaw SSE."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = exc_type, exc, tb
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b""


@pytest.fixture
def client() -> OpenClawClient:
    with patch("src.openclaw_client.config") as mock_config:
        mock_config.OPENCLAW_URL = "http://mock-claw"
        mock_config.OPENCLAW_TOKEN = "token"
        mock_config.LM_STUDIO_URL = "http://mock-lm"
        mock_config.LM_STUDIO_API_KEY = ""
        mock_config.LM_STUDIO_NATIVE_REASONING_MODE = "off"
        mock_config.LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS = 2
        mock_config.LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN = 8
        mock_config.LOCAL_FALLBACK_ENABLED = True
        mock_config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC = None
        mock_config.OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC = 240
        mock_config.OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC = 240
        mock_config.OPENCLAW_OPENAI_CODEX_BUFFERED_READ_TIMEOUT_SEC = 240
        mock_config.OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC = 540
        mock_config.HISTORY_WINDOW_MESSAGES = 20
        mock_config.HISTORY_WINDOW_MAX_CHARS = None
        mock_config.RETRY_HISTORY_WINDOW_MESSAGES = 8
        mock_config.RETRY_HISTORY_WINDOW_MAX_CHARS = 4000
        mock_config.RETRY_MESSAGE_MAX_CHARS = 1200
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
async def test_warmup_runtime_route_runs_short_probe_and_clears_temp_session(
    client: OpenClawClient,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_stream(
        *,
        message,
        chat_id,
        system_prompt=None,
        force_cloud=False,
        max_output_tokens=None,
        images=None,
    ):
        captured["message"] = message
        captured["chat_id"] = chat_id
        captured["system_prompt"] = system_prompt
        captured["force_cloud"] = force_cloud
        captured["max_output_tokens"] = max_output_tokens
        captured["images"] = images
        client._set_last_runtime_route(  # noqa: SLF001
            channel="openclaw_cloud",
            model="openai-codex/gpt-5.4",
            route_reason="openclaw_response_ok",
            route_detail="Ответ получен через OpenClaw API",
            force_cloud=bool(force_cloud),
        )
        yield "OK"

    with patch.object(client, "health_check", new=AsyncMock(return_value=True)):
        with patch(
            "src.openclaw_client.get_runtime_primary_model", return_value="openai-codex/gpt-5.4"
        ):
            with patch.object(client, "send_message_stream", new=_fake_stream):
                with patch.object(client, "clear_session") as clear_session:
                    report = await client.warmup_runtime_route(force_refresh=True)

    assert report["ok"] is True
    assert report["reason"] == "warmup_completed"
    assert report["route"]["model"] == "openai-codex/gpt-5.4"
    assert captured["chat_id"] == "__runtime_route_warmup__"
    assert captured["force_cloud"] is True
    assert captured["max_output_tokens"] == 8
    clear_session.assert_called_once_with("__runtime_route_warmup__")


@pytest.mark.asyncio
async def test_send_message_stream_success_buffered(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(
                client, "_openclaw_completion_once", new=AsyncMock(return_value="Hello World")
            ):
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
async def test_send_message_stream_strips_reasoning_before_history_cache(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    noisy_response = (
        "think\n"
        "Thinking Process:\n"
        "1. Проверю контекст.\n"
        "2. Сформулирую ответ.\n\n"
        "🦀 Контекст восстановлен. Продолжаем работу."
    )

    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(
                client, "_openclaw_completion_once", new=AsyncMock(return_value=noisy_response)
            ):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-reasoning-clean"):
                    chunks.append(chunk)

    assert "".join(chunks) == "🦀 Контекст восстановлен. Продолжаем работу."
    assert (
        client._sessions["chat-reasoning-clean"][-1]["content"]
        == "🦀 Контекст восстановлен. Продолжаем работу."
    )


@pytest.mark.asyncio
async def test_send_message_stream_strips_agentic_scratchpad_before_history_cache(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    noisy_response = (
        "Ready.\n"
        "Wait, I'll check if codex is installed.\n"
        "which codex\n"
        "Let's execute.\n\n"
        "🦀 `codex` найден. Продолжаем работу."
    )

    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(
                client, "_openclaw_completion_once", new=AsyncMock(return_value=noisy_response)
            ):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-agentic-clean"):
                    chunks.append(chunk)

    assert "".join(chunks) == "🦀 `codex` найден. Продолжаем работу."
    assert (
        client._sessions["chat-agentic-clean"][-1]["content"]
        == "🦀 `codex` найден. Продолжаем работу."
    )


@pytest.mark.asyncio
async def test_send_message_stream_sanitizes_restored_history_cache(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    cached_history = json.dumps(
        [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "think\nThinking Process:\n1. Проверка.\n\n🦀 Уже очищенный смысл.",
            },
        ],
        ensure_ascii=False,
    )

    with patch("src.openclaw_client.history_cache.get", return_value=cached_history):
        with patch("src.openclaw_client.history_cache.set") as cache_set:
            with patch.object(
                model_manager,
                "get_best_model",
                new=AsyncMock(return_value="google/gemini-2.5-flash"),
            ):
                with patch.object(model_manager, "is_local_model", return_value=False):
                    with patch.object(
                        client,
                        "_openclaw_completion_once",
                        new=AsyncMock(return_value="Новый ответ"),
                    ):
                        chunks = []
                        async for chunk in client.send_message_stream("Hi", "chat-restored-cache"):
                            chunks.append(chunk)

    assert "".join(chunks) == "Новый ответ"
    assert client._sessions["chat-restored-cache"][1]["content"] == "🦀 Уже очищенный смысл."
    persisted_payloads = [call.args[1] for call in cache_set.call_args_list if len(call.args) >= 2]
    assert persisted_payloads
    assert all("Thinking Process" not in payload for payload in persisted_payloads)


@pytest.mark.asyncio
async def test_send_message_stream_sanitizes_existing_in_memory_session(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    client._sessions["chat-existing-session"] = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": "think\nThe model is thinking.\n\n🦀 Сохраняем только это.",
        },
    ]

    with patch("src.openclaw_client.history_cache.set") as cache_set:
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
        ):
            with patch.object(model_manager, "is_local_model", return_value=False):
                with patch.object(
                    client, "_openclaw_completion_once", new=AsyncMock(return_value="OK")
                ):
                    chunks = []
                    async for chunk in client.send_message_stream("Hi", "chat-existing-session"):
                        chunks.append(chunk)

    assert "".join(chunks) == "OK"
    assert client._sessions["chat-existing-session"][1]["content"] == "🦀 Сохраняем только это."
    persisted_payloads = [call.args[1] for call in cache_set.call_args_list if len(call.args) >= 2]
    assert persisted_payloads
    assert all("The model is thinking" not in payload for payload in persisted_payloads)


@pytest.mark.asyncio
async def test_send_message_stream_honors_preferred_cloud_model(client: OpenClawClient) -> None:
    """Явно запрошенная модель из owner/web-path должна идти в runtime без подмены."""
    from src.model_manager import model_manager

    completion = AsyncMock(return_value="Cloud preferred OK")
    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ) as get_best:
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=completion):
                chunks = []
                async for chunk in client.send_message_stream(
                    "Hi",
                    "chat-preferred-cloud",
                    preferred_model="google-gemini-cli/gemini-3.1-pro-preview",
                ):
                    chunks.append(chunk)

    assert "".join(chunks) == "Cloud preferred OK"
    assert get_best.await_count == 0
    assert completion.await_args.kwargs["model_id"] == "google-gemini-cli/gemini-3.1-pro-preview"
    route = client.get_last_runtime_route()
    assert route.get("model") == "google-gemini-cli/gemini-3.1-pro-preview"
    assert route.get("channel") == "openclaw_cloud"


def test_resolve_buffered_read_timeout_uses_provider_specific_budgets(
    client: OpenClawClient,
) -> None:
    """Для зависающих buffered cloud-маршрутов должен включаться отдельный budget ожидания."""
    with patch("src.openclaw_client.config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC", None):
        with patch(
            "src.openclaw_client.config.OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC", 210.0
        ):
            with patch(
                "src.openclaw_client.config.OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC",
                195.0,
            ):
                with patch(
                    "src.openclaw_client.config.OPENCLAW_OPENAI_CODEX_BUFFERED_READ_TIMEOUT_SEC",
                    180.0,
                ):
                    assert (
                        client._resolve_buffered_read_timeout_sec(model_id="codex-cli/gpt-5.4")
                        == 210.0
                    )  # noqa: SLF001
                    assert (
                        client._resolve_buffered_read_timeout_sec(
                            model_id="google-gemini-cli/gemini-3.1-pro-preview"
                        )
                        == 195.0
                    )  # noqa: SLF001
                    assert (
                        client._resolve_buffered_read_timeout_sec(model_id="openai-codex/gpt-5.4")
                        == 180.0
                    )  # noqa: SLF001
                    assert (
                        client._resolve_buffered_read_timeout_sec(
                            model_id="google/gemini-3.1-pro-preview"
                        )
                        is None
                    )  # noqa: SLF001


@pytest.mark.asyncio
async def test_openclaw_completion_once_passes_request_timeout_for_google_gemini_cli(
    client: OpenClawClient,
) -> None:
    """Buffered-запрос к google-gemini-cli должен иметь отдельный request-level read-timeout."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
    client._http_client.post.return_value = response

    with patch(
        "src.openclaw_client.config.OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC", 205.0
    ):
        text = await client._openclaw_completion_once(
            model_id="google-gemini-cli/gemini-3.1-pro-preview",
            messages_to_send=[{"role": "user", "content": "ping"}],
        )

    assert text == "OK"
    timeout_arg = client._http_client.post.await_args.kwargs["timeout"]
    assert isinstance(timeout_arg, httpx.Timeout)
    assert timeout_arg.read == 205.0


@pytest.mark.asyncio
async def test_openclaw_completion_once_passes_request_timeout_for_codex_cli(
    client: OpenClawClient,
) -> None:
    """Buffered-запрос к codex-cli должен иметь отдельный request-level read-timeout."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
    client._http_client.post.return_value = response

    with patch("src.openclaw_client.config.OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC", 222.0):
        text = await client._openclaw_completion_once(
            model_id="codex-cli/gpt-5.4",
            messages_to_send=[{"role": "user", "content": "ping"}],
        )

    assert text == "OK"
    timeout_arg = client._http_client.post.await_args.kwargs["timeout"]
    assert isinstance(timeout_arg, httpx.Timeout)
    assert timeout_arg.read == 222.0


@pytest.mark.asyncio
async def test_openclaw_completion_once_keeps_request_timeout_unbounded_for_other_provider_by_default(
    client: OpenClawClient,
) -> None:
    """Если общий budget не задан, не навязываем read-timeout всем остальным провайдерам."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
    client._http_client.post.return_value = response

    with patch("src.openclaw_client.config.OPENCLAW_BUFFERED_READ_TIMEOUT_SEC", None):
        text = await client._openclaw_completion_once(
            model_id="google/gemini-3.1-pro-preview",
            messages_to_send=[{"role": "user", "content": "ping"}],
        )

    assert text == "OK"
    assert client._http_client.post.await_args.kwargs["timeout"] is None


@pytest.mark.asyncio
async def test_send_message_stream_marks_request_lifecycle(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    started = MagicMock()
    finished = MagicMock()

    with patch.object(model_manager, "mark_request_started", new=started):
        with patch.object(model_manager, "mark_request_finished", new=finished):
            with patch.object(
                model_manager,
                "get_best_model",
                new=AsyncMock(return_value="google/gemini-2.5-flash"),
            ):
                with patch.object(model_manager, "is_local_model", return_value=False):
                    with patch.object(
                        client, "_openclaw_completion_once", new=AsyncMock(return_value="OK")
                    ):
                        chunks = []
                        async for chunk in client.send_message_stream(
                            "Lifecycle", "chat-lifecycle"
                        ):
                            chunks.append(chunk)

    assert "".join(chunks) == "OK"
    assert started.call_count == 1
    assert finished.call_count == 1


@pytest.mark.asyncio
async def test_send_message_stream_text_request_strips_legacy_image_parts(
    client: OpenClawClient,
) -> None:
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
    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
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
    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(client, "_openclaw_completion_once", new=completion):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-retry-empty"):
                    chunks.append(chunk)

    assert "retry" in "".join(chunks).lower()
    assert completion.await_count == 2


def test_build_retry_messages_truncates_middle_and_applies_char_budget(
    client: OpenClawClient,
) -> None:
    messages = [
        {"role": "system", "content": "S" * 500},
        {"role": "user", "content": "A" * 900},
        {"role": "assistant", "content": "B" * 900},
        {"role": "user", "content": "C" * 900},
    ]

    with patch("src.openclaw_client.config.RETRY_HISTORY_WINDOW_MESSAGES", 3):
        with patch("src.openclaw_client.config.RETRY_HISTORY_WINDOW_MAX_CHARS", 700):
            with patch("src.openclaw_client.config.RETRY_MESSAGE_MAX_CHARS", 180):
                compacted = client._build_retry_messages(messages)

    total_chars = client._messages_size(compacted)
    assert total_chars <= 700
    assert compacted[0]["role"] == "system"
    assert "[...TRUNCATED MIDDLE...]" in str(compacted[0]["content"])
    assert any("[...TRUNCATED MIDDLE...]" in str(item.get("content")) for item in compacted)


def test_normalize_usage_snapshot_accepts_openai_and_fallback_fields(
    client: OpenClawClient,
) -> None:
    assert client._normalize_usage_snapshot({}) is None  # noqa: SLF001
    assert client._normalize_usage_snapshot(None) is None  # noqa: SLF001

    normalized = client._normalize_usage_snapshot(  # noqa: SLF001
        {"input_tokens": 7, "output_tokens": 3}
    )

    assert normalized == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


def test_commit_usage_snapshot_updates_compat_stats_and_cost_analytics(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    fake_analytics = MagicMock()
    with patch.object(model_manager, "_cost_analytics", fake_analytics):
        client._commit_usage_snapshot(  # noqa: SLF001
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model_id="google/gemini-2.5-flash",
        )

    assert client.get_usage_stats() == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }
    fake_analytics.record_usage.assert_called_once()
    call_args = fake_analytics.record_usage.call_args
    assert call_args[0][0] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert call_args[1]["model_id"] == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_openclaw_completion_once_estimates_usage_when_response_has_no_usage(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "cloud-usage",
                }
            }
        ]
    }
    client._http_client.post.return_value = response
    fake_analytics = MagicMock()
    messages = [{"role": "user", "content": "Ответь одним словом: cloud-usage"}]

    with patch.object(model_manager, "_cost_analytics", fake_analytics):
        result = await client._openclaw_completion_once(  # noqa: SLF001
            model_id="google/gemini-2.5-flash",
            messages_to_send=messages,
        )

    expected_usage = client._estimate_usage_snapshot(messages, "cloud-usage")  # noqa: SLF001
    assert result == "cloud-usage"
    assert expected_usage is not None
    assert client.get_usage_stats() == {
        "input_tokens": expected_usage["prompt_tokens"],
        "output_tokens": expected_usage["completion_tokens"],
        "total_tokens": expected_usage["total_tokens"],
    }
    fake_analytics.record_usage.assert_called_once()
    call_args2 = fake_analytics.record_usage.call_args
    assert call_args2[0][0] == expected_usage
    assert call_args2[1]["model_id"] == "google/gemini-2.5-flash"
    request_payload = client._http_client.post.call_args.kwargs["json"]
    assert request_payload["stream"] is False


@pytest.mark.asyncio
async def test_local_empty_stream_retry_uses_compact_retry_context(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    client._sessions["chat-local-retry"] = [
        {"role": "system", "content": "S" * 400},
        {"role": "user", "content": "U" * 1200},
        {"role": "assistant", "content": "A" * 1200},
        {"role": "user", "content": "Q" * 1200},
    ]

    completion = AsyncMock(side_effect=["<EMPTY MESSAGE>", "Локальный retry ок"])
    with patch("src.openclaw_client.config.LOCAL_HISTORY_WINDOW_MESSAGES", 12):
        with patch("src.openclaw_client.config.LOCAL_HISTORY_WINDOW_MAX_CHARS", 12000):
            with patch("src.openclaw_client.config.RETRY_HISTORY_WINDOW_MESSAGES", 3):
                with patch("src.openclaw_client.config.RETRY_HISTORY_WINDOW_MAX_CHARS", 650):
                    with patch("src.openclaw_client.config.RETRY_MESSAGE_MAX_CHARS", 160):
                        with patch.object(
                            model_manager,
                            "get_best_model",
                            new=AsyncMock(return_value="local/nemotron"),
                        ):
                            with patch.object(
                                model_manager,
                                "is_local_model",
                                side_effect=lambda mid: str(mid).startswith("local"),
                            ):
                                with patch.object(
                                    model_manager,
                                    "ensure_model_loaded",
                                    new=AsyncMock(return_value=True),
                                ):
                                    with patch.object(
                                        client,
                                        "_direct_lm_fallback",
                                        new=AsyncMock(return_value=None),
                                    ):
                                        with patch.object(
                                            client, "_openclaw_completion_once", new=completion
                                        ):
                                            chunks = []
                                            async for chunk in client.send_message_stream(
                                                "Новый запрос", "chat-local-retry"
                                            ):
                                                chunks.append(chunk)

    assert "".join(chunks) == "Локальный retry ок"
    assert completion.await_count == 2
    retry_messages = completion.await_args_list[1].kwargs["messages_to_send"]
    assert client._messages_size(retry_messages) <= 650
    assert any("[...TRUNCATED MIDDLE...]" in str(item.get("content")) for item in retry_messages)


@pytest.mark.asyncio
async def test_session_management_respects_window(client: OpenClawClient) -> None:
    client._sessions["chat-1"] = [{"role": "user", "content": "1"}] * 25

    from src.model_manager import model_manager

    with patch("src.openclaw_client.config.HISTORY_WINDOW_MESSAGES", 20):
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
        ):
            with patch.object(model_manager, "is_local_model", return_value=False):
                with patch.object(
                    client, "_openclaw_completion_once", new=AsyncMock(return_value="OK")
                ):
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

    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(
                client,
                "_openclaw_completion_once",
                new=AsyncMock(return_value="400 No models loaded. Please load a model"),
            ):
                chunks = []
                async for chunk in client.send_message_stream("Hi", "chat-1", force_cloud=True):
                    chunks.append(chunk)

    assert "модель" in "".join(chunks).lower()


@pytest.mark.asyncio
async def test_local_autoload_failure_switches_to_cloud_candidate(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.object(model_manager, "get_best_model", new=AsyncMock(return_value="local")):
        with patch.object(
            model_manager, "is_local_model", side_effect=lambda mid: str(mid).startswith("local")
        ):
            with patch.object(
                model_manager, "ensure_model_loaded", new=AsyncMock(return_value=False)
            ):
                with patch.object(
                    client,
                    "_pick_cloud_retry_model",
                    new=AsyncMock(return_value="google/gemini-2.5-flash"),
                ):
                    with patch.object(
                        client, "_openclaw_completion_once", new=AsyncMock(return_value="Cloud OK")
                    ) as completion:
                        chunks = []
                        async for chunk in client.send_message_stream("Hi", "chat-local-fallback"):
                            chunks.append(chunk)

    assert "".join(chunks) == "Cloud OK"
    assert completion.await_count == 1
    assert completion.await_args.kwargs["model_id"] == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_force_cloud_remaps_local_selected_model_to_cloud_candidate(
    client: OpenClawClient,
) -> None:
    """При force_cloud локальная первичная модель должна быть заменена на cloud-кандидат."""
    from src.model_manager import model_manager

    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="local/nemotron")
    ):
        with patch.object(
            model_manager, "is_local_model", side_effect=lambda mid: str(mid).startswith("local")
        ):
            with patch.object(
                client,
                "_pick_cloud_retry_model",
                new=AsyncMock(return_value="google/gemini-2.5-flash"),
            ):
                with patch.object(
                    client, "_openclaw_completion_once", new=AsyncMock(return_value="Cloud OK")
                ) as completion:
                    chunks = []
                    async for chunk in client.send_message_stream(
                        "Hi", "chat-force-cloud", force_cloud=True
                    ):
                        chunks.append(chunk)

    assert "".join(chunks) == "Cloud OK"
    assert completion.await_count == 1
    assert completion.await_args.kwargs["model_id"] == "google/gemini-2.5-flash"
    route = client.get_last_runtime_route()
    assert route.get("channel") == "openclaw_cloud"
    assert route.get("force_cloud") is True


@pytest.mark.asyncio
async def test_send_message_stream_passes_text_max_output_tokens(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(return_value="Короткий ответ")
    with patch("src.openclaw_client.config.USERBOT_MAX_OUTPUT_TOKENS", 333):
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
        ):
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
    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
    ):
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
async def test_vision_addon_missing_auto_mode_skips_alt_local_vision_and_goes_to_cloud(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(return_value="Cloud vision OK")
    with patch("src.openclaw_client.config.LOCAL_PREFERRED_VISION_MODEL", "auto"):
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="local/nemotron")
        ):
            with patch.object(
                model_manager,
                "is_local_model",
                side_effect=lambda mid: str(mid).startswith("local"),
            ):
                with patch.object(
                    model_manager, "ensure_model_loaded", new=AsyncMock(return_value=True)
                ) as ensure_loaded:
                    with patch.object(
                        client,
                        "_pick_cloud_retry_model",
                        new=AsyncMock(return_value="google/gemini-2.5-flash"),
                    ):
                        with patch.object(
                            model_manager,
                            "_local_candidates",
                            new=AsyncMock(
                                return_value=[("qwen2-vl-2b-instruct-abliterated-mlx", MagicMock())]
                            ),
                        ) as local_candidates:
                            with patch.object(
                                client, "_direct_lm_fallback", new=AsyncMock(return_value=None)
                            ) as direct_fallback:
                                with patch.object(
                                    client, "_openclaw_completion_once", new=completion
                                ):
                                    chunks = []
                                    async for chunk in client.send_message_stream(
                                        "Посмотри фото",
                                        "chat-photo-auto",
                                        images=["ZmFrZS1pbWFnZQ=="],
                                    ):
                                        chunks.append(chunk)

    assert "".join(chunks) == "Cloud vision OK"
    ensure_loaded.assert_not_awaited()
    direct_fallback.assert_not_awaited()
    assert completion.await_count == 1
    assert completion.await_args.kwargs["model_id"] == "google/gemini-2.5-flash"
    local_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_auto_mode_remaps_accidental_local_vision_selection_to_cloud(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    with patch("src.openclaw_client.config.LOCAL_PREFERRED_VISION_MODEL", "auto"):
        with patch.object(
            model_manager,
            "get_best_model",
            new=AsyncMock(return_value="qwen2-vl-2b-instruct-abliterated-mlx"),
        ):
            with patch.object(
                model_manager,
                "is_local_model",
                side_effect=lambda mid: (
                    str(mid).startswith("qwen2-vl") or str(mid).startswith("local")
                ),
            ):
                with patch.object(
                    client,
                    "_pick_cloud_retry_model",
                    new=AsyncMock(return_value="google/gemini-2.5-flash"),
                ):
                    with patch.object(
                        model_manager, "ensure_model_loaded", new=AsyncMock(return_value=True)
                    ) as ensure_loaded:
                        with patch.object(
                            client,
                            "_direct_lm_fallback",
                            new=AsyncMock(return_value="Локальный vision"),
                        ) as direct_fallback:
                            with patch.object(
                                client,
                                "_openclaw_completion_once",
                                new=AsyncMock(return_value="Cloud vision OK"),
                            ) as completion:
                                chunks = []
                                async for chunk in client.send_message_stream(
                                    "Опиши фото",
                                    "chat-photo-remap",
                                    images=["ZmFrZS1pbWFnZQ=="],
                                ):
                                    chunks.append(chunk)

    assert "".join(chunks) == "Cloud vision OK"
    ensure_loaded.assert_not_awaited()
    direct_fallback.assert_not_awaited()
    assert completion.await_count == 1
    assert completion.await_args.kwargs["model_id"] == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_photo_auto_mode_without_cloud_candidate_does_not_fall_back_to_direct_local(
    client: OpenClawClient,
) -> None:
    from src.model_manager import model_manager

    with patch("src.openclaw_client.config.LOCAL_PREFERRED_VISION_MODEL", "auto"):
        with patch.object(
            model_manager,
            "get_best_model",
            new=AsyncMock(return_value="qwen2-vl-2b-instruct-abliterated-mlx"),
        ):
            with patch.object(
                model_manager,
                "is_local_model",
                side_effect=lambda mid: (
                    str(mid).startswith("qwen2-vl") or str(mid).startswith("local")
                ),
            ):
                with patch.object(
                    client, "_pick_cloud_retry_model", new=AsyncMock(return_value="")
                ):
                    with patch.object(
                        model_manager, "ensure_model_loaded", new=AsyncMock(return_value=True)
                    ) as ensure_loaded:
                        with patch.object(
                            client,
                            "_direct_lm_fallback",
                            new=AsyncMock(return_value="Локальный vision"),
                        ) as direct_fallback:
                            with patch.object(
                                client,
                                "_openclaw_completion_once",
                                new=AsyncMock(return_value="vision add-on is not loaded"),
                            ) as completion:
                                chunks = []
                                async for chunk in client.send_message_stream(
                                    "Опиши фото",
                                    "chat-photo-no-cloud-candidate",
                                    images=["ZmFrZS1pbWFnZQ=="],
                                ):
                                    chunks.append(chunk)

    assert "не поддерживает обработку фото" in "".join(chunks)
    ensure_loaded.assert_not_awaited()
    direct_fallback.assert_not_awaited()
    assert completion.await_count >= 1


def test_local_recovery_disabled_for_photo_in_auto_vision_mode(client: OpenClawClient) -> None:
    with patch("src.openclaw_client.config.LOCAL_PREFERRED_VISION_MODEL", "auto"):
        with patch("src.openclaw_client.config.LOCAL_FALLBACK_ENABLED", True):
            assert client._local_recovery_enabled(force_cloud=False, has_photo=True) is False


def test_local_recovery_enabled_for_text_route_even_in_auto_vision_mode(
    client: OpenClawClient,
) -> None:
    with patch("src.openclaw_client.config.LOCAL_PREFERRED_VISION_MODEL", "auto"):
        with patch("src.openclaw_client.config.LOCAL_FALLBACK_ENABLED", True):
            assert client._local_recovery_enabled(force_cloud=False, has_photo=False) is True


@pytest.mark.asyncio
async def test_auth_error_without_cloud_retry_falls_back_to_local(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    completion = AsyncMock(side_effect=["401 Unauthorized: invalid api key", "Локальный ответ"])
    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
        ):
            with patch.object(
                model_manager,
                "is_local_model",
                side_effect=lambda mid: str(mid).startswith("local"),
            ):
                with patch.object(
                    client, "_pick_cloud_retry_model", new=AsyncMock(return_value="")
                ):
                    with patch.object(
                        client,
                        "_resolve_local_model_for_retry",
                        new=AsyncMock(return_value="local/qwen"),
                    ):
                        with patch.object(
                            model_manager, "ensure_model_loaded", new=AsyncMock(return_value=True)
                        ):
                            with patch.object(client, "_openclaw_completion_once", new=completion):
                                chunks = []
                                async for chunk in client.send_message_stream(
                                    "Hi", "chat-auth-local-fallback"
                                ):
                                    chunks.append(chunk)

    assert "".join(chunks) == "Локальный ответ"
    assert completion.await_count == 2
    assert completion.await_args_list[1].kwargs["model_id"] == "local/qwen"


@pytest.mark.asyncio
async def test_provider_timeout_does_not_use_local_recovery_when_disabled(
    client: OpenClawClient,
) -> None:
    """
    Если LOCAL_FALLBACK_ENABLED=0, cloud-ошибка не должна запускать
    автопереход в локальный recovery.
    """
    from src.model_manager import model_manager

    with patch("src.openclaw_client.config.LOCAL_FALLBACK_ENABLED", False):
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
        ):
            with patch.object(model_manager, "is_local_model", return_value=False):
                with patch.object(
                    client,
                    "_openclaw_completion_once",
                    new=AsyncMock(return_value="provider timeout"),
                ):
                    with patch.object(
                        client,
                        "_resolve_local_model_for_retry",
                        new=AsyncMock(return_value="local/qwen"),
                    ) as to_local:
                        with patch.object(
                            client,
                            "_direct_lm_fallback",
                            new=AsyncMock(return_value="Локальный ответ"),
                        ) as direct_local:
                            chunks = []
                            async for chunk in client.send_message_stream(
                                "Hi", "chat-no-local-recovery"
                            ):
                                chunks.append(chunk)

    text = "".join(chunks).lower()
    assert "облачный сервис" in text
    to_local.assert_not_awaited()
    direct_local.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_retry_updates_runtime_route_to_current_attempt(client: OpenClawClient) -> None:
    """
    Во время cloud->cloud recovery runtime-route должен показывать текущий
    fallback-кандидат, а не застывший стартовый маршрут.
    """
    from src.model_manager import model_manager

    seen_routes: list[dict[str, object]] = []

    async def _fake_completion(*, model_id, **kwargs):
        _ = kwargs
        route = client.get_last_runtime_route()
        seen_routes.append(
            {
                "model_id": model_id,
                "route_model": route.get("model"),
                "route_status": route.get("status"),
                "route_attempt": route.get("attempt"),
            }
        )
        if model_id == "codex-cli/gpt-5.4":
            return "provider timeout"
        return "Cloud retry OK"

    with patch.object(
        model_manager, "get_best_model", new=AsyncMock(return_value="codex-cli/gpt-5.4")
    ):
        with patch.object(model_manager, "is_local_model", return_value=False):
            with patch.object(
                client,
                "_pick_cloud_retry_model",
                new=AsyncMock(return_value="google-gemini-cli/gemini-3-flash-preview"),
            ):
                with patch.object(client, "_openclaw_completion_once", new=_fake_completion):
                    chunks = []
                    async for chunk in client.send_message_stream("Hi", "chat-cloud-retry-route"):
                        chunks.append(chunk)

    assert "".join(chunks) == "Cloud retry OK"
    assert seen_routes[0] == {
        "model_id": "codex-cli/gpt-5.4",
        "route_model": "codex-cli/gpt-5.4",
        "route_status": "pending",
        "route_attempt": 1,
    }
    assert seen_routes[1] == {
        "model_id": "google-gemini-cli/gemini-3-flash-preview",
        "route_model": "google-gemini-cli/gemini-3-flash-preview",
        "route_status": "pending",
        "route_attempt": 2,
    }
    route = client.get_last_runtime_route()
    assert route.get("status") == "ok"
    assert route.get("model") == "google-gemini-cli/gemini-3-flash-preview"


@pytest.mark.asyncio
async def test_tier_export_contains_required_fields(client: OpenClawClient) -> None:
    with patch(
        "src.openclaw_client.get_openclaw_cli_runtime_status",
        return_value={"can_reload": True, "error": ""},
    ):
        export = client.get_tier_state_export()
    assert "active_tier" in export
    assert "last_error_code" in export
    assert "tiers_configured" in export
    assert export["secrets_reload_runtime"]["can_reload"] is True


@pytest.mark.asyncio
async def test_cloud_runtime_check_updates_tier_state_from_probe(client: OpenClawClient) -> None:
    with patch(
        "src.openclaw_client.get_openclaw_cli_runtime_status",
        return_value={"can_reload": True, "error": ""},
    ):
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
    assert report["secrets_reload_runtime"]["can_reload"] is True
    state = client.get_tier_state_export()
    assert state["last_provider_status"] == "ok"
    assert state["last_error_code"] is None
    assert state["last_probe_at"] is not None


def test_runtime_google_key_state_detects_placeholder(client: OpenClawClient) -> None:
    with patch("src.openclaw_client.get_google_api_key_from_models", return_value="GEMINI_API_KEY"):
        state = client._runtime_google_key_state()  # noqa: SLF001

    assert state["state"] == "placeholder"
    assert state["tier"] == ""
    assert state["masked"].startswith("GEMI")


def test_effective_runtime_google_key_state_resolves_paid_placeholder_from_env(
    client: OpenClawClient,
) -> None:
    client.gemini_tiers["paid"] = "AIzaPAID1234567890123456789012345"
    client.gemini_tiers["free"] = "AIzaFREE1234567890123456789012345"
    with patch("src.openclaw_client.get_google_api_key_from_models", return_value="GEMINI_API_KEY"):
        with patch.dict(
            "os.environ", {"GEMINI_API_KEY": "AIzaPAID1234567890123456789012345"}, clear=False
        ):
            state = client._effective_runtime_google_key_state()  # noqa: SLF001

    assert state["state"] == "paid"
    assert state["tier"] == "paid"
    assert state["raw_state"] == "placeholder"
    assert state["raw_reference"] == "GEMINI_API_KEY"
    assert state["resolved_from_env"] is True
    assert state["resolved_env_name"] == "GEMINI_API_KEY"


@pytest.mark.asyncio
async def test_cloud_runtime_check_syncs_active_tier_from_models_json(
    client: OpenClawClient,
) -> None:
    client.active_tier = "free"
    client._cloud_tier_state["active_tier"] = "free"
    client._set_last_runtime_route(  # noqa: SLF001
        channel="openclaw_cloud",
        model="google-gemini-cli/gemini-3.1-pro-preview",
        route_reason="warmup_completed",
        route_detail="warmup route",
        force_cloud=True,
    )
    client.gemini_tiers["free"] = "AIzaFREE1234567890123456789012345"
    client.gemini_tiers["paid"] = "AIzaPAID1234567890123456789012345"

    with patch(
        "src.openclaw_client.get_google_api_key_from_models",
        return_value="AIzaPAID1234567890123456789012345",
    ):
        with patch(
            "src.openclaw_client.get_openclaw_cli_runtime_status",
            return_value={
                "can_reload": False,
                "error": "cli_not_executable",
                "cli_path": "/opt/homebrew/bin/openclaw",
            },
        ):
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
                            provider_status="ok",
                            key_source="env:GEMINI_API_KEY_PAID",
                            key_tier="paid",
                            semantic_error_code="ok",
                            recovery_action="none",
                            http_status=200,
                            detail="",
                        ),
                    ]
                ),
            ):
                report = await client.get_cloud_runtime_check()

    assert report["active_tier"] == "paid"
    assert report["current_google_key_state"] == "paid"
    assert report["current_google_key_tier"] == "paid"
    assert report["secrets_reload_runtime"]["error"] == "cli_not_executable"
    assert client.get_tier_state_export()["active_tier"] == "paid"
    assert client.get_last_runtime_route()["active_tier"] == "paid"


@pytest.mark.asyncio
async def test_cloud_runtime_check_reports_effective_paid_key_when_models_json_keeps_placeholder(
    client: OpenClawClient,
) -> None:
    client.active_tier = "free"
    client._cloud_tier_state["active_tier"] = "free"
    client._set_last_runtime_route(  # noqa: SLF001
        channel="openclaw_cloud",
        model="google-gemini-cli/gemini-3.1-pro-preview",
        route_reason="warmup_completed",
        route_detail="warmup route",
        force_cloud=True,
    )
    client.gemini_tiers["free"] = "AIzaFREE1234567890123456789012345"
    client.gemini_tiers["paid"] = "AIzaPAID1234567890123456789012345"

    with patch("src.openclaw_client.get_google_api_key_from_models", return_value="GEMINI_API_KEY"):
        with patch.dict(
            "os.environ", {"GEMINI_API_KEY": "AIzaPAID1234567890123456789012345"}, clear=False
        ):
            with patch(
                "src.openclaw_client.get_openclaw_cli_runtime_status",
                return_value={
                    "can_reload": True,
                    "error": "",
                    "cli_path": "/opt/homebrew/bin/openclaw",
                },
            ):
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
                                provider_status="ok",
                                key_source="env:GEMINI_API_KEY_PAID",
                                key_tier="paid",
                                semantic_error_code="ok",
                                recovery_action="none",
                                http_status=200,
                                detail="",
                            ),
                        ]
                    ),
                ):
                    report = await client.get_cloud_runtime_check()

    assert report["active_tier"] == "paid"
    assert report["current_google_key_state"] == "paid"
    assert report["current_google_key_tier"] == "paid"
    assert report["current_google_key_raw_state"] == "placeholder"
    assert report["current_google_key_reference"] == "GEMINI_API_KEY"
    assert report["current_google_key_resolved_from_env"] is True
    assert client.get_last_runtime_route()["active_tier"] == "paid"


@pytest.mark.asyncio
async def test_switch_cloud_tier_syncs_last_runtime_route_active_tier(
    client: OpenClawClient,
) -> None:
    client._set_last_runtime_route(  # noqa: SLF001
        channel="openclaw_cloud",
        model="google-gemini-cli/gemini-3.1-pro-preview",
        route_reason="openclaw_response_ok",
        route_detail="Ответ получен через OpenClaw API",
        force_cloud=True,
    )
    client.gemini_tiers["paid"] = "AIzaPAID1234567890123456789012345"

    with patch.object(client, "_set_google_key_in_models", return_value=True):
        with patch("src.openclaw_client.is_ai_studio_key", return_value=True):
            with patch(
                "src.openclaw_client.reload_openclaw_secrets",
                new=AsyncMock(return_value={"ok": True}),
            ):
                result = await client.switch_cloud_tier("paid")

    assert result["ok"] is True
    assert client.get_last_runtime_route()["active_tier"] == "paid"


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
    semantic = client._semantic_from_provider_exception(
        ProviderAuthError(message="401", user_message="auth failed")
    )
    assert semantic["code"] == "openclaw_auth_unauthorized"


def test_semantic_from_provider_exception_maps_vision_addon_missing(client: OpenClawClient) -> None:
    semantic = client._semantic_from_provider_exception(
        ProviderError(
            message="Error in iterating prediction stream: ValueError: Vision add-on is not loaded, but images were provided for processing",
            user_message="backend error",
        )
    )
    assert semantic["code"] == "vision_addon_missing"


def test_refresh_gateway_token_from_runtime_updates_auth_header(
    client: OpenClawClient, tmp_path: Path
) -> None:
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


def test_resolve_gateway_reported_model_prefers_recent_fallback_log(
    client: OpenClawClient, tmp_path: Path
) -> None:
    log_path = tmp_path / "openclaw.log"
    log_path.write_text(
        '2026-03-11T04:09:15.775+01:00 [model-fallback] Model "openai-codex/gpt-5.4" not found. Fell back to "google/gemini-3.1-pro-preview".\n',
        encoding="utf-8",
    )
    client._gateway_log_path = log_path

    resolved = client._resolve_gateway_reported_model(  # noqa: SLF001
        "openai-codex/gpt-5.4",
        request_started_at=0.0,
    )

    assert resolved == "google/gemini-3.1-pro-preview"


def test_resolve_gateway_reported_model_uses_embedded_session_state_after_lane_error(
    client: OpenClawClient,
    tmp_path: Path,
) -> None:
    """Если gateway не написал model-fallback, truth берём из session-state embedded agent."""
    log_path = tmp_path / "openclaw.log"
    log_path.write_text(
        '2026-03-11T04:09:15.775+01:00 [diagnostic] lane task error: lane=session:agent:main:openai:abc123 durationMs=1234 error="HTTP 401: Missing scopes: model.request"\n',
        encoding="utf-8",
    )
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(
        (
            "{"
            '"agent:main:openai:abc123": {'
            '"modelProvider": "google-gemini-cli", '
            '"model": "gemini-3.1-pro-preview"'
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    client._gateway_log_path = log_path
    client._openclaw_sessions_index_path = sessions_path

    resolved = client._resolve_gateway_reported_model(  # noqa: SLF001
        "openai-codex/gpt-5.4",
        request_started_at=0.0,
    )

    assert resolved == "google-gemini-cli/gemini-3.1-pro-preview"


@pytest.mark.asyncio
async def test_direct_lm_fallback_uses_lm_studio_auth_headers(client: OpenClawClient) -> None:
    native_response = MagicMock()
    native_response.status_code = 200
    native_response.json.return_value = {
        "output": [{"type": "message", "content": "Локальный ответ"}],
        "response_id": "resp-native-1",
        "stats": {"total_output_tokens": 42, "reasoning_output_tokens": 0},
    }

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=native_response)
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False

    with patch("src.openclaw_client.config.LM_STUDIO_API_KEY", "lm-secret"):
        with patch("src.openclaw_client.is_lm_studio_available", new=AsyncMock(return_value=True)):
            with patch(
                "src.openclaw_client.httpx.AsyncClient", return_value=fake_client
            ) as mock_async_client:
                result = await client._direct_lm_fallback(  # noqa: SLF001
                    chat_id="chat-lm-auth",
                    messages_to_send=[{"role": "user", "content": "Привет"}],
                    model_hint="nvidia/nemotron-3-nano",
                )

    assert result == "Локальный ответ"
    assert mock_async_client.call_args.kwargs["headers"]["Authorization"] == "Bearer lm-secret"
    assert mock_async_client.call_args.kwargs["headers"]["x-api-key"] == "lm-secret"
    fake_client.post.assert_awaited_once()
    assert fake_client.post.await_args.args[0] == "/api/v1/chat"
    assert fake_client.post.await_args.kwargs["json"]["reasoning"] == "off"


@pytest.mark.asyncio
async def test_direct_lm_fallback_native_chat_reuses_response_id(client: OpenClawClient) -> None:
    first_response = MagicMock()
    first_response.status_code = 200
    first_response.json.return_value = {
        "output": [{"type": "message", "content": "Первый локальный ответ"}],
        "response_id": "resp-native-1",
        "stats": {"total_output_tokens": 80, "reasoning_output_tokens": 0},
    }
    second_response = MagicMock()
    second_response.status_code = 200
    second_response.json.return_value = {
        "output": [{"type": "message", "content": "Второй локальный ответ"}],
        "response_id": "resp-native-2",
        "stats": {"total_output_tokens": 81, "reasoning_output_tokens": 0},
    }

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=[first_response, second_response])
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False

    with patch("src.openclaw_client.is_lm_studio_available", new=AsyncMock(return_value=True)):
        with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake_client):
            first = await client._direct_lm_fallback(  # noqa: SLF001
                chat_id="chat-native-state",
                messages_to_send=[
                    {"role": "system", "content": "Отвечай на русском"},
                    {"role": "user", "content": "Привет"},
                ],
                model_hint="nvidia/nemotron-3-nano",
            )
            second = await client._direct_lm_fallback(  # noqa: SLF001
                chat_id="chat-native-state",
                messages_to_send=[
                    {"role": "system", "content": "Отвечай на русском"},
                    {"role": "user", "content": "Привет"},
                    {"role": "assistant", "content": "Первый локальный ответ"},
                    {"role": "user", "content": "Продолжай"},
                ],
                model_hint="nvidia/nemotron-3-nano",
            )

    assert first == "Первый локальный ответ"
    assert second == "Второй локальный ответ"
    first_call = fake_client.post.await_args_list[0]
    second_call = fake_client.post.await_args_list[1]
    assert first_call.args[0] == "/api/v1/chat"
    assert "previous_response_id" not in first_call.kwargs["json"]
    assert second_call.kwargs["json"]["previous_response_id"] == "resp-native-1"
    assert second_call.kwargs["json"]["input"] == "Продолжай"
    assert first_call.kwargs["json"]["reasoning"] == "off"
    assert second_call.kwargs["json"]["reasoning"] == "off"


@pytest.mark.asyncio
async def test_direct_lm_fallback_falls_back_to_compat_if_native_has_no_message(
    client: OpenClawClient,
) -> None:
    native_response = MagicMock()
    native_response.status_code = 200
    native_response.json.return_value = {
        "output": [{"type": "reasoning", "content": "внутренние мысли"}],
        "response_id": "resp-native-empty",
        "stats": {"total_output_tokens": 40, "reasoning_output_tokens": 40},
    }
    compat_response = MagicMock()
    compat_response.status_code = 200
    compat_response.json.return_value = {
        "choices": [{"message": {"content": "Compat локальный ответ"}}]
    }

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=[native_response, compat_response])
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False

    with patch("src.openclaw_client.is_lm_studio_available", new=AsyncMock(return_value=True)):
        with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake_client):
            result = await client._direct_lm_fallback(  # noqa: SLF001
                chat_id="chat-native-empty",
                messages_to_send=[{"role": "user", "content": "Привет"}],
                model_hint="nvidia/nemotron-3-nano",
            )

    assert result == "Compat локальный ответ"
    assert fake_client.post.await_args_list[0].args[0] == "/api/v1/chat"
    assert fake_client.post.await_args_list[1].args[0] == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_direct_lm_fallback_native_chat_auto_continues_on_output_cap(
    client: OpenClawClient,
) -> None:
    first_response = MagicMock()
    first_response.status_code = 200
    first_response.json.return_value = {
        "output": [{"type": "message", "content": "Первая часть ответа"}],
        "response_id": "resp-native-1",
        "stats": {"total_output_tokens": 118, "reasoning_output_tokens": 0},
    }
    second_response = MagicMock()
    second_response.status_code = 200
    second_response.json.return_value = {
        "output": [{"type": "message", "content": "Вторая часть ответа"}],
        "response_id": "resp-native-2",
        "stats": {"total_output_tokens": 47, "reasoning_output_tokens": 0},
    }

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=[first_response, second_response])
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False

    with patch("src.openclaw_client.is_lm_studio_available", new=AsyncMock(return_value=True)):
        with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake_client):
            result = await client._direct_lm_fallback(  # noqa: SLF001
                chat_id="chat-native-continue",
                messages_to_send=[{"role": "user", "content": "Расскажи длинно"}],
                model_hint="nvidia/nemotron-3-nano",
                max_output_tokens=120,
            )

    assert result == "Первая часть ответа\n\nВторая часть ответа"
    assert fake_client.post.await_count == 2
    first_call = fake_client.post.await_args_list[0]
    second_call = fake_client.post.await_args_list[1]
    assert first_call.args[0] == "/api/v1/chat"
    assert second_call.args[0] == "/api/v1/chat"
    assert second_call.kwargs["json"]["previous_response_id"] == "resp-native-1"
    assert "Продолжай ответ с того места" in second_call.kwargs["json"]["input"]
    assert second_call.kwargs["json"]["reasoning"] == "off"


@pytest.mark.asyncio
async def test_empty_response_does_not_override_last_auth_error(client: OpenClawClient) -> None:
    from src.model_manager import model_manager

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
        with patch.object(
            model_manager, "get_best_model", new=AsyncMock(return_value="google/gemini-2.5-flash")
        ):
            with patch.object(
                model_manager,
                "is_local_model",
                side_effect=lambda mid: str(mid).startswith("local"),
            ):
                with patch.object(
                    model_manager,
                    "get_best_cloud_model",
                    new=AsyncMock(return_value="google/gemini-2.5-flash"),
                ):
                    with patch.object(
                        client, "_resolve_local_model_for_retry", new=AsyncMock(return_value=None)
                    ):
                        with patch.object(
                            client,
                            "_openclaw_completion_once",
                            new=AsyncMock(
                                side_effect=ProviderAuthError(
                                    message="401", user_message="auth failed"
                                )
                            ),
                        ):
                            chunks = []
                            async for chunk in client.send_message_stream(
                                "Hi", "chat-auth-priority"
                            ):
                                chunks.append(chunk)

    text = "".join(chunks).lower()
    assert "ключ" in text
    assert ("авторизац" in text) or ("невалид" in text)


@pytest.mark.asyncio
async def test_force_cloud_empty_stream_switches_to_runtime_cloud_retry(
    client: OpenClawClient,
) -> None:
    """
    При force_cloud и пустом облачном ответе пробуем следующий live fallback
    из runtime-цепочки, а не старый hardcoded OpenAI API fallback.
    """
    from src.model_manager import model_manager

    completion = AsyncMock(
        side_effect=[
            "<EMPTY MESSAGE>",
            "<EMPTY MESSAGE>",
            "Cloud recovery OK",
        ]
    )
    with patch(
        "src.openclaw_client.get_runtime_primary_model", return_value="openai-codex/gpt-5.4"
    ):
        with patch(
            "src.openclaw_client.get_runtime_fallback_models",
            return_value=["google/gemini-3.1-pro-preview", "qwen-portal/coder-model"],
        ):
            with patch.object(
                model_manager, "get_best_model", new=AsyncMock(return_value="openai-codex/gpt-5.4")
            ):
                with patch.object(model_manager, "is_local_model", return_value=False):
                    with patch.object(
                        model_manager,
                        "get_best_cloud_model",
                        new=AsyncMock(return_value="openai-codex/gpt-5.4"),
                    ):
                        with patch.object(client, "_openclaw_completion_once", new=completion):
                            chunks = []
                            async for chunk in client.send_message_stream(
                                "Hi",
                                "chat-force-cloud-quality-retry",
                                force_cloud=True,
                            ):
                                chunks.append(chunk)

    assert "".join(chunks) == "Cloud recovery OK"
    assert completion.await_count == 3
    assert completion.await_args_list[-1].kwargs["model_id"] == "google/gemini-3.1-pro-preview"
    route = client.get_last_runtime_route()
    assert route.get("channel") == "openclaw_cloud"
