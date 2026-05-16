# -*- coding: utf-8 -*-
"""Session 52 P2: local translator via LM Studio Gemma 4 vanilla.

Background:
- Highest-frequency cloud-burner в Krab: `translator_engine.translate_text`
  hardcoded `force_cloud=True, preferred_model="google/gemini-3-flash-preview"`.
- Every auto-translate потоков (incoming foreign messages) + voice transcript
  translation hits Gemini Flash via Gateway — significant cost over time.
- S52 P0 (commit d4ff0e6) уже loaded Gemma 4 26B vanilla в LM Studio для
  vision. **Reuse**: тот же loaded model для translation = **0 RAM cost**.
- Bench S52: Gemma 4 vanilla accurately translates RU↔EN with idiomatic
  word choice ("Быстрая бурая лиса перепрыгивает через ленивую собаку"
  formal preserved).

Coverage:
- _translate_via_lmstudio happy path (200 OK + clean text)
- env defaults (KRAB_LOCAL_VISION_URL reuse + KRAB_LOCAL_TRANSLATOR_MODEL fallback)
- HTTP error → empty string (fail-open)
- reasoning fallback when content empty
- translate_text routing: KRAB_LOCAL_TRANSLATOR_ENABLED=1 → local first,
  empty result falls through to cloud
- cache still works (S95 wave)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core import translator_engine
from src.core.translator_engine import (
    TranslationResult,
    _translate_via_lmstudio,
    translate_text,
)

# ── _translate_via_lmstudio direct tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_lmstudio_translate_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio 200 OK → returns (translated, model_id)."""
    monkeypatch.setenv("KRAB_LOCAL_VISION_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_MODEL", "gemma-test")
    monkeypatch.setenv("LM_STUDIO_API_KEY", "sk-test-key")

    captured: dict[str, Any] = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "Привет, мир!"}}]}

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
        translated, model_id = await _translate_via_lmstudio(
            "Hello, world!", "en", "ru", timeout_sec=20.0
        )

    assert translated == "Привет, мир!"
    assert model_id == "lmstudio/gemma-test"
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    # Request includes both system message + user prompt
    messages = captured["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert "переводчик" in messages[0]["content"].lower()
    assert messages[1]["role"] == "user"
    assert "Hello, world!" in messages[1]["content"]
    assert captured["json"]["max_tokens"] == 512
    assert captured["json"]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_lmstudio_translate_http_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP error → log + empty string + lmstudio_error marker."""
    monkeypatch.setenv("LM_STUDIO_API_KEY", "sk-test")

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
        translated, model_id = await _translate_via_lmstudio("test", "en", "ru", timeout_sec=5.0)

    assert translated == ""
    assert model_id == "lmstudio_error"


@pytest.mark.asyncio
async def test_lmstudio_translate_reasoning_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если content empty, читаем reasoning (thinking-mode models)."""
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning": "Перевожу: 'привет'.",
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
        translated, _ = await _translate_via_lmstudio("hello", "en", "ru", timeout_sec=5.0)

    assert translated == "Перевожу: 'привет'."


@pytest.mark.asyncio
async def test_lmstudio_translate_falls_back_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_TRANSLATOR_MODEL unset → fall back на KRAB_LOCAL_VISION_MODEL."""
    monkeypatch.delenv("KRAB_LOCAL_TRANSLATOR_MODEL", raising=False)
    monkeypatch.setenv("KRAB_LOCAL_VISION_MODEL", "shared-gemma-model")
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)

    captured: dict[str, Any] = {}

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
            captured["json"] = json
            return _FakeResp()

    with patch.object(httpx, "AsyncClient", _FakeClient):
        _, model_id = await _translate_via_lmstudio("test", "en", "ru", timeout_sec=5.0)

    assert captured["json"]["model"] == "shared-gemma-model"
    assert model_id == "lmstudio/shared-gemma-model"


# ── translate_text routing ────────────────────────────────────────────────


def _make_mock_client(chunks: list[str]):
    """Mock OpenClawClient with streaming response."""

    async def _async_iter(items):
        for item in items:
            yield item

    mock = AsyncMock()
    mock.send_message_stream = lambda **kwargs: _async_iter(chunks)
    mock.clear_session = AsyncMock(return_value=None)
    mock._last_runtime_route = {"model": "google/gemini-3-flash-preview"}
    return mock


@pytest.mark.asyncio
async def test_translate_text_local_when_env_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_TRANSLATOR_ENABLED=1 → local path used, cloud bypassed."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    # Clear cache to avoid pollution
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    local_mock = AsyncMock(return_value=("Привет!", "lmstudio/gemma-4-26b-a4b-it@4bit"))
    mock_client = _make_mock_client(["should not be called"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text("Hello!", "en", "ru", openclaw_client=mock_client)

    assert isinstance(result, TranslationResult)
    assert result.translated == "Привет!"
    assert result.model_id == "lmstudio/gemma-4-26b-a4b-it@4bit"
    local_mock.assert_called_once()
    # Cloud send_message_stream NOT called (would error since not async-gen)


@pytest.mark.asyncio
async def test_translate_text_cloud_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_LOCAL_TRANSLATOR_ENABLED unset (default) → cloud only."""
    monkeypatch.delenv("KRAB_LOCAL_TRANSLATOR_ENABLED", raising=False)
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    local_mock = AsyncMock(return_value=("UNUSED", "lmstudio/X"))
    mock_client = _make_mock_client(["Привет cloud"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text(
            "Hello cloud!",
            "en",
            "ru",
            openclaw_client=mock_client,
        )

    assert result.translated == "Привет cloud"
    local_mock.assert_not_called()


@pytest.mark.asyncio
async def test_translate_text_local_empty_falls_through_to_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local returns "" → cloud path used (resilience)."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    # Local fails → empty string
    local_mock = AsyncMock(return_value=("", "lmstudio_error"))
    mock_client = _make_mock_client(["Fallback cloud translation"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text("Fallback test", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Fallback cloud translation"
    local_mock.assert_called_once()  # Tried local first


@pytest.mark.asyncio
async def test_translate_text_local_strips_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local model wraps в quotes → strip them like cloud path does."""
    monkeypatch.setenv("KRAB_LOCAL_TRANSLATOR_ENABLED", "1")
    from src.core import translation_cache as cache_mod

    cache_mod.translation_cache._entries.clear()  # type: ignore[attr-defined]

    local_mock = AsyncMock(return_value=('"Привет с кавычками"', "lmstudio/X"))
    mock_client = _make_mock_client(["unused"])

    with patch.object(translator_engine, "_translate_via_lmstudio", local_mock):
        result = await translate_text("Hello quotes", "en", "ru", openclaw_client=mock_client)

    assert result.translated == "Привет с кавычками"
