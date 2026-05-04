# -*- coding: utf-8 -*-
"""Wave 20-A: end-to-end integration tests для Google direct bypass.

Защищает от регрессий типа Wave 18-G (silent ImportError прятал bypass 6 часов):
- Bypass должен engage для google/* моделей
- Условия: enabled, not has_photo, is_google_model(attempt_model)
- google.genai мокируется на уровне Client.models.generate_content
- Verify: bypass был вызван, response получен, openclaw НЕ вызывался (или был fallback)

Тестирование идёт через реальный send_message_stream с моками на уровне SDK,
а не через openclaw_client_once — так мы проверяем весь route path включая
Wave 18-D (bypass проверяется КАЖДЫЙ attempt, а не только initial),
Wave 18-E (_has_photo_bypass определён в scope), Wave 18-G (import fix).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.openclaw_client import OpenClawClient

# ---------------------------------------------------------------------------
# Helpers для мокирования google.genai Client
# ---------------------------------------------------------------------------


def _make_google_mock(text: str = "Привет!") -> tuple[MagicMock, MagicMock]:
    """Создаёт mock google.genai.Client, возвращающий text в ответе."""
    mock_response = MagicMock()
    mock_response.text = text

    # usage_metadata с нормальными счётчиками
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 5
    mock_usage.candidates_token_count = 3
    mock_usage.thoughts_token_count = 0
    mock_response.usage_metadata = mock_usage

    # candidates для fallback extraction
    mock_part = MagicMock()
    mock_part.text = text
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_response.candidates = [mock_candidate]

    # Client().models.generate_content returns mock_response
    mock_models = MagicMock()
    mock_models.generate_content = MagicMock(return_value=mock_response)
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    mock_client_class = MagicMock()
    mock_client_class.return_value = mock_client_instance

    return mock_client_class, mock_response


# ---------------------------------------------------------------------------
# Test 1: bypass engage для google/* в цепочке
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_engages_for_google_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full flow: send_message_stream с preferred_model=google/* → bypass engages.

    Проверяет Wave 18-G fix: import `from .integrations.google_genai_direct` должен
    работать (relative одна точка). При успехе — google.genai.Client вызван,
    ответ из bypass ('Привет!') попал в chunks.
    """
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "test-free-key-stub")

    mock_client_class, _ = _make_google_mock("Привет от Google Direct!")

    # Патчим google.genai.Client на уровне google_genai_direct модуля
    with patch("google.genai.Client", mock_client_class):
        # _openclaw_completion_once не должен вызываться если bypass succeed
        # Но если bypass вернёт пустую строку — fallback в openclaw, поэтому
        # подстрахуемся AsyncMock на случай fallback
        with patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as mock_oc:
            mock_oc.return_value = "openclaw fallback response"

            client = OpenClawClient()
            chunks: list[str] = []
            async for chunk in client.send_message_stream(
                message="привет",
                chat_id="test-bypass-engage-1",
                preferred_model="google/gemini-3-pro-preview",
            ):
                chunks.append(chunk)

    # Bypass должен был вызвать Google Client
    assert mock_client_class.call_count >= 1, (
        f"google.genai.Client должен быть вызван (bypass engaged). "
        f"Вызовов: {mock_client_class.call_count}. chunks={chunks}"
    )

    full_text = "".join(chunks)
    assert full_text.strip(), f"Ожидался непустой ответ, получили: {chunks!r}"

    # Если bypass отработал — openclaw НЕ должен быть вызван
    # (bypass returns early через `yield _direct_text; return`)
    if "Привет от Google Direct!" in full_text:
        assert mock_oc.call_count == 0, (
            "Bypass succeeded → openclaw НЕ должен быть вызван, "
            f"но mock_oc.call_count={mock_oc.call_count}"
        )


# ---------------------------------------------------------------------------
# Test 2: bypass skip для non-google модели
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_skipped_for_non_google_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """openai/* модель → bypass НЕ должен engage, google.genai.Client не вызван.

    Проверяет _is_google_model('openai/gpt-5.5') == False → bypass skip.
    """
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")

    mock_client_class, _ = _make_google_mock("не должен быть вызван")

    with patch("google.genai.Client", mock_client_class):
        with patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as mock_oc:
            # OpenClaw возвращает ответ напрямую (не google bypass)
            mock_oc.return_value = "openai response via openclaw"

            client = OpenClawClient()
            chunks: list[str] = []
            try:
                async for chunk in client.send_message_stream(
                    message="hi",
                    chat_id="test-bypass-skip-non-google-2",
                    preferred_model="openai/gpt-5.5",
                ):
                    chunks.append(chunk)
            except Exception:
                # OpenClaw может упасть с другими ошибками в тест окружении
                pass

    # Google Client должен быть вызван 0 раз — bypass skip для openai/*
    assert mock_client_class.call_count == 0, (
        f"Bypass НЕ должен engage для openai/gpt-5.5. "
        f"Google Client вызовов: {mock_client_class.call_count}"
    )


# ---------------------------------------------------------------------------
# Test 3: bypass skip когда disabled via env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_skipped_when_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=0 → bypass disabled даже для google/*.

    Проверяет is_google_direct_enabled() returns False → bypass gate не проходит.
    """
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "0")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "test-free-key-stub")

    mock_client_class, _ = _make_google_mock("не должен быть вызван")

    with patch("google.genai.Client", mock_client_class):
        with patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as mock_oc:
            # OpenClaw тоже падает чтобы не маскировать bypass call
            mock_oc.side_effect = RuntimeError("openclaw mocked failure")

            client = OpenClawClient()
            try:
                async for _ in client.send_message_stream(
                    message="hi",
                    chat_id="test-bypass-disabled-3",
                    preferred_model="google/gemini-3-pro-preview",
                ):
                    pass
            except Exception:
                # Ожидаемо: openclaw fallback тоже упал (mocked)
                pass

    # Bypass disabled → google Client НЕ должен быть вызван
    assert mock_client_class.call_count == 0, (
        f"Bypass disabled (KRAB_GOOGLE_DIRECT_BYPASS_ENABLED=0) → "
        f"google.genai.Client НЕ должен вызываться. "
        f"Вызовов: {mock_client_class.call_count}"
    )


# ---------------------------------------------------------------------------
# Test 4: bypass skip при has_photo=True (images переданы)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_skipped_with_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """images=['base64...'] → _has_photo_bypass=True → bypass skip.

    Wave 18-E fix: `_has_photo_bypass = bool(images)` явно определён в scope.
    До этого bypass попадал в except NameError silent — этот тест защищает
    от возврата к тому behaviour.
    """
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "test-free-key-stub")

    mock_client_class, _ = _make_google_mock("не должен быть вызван для multimodal")

    with patch("google.genai.Client", mock_client_class):
        with patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as mock_oc:
            mock_oc.side_effect = RuntimeError("openclaw mocked failure — expected")

            client = OpenClawClient()
            try:
                async for _ in client.send_message_stream(
                    message="что на фото?",
                    chat_id="test-bypass-multimodal-4",
                    preferred_model="google/gemini-3-pro-preview",
                    images=["data:image/png;base64,iVBORw0KGgo="],  # multimodal
                ):
                    pass
            except Exception:
                # Fallback тоже упал — ОК для этого теста
                pass

    # Multimodal → _has_photo_bypass=True → bypass skip
    assert mock_client_class.call_count == 0, (
        f"Multimodal запрос (images!=None) → bypass skip (_has_photo_bypass=True). "
        f"Google Client вызовов: {mock_client_class.call_count}"
    )


# ---------------------------------------------------------------------------
# Test 5: bypass empty response → fallback на OpenClaw (Wave 18-I path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_empty_response_falls_back_to_openclaw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google direct returns '' (empty) → bypass NOT yielded → fallback в openclaw.

    Wave 18-I path: google response.text='' (thinking съело весь output budget) →
    retry с thinking_budget=0 → тоже '' → bypass skip → openclaw called.
    """
    monkeypatch.setenv("KRAB_GOOGLE_DIRECT_BYPASS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY_FREE", "test-free-key-stub")

    # Google SDK возвращает пустой ответ (оба вызова — initial + thinking retry)
    mock_response_empty = MagicMock()
    mock_response_empty.text = ""  # empty — bypass не yield'ит
    mock_response_empty.candidates = []

    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 5
    mock_usage.candidates_token_count = 0
    mock_usage.thoughts_token_count = 1500  # thinking съело токены
    mock_response_empty.usage_metadata = mock_usage

    mock_models = MagicMock()
    # Обе попытки возвращают пустоту
    mock_models.generate_content = MagicMock(return_value=mock_response_empty)
    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_models

    mock_client_class = MagicMock()
    mock_client_class.return_value = mock_client_instance

    with patch("google.genai.Client", mock_client_class):
        with patch.object(
            OpenClawClient,
            "_openclaw_completion_once",
            new_callable=AsyncMock,
        ) as mock_oc:
            mock_oc.return_value = "openclaw fallback worked after empty bypass"

            client = OpenClawClient()
            chunks: list[str] = []
            async for chunk in client.send_message_stream(
                message="ping",
                chat_id="test-bypass-empty-fallback-5",
                preferred_model="google/gemini-3-pro-preview",
            ):
                chunks.append(chunk)

    # Bypass был вызван (google.genai.Client вызван)
    assert mock_client_class.call_count >= 1, (
        f"Bypass должен попробовать (Google Client вызван). Вызовов: {mock_client_class.call_count}"
    )

    # После empty response → fallback в openclaw
    assert mock_oc.call_count >= 1, (
        f"OpenClaw fallback должен сработать после empty bypass response. "
        f"mock_oc.call_count={mock_oc.call_count}"
    )

    full_text = "".join(chunks)
    assert "openclaw fallback worked" in full_text, (
        f"Ожидали openclaw fallback текст в chunks: {chunks!r}"
    )
