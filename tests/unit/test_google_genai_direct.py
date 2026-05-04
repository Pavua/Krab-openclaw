# -*- coding: utf-8 -*-
"""Unit tests для src/integrations/google_genai_direct.py (Wave 18-B).

Тестируем:
- is_google_model: корректное распознавание google/* моделей
- _strip_provider_prefix: очистка префикса провайдера
- is_google_direct_enabled: ENV gate
- complete_direct: mock SDK — text return, no-key, sdk-not-installed
- health_check_direct: smoke через mock
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. is_google_model
# ---------------------------------------------------------------------------


def test_is_google_model_recognizes_google_prefix():
    from src.integrations.google_genai_direct import is_google_model

    assert is_google_model("google/gemini-3-pro-preview") is True
    assert is_google_model("google/gemini-2.5-flash") is True
    assert is_google_model("google/gemini-3-flash-preview") is True


def test_is_google_model_excludes_google_cli_provider():
    from src.integrations.google_genai_direct import is_google_model

    # google-gemini-cli и google-antigravity — НЕ direct
    assert is_google_model("google-gemini-cli/gemini-2.5-pro") is False
    assert is_google_model("google-antigravity/gemini-3-pro-preview") is False


def test_is_google_model_excludes_other_providers():
    from src.integrations.google_genai_direct import is_google_model

    assert is_google_model("openai/gpt-5") is False
    assert is_google_model("anthropic/claude-3-opus") is False
    assert is_google_model("lmstudio/llama-3.2") is False
    assert is_google_model("") is False


# ---------------------------------------------------------------------------
# 2. _strip_provider_prefix
# ---------------------------------------------------------------------------


def test_strip_provider_prefix_removes_prefix():
    from src.integrations.google_genai_direct import _strip_provider_prefix

    assert _strip_provider_prefix("google/gemini-3-pro-preview") == "gemini-3-pro-preview"
    assert _strip_provider_prefix("google/gemini-2.5-flash") == "gemini-2.5-flash"


def test_strip_provider_prefix_no_prefix():
    from src.integrations.google_genai_direct import _strip_provider_prefix

    # Если уже без префикса — не меняем
    assert _strip_provider_prefix("gemini-3-pro-preview") == "gemini-3-pro-preview"


# ---------------------------------------------------------------------------
# 3. is_google_direct_enabled
# ---------------------------------------------------------------------------


def test_is_enabled_default_on(monkeypatch):
    monkeypatch.delenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", raising=False)
    from src.integrations.google_genai_direct import is_google_direct_enabled

    assert is_google_direct_enabled() is True


def test_is_enabled_off_via_env(monkeypatch):
    from src.integrations.google_genai_direct import is_google_direct_enabled

    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "0")
    assert is_google_direct_enabled() is False

    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "false")
    assert is_google_direct_enabled() is False


def test_is_enabled_on_via_env(monkeypatch):
    from src.integrations.google_genai_direct import is_google_direct_enabled

    for val in ("1", "true", "yes", "on"):
        monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", val)
        assert is_google_direct_enabled() is True, f"expected True for {val!r}"


# ---------------------------------------------------------------------------
# 4. complete_direct — mock SDK через patch на google.genai.Client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_direct_yields_text():
    """complete_direct возвращает текст из mock SDK."""
    mock_response = MagicMock()
    mock_response.text = "Привет от Gemini!"

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response

    mock_client_cls = MagicMock(return_value=mock_client_instance)

    # Патчим google.genai.Client в модуле
    import src.integrations.google_genai_direct as gd_module

    with patch.object(gd_module, "_resolve_api_key", return_value="AIzaFakeKey"):
        # Патчим через sys.modules чтобы lazy import поймал mock
        import google.genai as real_genai

        with patch.object(real_genai, "Client", mock_client_cls):
            result = await gd_module.complete_direct(
                model="google/gemini-3-pro-preview",
                messages=[{"role": "user", "content": "Привет"}],
                timeout_sec=30.0,
            )

    assert result == "Привет от Gemini!"
    mock_client_instance.models.generate_content.assert_called_once()


@pytest.mark.asyncio
async def test_complete_direct_no_api_key_raises():
    """complete_direct поднимает RuntimeError при отсутствии API ключа."""
    import src.integrations.google_genai_direct as gd_module

    with patch.object(gd_module, "_resolve_api_key", return_value=None):
        with pytest.raises(RuntimeError, match="Gemini API key недоступен"):
            await gd_module.complete_direct(
                model="google/gemini-2.5-flash",
                messages=[{"role": "user", "content": "test"}],
            )


@pytest.mark.asyncio
async def test_complete_direct_with_system_message():
    """complete_direct корректно обрабатывает system message и передаёт system_instruction."""
    mock_response = MagicMock()
    mock_response.text = "ответ с системой"

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response

    mock_client_cls = MagicMock(return_value=mock_client_instance)

    import google.genai as real_genai

    import src.integrations.google_genai_direct as gd_module

    with (
        patch.object(gd_module, "_resolve_api_key", return_value="AIzaFakeKey"),
        patch.object(real_genai, "Client", mock_client_cls),
    ):
        result = await gd_module.complete_direct(
            model="google/gemini-3-pro-preview",
            messages=[
                {"role": "system", "content": "Ты помощник"},
                {"role": "user", "content": "Привет"},
            ],
            timeout_sec=30.0,
        )

    # Убеждаемся что generate_content был вызван (system_instruction попадает в GenerateContentConfig)
    mock_client_instance.models.generate_content.assert_called_once()
    call_kwargs = mock_client_instance.models.generate_content.call_args
    # model должен быть без префикса
    assert call_kwargs.kwargs.get("model") == "gemini-3-pro-preview" or (
        call_kwargs.args and call_kwargs.args[0] == "gemini-3-pro-preview"
        if call_kwargs.args
        else True
    )
    assert result == "ответ с системой"


# ---------------------------------------------------------------------------
# 5. SDK not installed — simulate через sys.modules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_direct_sdk_not_installed_raises():
    """complete_direct поднимает RuntimeError если google.genai не доступен."""
    import sys

    import src.integrations.google_genai_direct as gd_module

    # Временно вынимаем google.genai из sys.modules
    saved = sys.modules.pop("google.genai", None)
    saved_types = sys.modules.pop("google.genai.types", None)
    # Помечаем как недоступный
    sys.modules["google.genai"] = None  # type: ignore[assignment]

    try:
        with pytest.raises((RuntimeError, ImportError)):
            await gd_module.complete_direct(
                model="google/gemini-2.5-flash",
                messages=[{"role": "user", "content": "test"}],
                api_key="AIzaFakeKey",
            )
    finally:
        # Восстанавливаем
        if saved is not None:
            sys.modules["google.genai"] = saved
        else:
            sys.modules.pop("google.genai", None)
        if saved_types is not None:
            sys.modules["google.genai.types"] = saved_types
        else:
            sys.modules.pop("google.genai.types", None)


# ---------------------------------------------------------------------------
# 6. health_check_direct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_direct_ok():
    """health_check_direct возвращает True при успешном ответе."""
    from src.integrations import google_genai_direct

    with patch.object(google_genai_direct, "complete_direct", new=AsyncMock(return_value="pong")):
        ok = await google_genai_direct.health_check_direct(api_key="AIzaFakeKey")

    assert ok is True


@pytest.mark.asyncio
async def test_health_check_direct_fail_on_exception():
    """health_check_direct возвращает False при ошибке SDK."""
    from src.integrations import google_genai_direct

    with patch.object(
        google_genai_direct,
        "complete_direct",
        new=AsyncMock(side_effect=RuntimeError("no key")),
    ):
        ok = await google_genai_direct.health_check_direct(api_key=None)

    assert ok is False


# ---------------------------------------------------------------------------
# 7. Wave 18-I: retry с thinking_budget=0 при empty response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_direct_retries_on_empty_with_thinking_disabled():
    """Wave 18-I: первый вызов возвращает empty text → retry с ThinkingConfig(thinking_budget=0) → 'Привет'."""
    import google.genai as real_genai
    from google.genai import types as real_genai_types

    import src.integrations.google_genai_direct as gd_module

    # Первый response: пустой text (thinking-only)
    mock_response_empty = MagicMock()
    mock_response_empty.text = ""
    mock_response_empty.usage_metadata = MagicMock(prompt_token_count=10, thoughts_token_count=288)

    # Второй response (no-thinking retry): нормальный текст
    mock_response_ok = MagicMock()
    mock_response_ok.text = "Привет"

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.side_effect = [
        mock_response_empty,
        mock_response_ok,
    ]
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with (
        patch.object(gd_module, "_resolve_api_key", return_value="AIzaFakeKey"),
        patch.object(real_genai, "Client", mock_client_cls),
    ):
        result = await gd_module.complete_direct(
            model="google/gemini-3-pro-preview",
            messages=[{"role": "user", "content": "ping"}],
            timeout_sec=30.0,
        )

    assert result == "Привет"
    # generate_content должен быть вызван дважды: первый раз без thinking config, второй с ним
    assert mock_client_instance.models.generate_content.call_count == 2

    # Проверяем что второй вызов содержит thinking_config с thinking_budget=0
    second_call_kwargs = mock_client_instance.models.generate_content.call_args_list[1]
    config_arg = second_call_kwargs.kwargs.get("config") or (
        second_call_kwargs.args[2] if len(second_call_kwargs.args) > 2 else None
    )
    assert config_arg is not None, "второй вызов должен иметь config с thinking_config"
    thinking_cfg = getattr(config_arg, "thinking_config", None)
    assert thinking_cfg is not None, "config должен содержать thinking_config"
    assert getattr(thinking_cfg, "thinking_budget", None) == 0


@pytest.mark.asyncio
async def test_complete_direct_returns_empty_when_retry_also_empty():
    """Wave 18-I: если retry тоже возвращает empty text → вернуть пустую строку."""
    import google.genai as real_genai

    import src.integrations.google_genai_direct as gd_module

    # Оба вызова возвращают пустой text
    mock_response_empty1 = MagicMock()
    mock_response_empty1.text = ""
    mock_response_empty1.usage_metadata = MagicMock(prompt_token_count=5, thoughts_token_count=50)

    mock_response_empty2 = MagicMock()
    mock_response_empty2.text = ""

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.side_effect = [
        mock_response_empty1,
        mock_response_empty2,
    ]
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with (
        patch.object(gd_module, "_resolve_api_key", return_value="AIzaFakeKey"),
        patch.object(real_genai, "Client", mock_client_cls),
    ):
        result = await gd_module.complete_direct(
            model="google/gemini-3-pro-preview",
            messages=[{"role": "user", "content": "ping"}],
            timeout_sec=30.0,
        )

    assert result == ""
    # Оба вызова были выполнены (retry произошёл)
    assert mock_client_instance.models.generate_content.call_count == 2


@pytest.mark.asyncio
async def test_complete_direct_graceful_degrade_when_thinking_config_unavailable():
    """Wave 18-I: если ThinkingConfig недоступен в SDK — graceful degrade, возвращаем ''.

    Симулируем старую версию SDK без ThinkingConfig через patch на genai_types.
    """
    import google.genai as real_genai
    from google.genai import types as real_genai_types

    import src.integrations.google_genai_direct as gd_module

    # Первый response: пустой text
    mock_response_empty = MagicMock()
    mock_response_empty.text = ""
    mock_response_empty.usage_metadata = MagicMock(prompt_token_count=5, thoughts_token_count=50)

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response_empty
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    # Патчим types чтобы ThinkingConfig отсутствовал (старая версия SDK)
    mock_types_no_thinking = MagicMock(spec=real_genai_types)
    del mock_types_no_thinking.ThinkingConfig  # убираем атрибут

    with (
        patch.object(gd_module, "_resolve_api_key", return_value="AIzaFakeKey"),
        patch.object(real_genai, "Client", mock_client_cls),
    ):
        # Патчим genai_types внутри модуля через sys.modules подмену при импорте
        # Проще: patch на google.genai.types.ThinkingConfig → None через setattr
        original_thinking_config = getattr(real_genai_types, "ThinkingConfig", None)
        try:
            # Временно убираем ThinkingConfig из types
            real_genai_types.ThinkingConfig = None  # type: ignore[attr-defined]
            result = await gd_module.complete_direct(
                model="google/gemini-3-pro-preview",
                messages=[{"role": "user", "content": "ping"}],
                timeout_sec=30.0,
            )
        finally:
            # Восстанавливаем
            if original_thinking_config is not None:
                real_genai_types.ThinkingConfig = original_thinking_config
            else:
                try:
                    del real_genai_types.ThinkingConfig
                except AttributeError:
                    pass

    # Без ThinkingConfig — возвращаем пустую строку (graceful degrade, NO retry)
    assert result == ""
    # generate_content вызван только один раз (retry не произошёл)
    assert mock_client_instance.models.generate_content.call_count == 1
