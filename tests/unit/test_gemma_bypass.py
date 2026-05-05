# -*- coding: utf-8 -*-
"""Unit tests для Wave 25-E: Gemma fallback через AI Studio API (free tier).

Тестируем:
1. is_gemma_model() распознаёт gemma- prefix
2. is_gemma_model() отклоняет не-gemma модели
3. complete_via_genai_direct() работает для gemma model (mock SDK)
4. _strip_provider_prefix для gemma model возвращает as-is (нет provider/)
5. is_google_model() включает gemma модели (расширение Wave 25-E)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. is_gemma_model — распознаёт gemma- prefix
# ---------------------------------------------------------------------------


def test_is_gemma_model_recognizes_gemma_prefix():
    from src.integrations.google_genai_direct import is_gemma_model

    assert is_gemma_model("gemma-3-27b-it") is True
    assert is_gemma_model("gemma-3-12b-it") is True
    assert is_gemma_model("gemma-3-4b-it") is True
    assert is_gemma_model("gemma-2-9b-it") is True


# ---------------------------------------------------------------------------
# 2. is_gemma_model — отклоняет не-gemma модели
# ---------------------------------------------------------------------------


def test_is_gemma_model_rejects_non_gemma():
    from src.integrations.google_genai_direct import is_gemma_model

    # Gemini не Gemma
    assert is_gemma_model("gemini-3-pro-preview") is False
    assert is_gemma_model("google/gemini-3-pro-preview") is False
    # Другие провайдеры
    assert is_gemma_model("openai/gpt-5") is False
    assert is_gemma_model("anthropic/claude-opus") is False
    # Пустая строка
    assert is_gemma_model("") is False
    # Схожие, но не Gemma
    assert is_gemma_model("gemmapro") is False


# ---------------------------------------------------------------------------
# 3. complete_via_genai_direct работает для gemma model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_genai_direct_for_gemma():
    """complete_via_genai_direct (alias complete_direct) работает с gemma-* моделью."""
    mock_response = MagicMock()
    mock_response.text = "Gemma отвечает!"

    mock_client_instance = MagicMock()
    mock_client_instance.models.generate_content.return_value = mock_response

    mock_client_cls = MagicMock(return_value=mock_client_instance)

    import google.genai as real_genai

    import src.integrations.google_genai_direct as gd_module

    with (
        patch.object(gd_module, "_resolve_api_key", return_value="AIzaFakeKey"),
        patch.object(real_genai, "Client", mock_client_cls),
    ):
        result = await gd_module.complete_via_genai_direct(
            model="gemma-3-27b-it",
            messages=[{"role": "user", "content": "Привет Gemma"}],
            timeout_sec=30.0,
        )

    assert result == "Gemma отвечает!"
    # Проверяем что model_id передан без изменений (нет provider/ prefix у gemma)
    call_kwargs = mock_client_instance.models.generate_content.call_args
    called_model = call_kwargs.kwargs.get("model") or (
        call_kwargs.args[0] if call_kwargs.args else None
    )
    assert called_model == "gemma-3-27b-it"


# ---------------------------------------------------------------------------
# 4. _strip_provider_prefix для gemma model — возвращает as-is
# ---------------------------------------------------------------------------


def test_strip_provider_prefix_gemma_no_change():
    from src.integrations.google_genai_direct import _strip_provider_prefix

    # Gemma не имеет provider/ prefix — возвращается без изменений
    assert _strip_provider_prefix("gemma-3-27b-it") == "gemma-3-27b-it"
    assert _strip_provider_prefix("gemma-3-12b-it") == "gemma-3-12b-it"
    # Для сравнения: google/ strips'ится
    assert _strip_provider_prefix("google/gemini-3-pro-preview") == "gemini-3-pro-preview"


# ---------------------------------------------------------------------------
# 5. is_google_model() теперь включает gemma модели (Wave 25-E extension)
# ---------------------------------------------------------------------------


def test_is_google_model_includes_gemma():
    """is_google_model расширена: gemma- prefix тоже route'ится через AI Studio."""
    from src.integrations.google_genai_direct import is_google_model

    # Gemma модели теперь входят в google_model
    assert is_google_model("gemma-3-27b-it") is True
    assert is_google_model("gemma-3-12b-it") is True
    # Обычные google/* модели — по-прежнему True
    assert is_google_model("google/gemini-3-pro-preview") is True
    # Прочие провайдеры — False
    assert is_google_model("openai/gpt-5") is False
    assert is_google_model("google-antigravity/model") is False
    assert is_google_model("") is False
