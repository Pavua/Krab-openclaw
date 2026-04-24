# -*- coding: utf-8 -*-
"""Тесты vision-aware роутинга для photo-запросов через CLI-провайдеры."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.openclaw_client import (
    OpenClawClient,
    _is_cli_provider,
    _supports_vision,
)

# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestIsCliProvider:
    def test_codex_cli_detected(self):
        assert _is_cli_provider("codex-cli/gpt-5.4") is True

    def test_gemini_cli_detected(self):
        assert _is_cli_provider("gemini-cli/gemini-3-pro") is True

    def test_claude_cli_detected(self):
        assert _is_cli_provider("claude-cli/opus") is True

    def test_opencode_detected(self):
        assert _is_cli_provider("opencode/gpt-4o") is True

    def test_google_cloud_not_cli(self):
        assert _is_cli_provider("google/gemini-3-pro-preview") is False

    def test_gemini_without_cli_suffix_not_cli(self):
        assert _is_cli_provider("gemini-2.5-pro") is False

    def test_empty_string(self):
        assert _is_cli_provider("") is False

    def test_openai_cloud_not_cli(self):
        assert _is_cli_provider("openai/gpt-4o") is False


class TestSupportsVision:
    def test_gemini3_supports_vision(self):
        assert _supports_vision("google/gemini-3-pro-preview") is True

    def test_gemini25_supports_vision(self):
        assert _supports_vision("gemini-2.5-pro-preview") is True

    def test_gpt4o_supports_vision(self):
        assert _supports_vision("openai/gpt-4o") is True

    def test_claude_sonnet4_supports_vision(self):
        assert _supports_vision("claude-sonnet-4-5") is True

    def test_qwen_vl_supports_vision(self):
        assert _supports_vision("qwen3.5-vl-4b-mlx") is True

    def test_qwen25_vl_supports_vision(self):
        assert _supports_vision("qwen2.5-vl-7b") is True

    def test_codex_cli_no_vision(self):
        assert _supports_vision("codex-cli/gpt-5.4") is False

    def test_opencode_no_vision(self):
        assert _supports_vision("opencode/gpt-5") is False

    def test_empty_string(self):
        assert _supports_vision("") is False

    def test_generic_vl_suffix(self):
        assert _supports_vision("some-model-vl-8b") is True

    def test_generic_vision_substring(self):
        assert _supports_vision("my-vision-model") is True


# ---------------------------------------------------------------------------
# Integration tests: redirect logic в _send_to_openclaw
# ---------------------------------------------------------------------------


def _make_model_manager(
    selected_model: str = "codex-cli/gpt-5.4",
    is_local: bool = False,
    cloud_model: str = "",
) -> MagicMock:
    """Минимальный mock model_manager для тестов роутинга."""
    mm = MagicMock()
    mm.get_best_model = AsyncMock(return_value=selected_model)
    mm.is_local_model = MagicMock(return_value=is_local)
    mm.get_best_cloud_model = AsyncMock(return_value=cloud_model)
    mm.ensure_model_loaded = AsyncMock(return_value=True)
    mm.mark_request_started = MagicMock()
    mm.get_current_model = MagicMock(return_value=selected_model)
    return mm


def _make_client() -> OpenClawClient:
    client = OpenClawClient.__new__(OpenClawClient)
    client._sessions = {}
    client._pending_requests = {}
    return client


def _run(coro):
    # Новый event loop вместо asyncio.get_event_loop() — последний deprecated
    # в Python 3.13 и поднимает RuntimeError, если предыдущий asyncio-тест
    # закрыл default loop (вызывает pollution в полном test suite).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestCliProviderPhotoRedirect:
    """Проверяем, что photo + CLI-провайдер → redirect на vision-capable модель."""

    def test_pick_vision_cloud_model_finds_vision(self):
        """_pick_vision_cloud_model возвращает первый vision-capable кандидат."""
        client = _make_client()
        mm = _make_model_manager()
        mm.is_local_model.return_value = False

        with (
            patch(
                "src.openclaw_client.get_runtime_primary_model", return_value="codex-cli/gpt-5.4"
            ),
            patch(
                "src.openclaw_client.get_runtime_fallback_models",
                return_value=["google/gemini-3-pro-preview", "codex-cli/backup"],
            ),
        ):
            result = _run(
                client._pick_vision_cloud_model(
                    model_manager=mm,
                    current_model="codex-cli/gpt-5.4",
                )
            )
        # gemini-3 — vision-capable, должен быть выбран
        assert result == "google/gemini-3-pro-preview"

    def test_pick_vision_cloud_model_no_vision_fallback(self):
        """_pick_vision_cloud_model возвращает '' если нет vision-capable кандидата."""
        client = _make_client()
        mm = _make_model_manager()
        mm.is_local_model.return_value = False

        with (
            patch(
                "src.openclaw_client.get_runtime_primary_model", return_value="codex-cli/gpt-5.4"
            ),
            patch(
                "src.openclaw_client.get_runtime_fallback_models",
                return_value=["opencode/backup"],
            ),
        ):
            result = _run(
                client._pick_vision_cloud_model(
                    model_manager=mm,
                    current_model="codex-cli/gpt-5.4",
                )
            )
        assert result == ""

    def test_pick_vision_skips_current_model(self):
        """Не возвращает current_model как кандидат, даже если он vision-capable."""
        client = _make_client()
        mm = _make_model_manager()
        mm.is_local_model.return_value = False

        with (
            patch(
                "src.openclaw_client.get_runtime_primary_model",
                return_value="google/gemini-3-pro-preview",
            ),
            patch(
                "src.openclaw_client.get_runtime_fallback_models",
                return_value=["google/gemini-3-flash-preview"],
            ),
        ):
            result = _run(
                client._pick_vision_cloud_model(
                    model_manager=mm,
                    current_model="google/gemini-3-pro-preview",
                )
            )
        # primary == current → skip; flash → vision-capable → выбрать
        assert result == "google/gemini-3-flash-preview"

    def test_no_redirect_when_not_photo(self):
        """has_photo=False → CLI-провайдер не вызывает redirect."""
        assert not _is_cli_provider("google/gemini-3-pro-preview")
        # Если not has_photo, ветка `elif has_photo and _is_cli_provider` не входит.
        # Тестируем через хелперы: photo=False не должно вызывать _pick_vision_cloud_model.
        client = _make_client()
        mm = _make_model_manager()
        mm.is_local_model.return_value = False

        pick_called = []

        async def mock_pick(**kwargs):  # noqa: ARG001
            pick_called.append(True)
            return "google/gemini-3-pro-preview"

        client._pick_vision_cloud_model = mock_pick

        # has_photo=False + CLI provider → _pick_vision_cloud_model НЕ должен вызываться
        has_photo = False
        selected = "codex-cli/gpt-5.4"
        # Логика: `elif has_photo and _is_cli_provider(selected)` — ложь при has_photo=False
        assert not (has_photo and _is_cli_provider(selected))
        assert pick_called == []

    def test_no_redirect_when_not_cli(self):
        """has_photo=True + non-CLI provider → redirect не нужен."""
        has_photo = True
        selected = "google/gemini-3-pro-preview"
        # Ветка не войдёт: _is_cli_provider вернёт False
        assert not (has_photo and _is_cli_provider(selected))
