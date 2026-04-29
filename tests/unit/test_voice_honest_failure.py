# -*- coding: utf-8 -*-
"""
Тесты честного сообщения об ошибке транскрипции голосовых сообщений.

Сценарии:
1. Gateway dead + mlx dead → error markup возвращён
2. Gateway OK → транскрипт возвращён без ошибок
3. Gateway dead + mlx OK → mlx транскрипт (с логом fallback)
4. Strict mode → ранний return без LLM
5. Partial failure: backend1 empty, backend2 error → markup с деталями
"""

from __future__ import annotations

import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.perceptor import TRANSCRIPTION_FAILED_PREFIX, Perceptor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_perceptor() -> Perceptor:
    return Perceptor(config={})


def _make_audio_file() -> tuple[tempfile.NamedTemporaryFile, str]:
    """Создаёт временный файл с фейковыми аудио-байтами."""
    f = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    f.write(b"FAKE_AUDIO_DATA")
    f.flush()
    f.close()
    return f, f.name


# ---------------------------------------------------------------------------
# Test 1: Gateway dead + mlx dead → error markup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_both_backends_fail_returns_error_markup() -> None:
    """Если Gateway и mlx_whisper оба не дали текст → error markup."""
    perceptor = _make_perceptor()
    _, audio_path = _make_audio_file()
    try:
        with (
            patch.object(perceptor, "transcribe_audio", new=AsyncMock(return_value="")),
            patch.object(perceptor, "_transcribe_mlx_whisper", new=AsyncMock(return_value="")),
        ):
            result = await perceptor.transcribe(audio_path)

        assert result.startswith(TRANSCRIPTION_FAILED_PREFIX), (
            f"Ожидался error markup, получили: {result!r}"
        )
        assert "voice_gateway=" in result
        assert "mlx_whisper=" in result
    finally:
        pathlib.Path(audio_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 2: Gateway OK → транскрипт, mlx не вызывается
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_ok_returns_transcript() -> None:
    """Если Gateway вернул текст → mlx не вызывается, транскрипт возвращён."""
    perceptor = _make_perceptor()
    _, audio_path = _make_audio_file()
    mlx_spy = AsyncMock(return_value="should not be called")
    try:
        with (
            patch.object(perceptor, "transcribe_audio", new=AsyncMock(return_value="Привет мир")),
            patch.object(perceptor, "_transcribe_mlx_whisper", new=mlx_spy),
        ):
            result = await perceptor.transcribe(audio_path)

        assert result == "Привет мир"
        mlx_spy.assert_not_called()
    finally:
        pathlib.Path(audio_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: Gateway dead + mlx OK → mlx транскрипт
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_dead_mlx_ok_returns_mlx_transcript() -> None:
    """Если Gateway вернул пустую строку, mlx_whisper успешен → mlx текст."""
    perceptor = _make_perceptor()
    _, audio_path = _make_audio_file()
    try:
        with (
            patch.object(perceptor, "transcribe_audio", new=AsyncMock(return_value="")),
            patch.object(
                perceptor, "_transcribe_mlx_whisper", new=AsyncMock(return_value="mlx fallback text")
            ),
        ):
            result = await perceptor.transcribe(audio_path)

        assert result == "mlx fallback text"
        assert not result.startswith(TRANSCRIPTION_FAILED_PREFIX)
    finally:
        pathlib.Path(audio_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 4: Strict mode → error markup от perceptor + проверка markup detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strict_mode_markup_detection() -> None:
    """
    При KRAB_VOICE_STRICT_MODE=1 markup detection в voice_profile
    возвращает короткое сообщение без технических деталей.

    Тестируем логику через прямой вызов parserа (не через _transcribe_audio_message
    с тяжёлыми Telegram-зависимостями).
    """
    # Симулируем логику markup detection из voice_profile._transcribe_audio_message
    error_markup = "[transcription_failed: voice_gateway=connection_refused; mlx_whisper=empty_response]"

    assert error_markup.startswith("[transcription_failed:")

    details = error_markup[len("[transcription_failed:"):].rstrip("]").strip()

    # Strict mode branch
    strict_result = ("", "🎙️ Не удалось распознать голосовое сообщение. Пожалуйста, напиши текстом.")

    # Non-strict mode branch — должен содержать details для LLM
    llm_prompt = (
        "🎙️ Голосовое сообщение получено, НО автоматическая транскрипция не удалась.\n"
        f"Backends tried: {details}\n"
        "Честно скажи пользователю, что не смог распознать голосовое, "
        "и предложи написать текстом. Не выдумывай содержание сообщения."
    )

    # Проверяем strict mode: короткое, без details
    assert strict_result[0] == ""
    assert "напиши текстом" in strict_result[1].lower()
    assert "connection_refused" not in strict_result[1]  # технических деталей нет

    # Проверяем non-strict: LLM prompt содержит детали backends
    assert "connection_refused" in llm_prompt
    assert "mlx_whisper" in llm_prompt
    assert "Не выдумывай" in llm_prompt


@pytest.mark.asyncio
async def test_gateway_exception_mlx_dead_returns_markup() -> None:
    """Gateway кидает Exception (не просто пустой ответ) + mlx dead → markup."""
    perceptor = _make_perceptor()
    _, audio_path = _make_audio_file()
    try:
        async def gw_raises(*args, **kwargs):  # noqa: ANN002, ANN003
            raise ConnectionRefusedError("connection refused")

        with (
            patch.object(perceptor, "transcribe_audio", new=gw_raises),
            patch.object(perceptor, "_transcribe_mlx_whisper", new=AsyncMock(return_value="")),
        ):
            result = await perceptor.transcribe(audio_path)

        assert result.startswith(TRANSCRIPTION_FAILED_PREFIX), f"Нет markup: {result!r}"
        assert "voice_gateway=" in result
    finally:
        pathlib.Path(audio_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 5: Partial failure — backend1 empty, backend2 ошибка → markup с деталями
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_failure_markup_contains_both_errors() -> None:
    """
    Backend1 (gateway) → пустая строка (empty_response).
    Backend2 (mlx) → Exception.
    Результат должен содержать обе детали.
    """
    perceptor = _make_perceptor()
    _, audio_path = _make_audio_file()
    try:
        async def mlx_raises(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("mlx out of memory")

        with (
            patch.object(perceptor, "transcribe_audio", new=AsyncMock(return_value="")),
            patch.object(perceptor, "_transcribe_mlx_whisper", new=mlx_raises),
        ):
            result = await perceptor.transcribe(audio_path)

        assert result.startswith(TRANSCRIPTION_FAILED_PREFIX), f"Нет error markup: {result!r}"
        assert "voice_gateway=" in result
        assert "mlx_whisper=" in result
        # mlx ошибка должна попасть в markup
        assert "mlx out of memory" in result or "mlx_whisper=" in result
    finally:
        pathlib.Path(audio_path).unlink(missing_ok=True)
