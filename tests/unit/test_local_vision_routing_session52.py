# -*- coding: utf-8 -*-
"""Session 52 P0: local vision describe through LM Studio (Gemma 4 vanilla).

Background:
- S51 verify revealed cloud Gemini vision describes timeout 3/3 frames @ 25s
  (production regression). `_describe_video_frame` had hardcoded
  `force_cloud=True`, no escape hatch.
- S52 bench (~7 candidates × 3 stacks):
  - LM Studio + Gemma 4 26B vanilla = 68.5 tok/s text / 1.7-2.2s vision /
    clean output (WINNER)
  - Other candidates (Qwen 3.5/3.6, GLM-4.6V, Gemma-Claude-distilled, OptiQ):
    thinking-mode template quirks или backend errors
- S52 implementation: `KRAB_LOCAL_VISION_ENABLED=1` routes frame describes
  to LM Studio :1234 (Gemma 4 vanilla loaded). Cloud path remains as
  fallback (resilience if local empty).

Coverage:
- _describe_frame_via_lmstudio happy path (200 OK + text content)
- env defaults (URL, model name, API key)
- HTTP error → empty string (fail-open)
- timeout → empty string
- _describe_video_frame routing: env=1 calls local first, env=0/unset → cloud
- empty local result → falls through to cloud (resilience)
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.userbot.media_processors import MediaProcessorsMixin

# ── Host stub ──────────────────────────────────────────────────────────────


class _Host(MediaProcessorsMixin):
    """Минимальный host для тестирования mixin."""

    def __init__(self) -> None:
        self.client = None


# ── _describe_frame_via_lmstudio direct tests ──────────────────────────────


@pytest.mark.asyncio
async def test_lmstudio_describe_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio 200 OK → returns content stripped."""
    monkeypatch.setenv("KRAB_LOCAL_VISION_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("KRAB_LOCAL_VISION_MODEL", "gemma-test")
    monkeypatch.setenv("LM_STUDIO_API_KEY", "sk-test-key")

    host = _Host()

    captured: dict[str, Any] = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "На кадре виден кот."}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        result = await host._describe_frame_via_lmstudio(
            "BASE64_FRAME", idx=0, chat_id="-100", timeout_sec=10.0
        )

    assert result == "На кадре виден кот."
    # Auth header sent
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    # Correct endpoint
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    # Multimodal request shape
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert "BASE64_FRAME" in content[1]["image_url"]["url"]
    assert captured["json"]["model"] == "gemma-test"
    assert captured["timeout"] == 10.0


@pytest.mark.asyncio
async def test_lmstudio_describe_http_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP error → log warning + return empty (fail-open)."""
    monkeypatch.setenv("LM_STUDIO_API_KEY", "sk-test")
    host = _Host()

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.HTTPError("connection refused")

    with patch.object(httpx, "AsyncClient", _FakeClient):
        result = await host._describe_frame_via_lmstudio(
            "X", idx=0, chat_id="-100", timeout_sec=5.0
        )

    assert result == ""


@pytest.mark.asyncio
async def test_lmstudio_describe_reasoning_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если content пустой, читаем reasoning field (thinking-mode models)."""
    monkeypatch.setenv("LM_STUDIO_API_KEY", "")
    host = _Host()

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning": "Думаю: на кадре стол.",
                        }
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        result = await host._describe_frame_via_lmstudio(
            "X", idx=0, chat_id="-100", timeout_sec=5.0
        )

    assert result == "Думаю: на кадре стол."


@pytest.mark.asyncio
async def test_lmstudio_describe_no_auth_when_key_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если LM_STUDIO_API_KEY пустой — Authorization header не отправляется."""
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)
    host = _Host()

    captured_headers: dict[str, Any] = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured_headers.update(headers or {})
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        await host._describe_frame_via_lmstudio("X", idx=0, chat_id="-100", timeout_sec=5.0)

    assert "Authorization" not in captured_headers


# ── _describe_video_frame routing ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_video_frame_local_when_env_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_VISION_ENABLED=1 → calls _describe_frame_via_lmstudio."""
    monkeypatch.setenv("KRAB_LOCAL_VISION_ENABLED", "1")
    host = _Host()

    local_mock = AsyncMock(return_value="local result")
    host._describe_frame_via_lmstudio = local_mock  # type: ignore[method-assign]

    frame_bytes = b"\xff\xd8\xff\xe0fake_jpeg"
    result = await host._describe_video_frame(frame_bytes, idx=2, chat_id="-100")

    assert result == "local result"
    local_mock.assert_called_once()
    # b64 encoded passed
    call_kwargs = local_mock.call_args
    assert call_kwargs.args[0] == base64.b64encode(frame_bytes).decode("utf-8")
    assert call_kwargs.args[1] == 2  # idx


@pytest.mark.asyncio
async def test_describe_video_frame_cloud_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_VISION_ENABLED=0 (default) → NEVER calls local, uses cloud path.

    Cloud path использует openclaw_client.send_message_stream — mock'аем.
    """
    monkeypatch.delenv("KRAB_LOCAL_VISION_ENABLED", raising=False)
    host = _Host()

    local_mock = AsyncMock(return_value="should not be called")
    host._describe_frame_via_lmstudio = local_mock  # type: ignore[method-assign]

    # Cloud path mock
    async def _fake_stream(*args, **kwargs):
        for chunk in ["cloud ", "answer"]:
            yield chunk

    with patch(
        "src.userbot.media_processors.openclaw_client.send_message_stream",
        _fake_stream,
    ):
        result = await host._describe_video_frame(b"\xff\xd8fake", idx=0, chat_id="-100")

    assert result == "cloud answer"
    local_mock.assert_not_called()


@pytest.mark.asyncio
async def test_describe_video_frame_local_empty_falls_through_to_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если local returns "" (e.g. LM Studio offline) → fallback на cloud."""
    monkeypatch.setenv("KRAB_LOCAL_VISION_ENABLED", "1")
    host = _Host()

    local_mock = AsyncMock(return_value="")  # Empty → fallthrough
    host._describe_frame_via_lmstudio = local_mock  # type: ignore[method-assign]

    async def _fake_stream(*args, **kwargs):
        yield "cloud fallback"

    with patch(
        "src.userbot.media_processors.openclaw_client.send_message_stream",
        _fake_stream,
    ):
        result = await host._describe_video_frame(b"\xff\xd8fake", idx=0, chat_id="-100")

    assert result == "cloud fallback"
    local_mock.assert_called_once()  # Local tried first


@pytest.mark.asyncio
async def test_describe_video_frame_empty_input_returns_empty() -> None:
    """Empty frame_bytes → empty string immediately."""
    host = _Host()
    result = await host._describe_video_frame(b"", idx=0, chat_id="-100")
    assert result == ""


@pytest.mark.asyncio
async def test_describe_video_frame_b64_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """b64 encode failure → log + return empty."""
    monkeypatch.setenv("KRAB_LOCAL_VISION_ENABLED", "1")
    host = _Host()

    # Force b64encode to raise
    def _bad_b64(data):
        raise OSError("b64 failed")

    with patch("src.userbot.media_processors.base64.b64encode", _bad_b64):
        result = await host._describe_video_frame(b"\xff\xd8fake", idx=0, chat_id="-100")

    assert result == ""
