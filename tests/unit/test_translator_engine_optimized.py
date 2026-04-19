# -*- coding: utf-8 -*-
"""
Тесты оптимизаций translator_engine: max_output_tokens=512 + pre-clear session.

Session 7: max_output_tokens снижен с 2048 до 512, добавлен clear_session ДО вызова.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.core.translator_engine import translate_text


@pytest.fixture()
def mock_openclaw_client() -> MagicMock:
    """Мок OpenClawClient с async stream и clear_session."""
    client = MagicMock()

    async def fake_stream(**kwargs):
        yield "Hola"
        yield " mundo"

    client.send_message_stream = MagicMock(side_effect=fake_stream)
    client.clear_session = MagicMock()
    client._last_runtime_route = {"model": "gemini-3-flash"}
    return client


@pytest.mark.asyncio
async def test_max_output_tokens_is_512(mock_openclaw_client: MagicMock) -> None:
    """send_message_stream вызывается с max_output_tokens=512 (не 2048)."""
    await translate_text(
        "Hello world",
        src_lang="en",
        tgt_lang="es",
        openclaw_client=mock_openclaw_client,
    )
    mock_openclaw_client.send_message_stream.assert_called_once()
    kwargs = mock_openclaw_client.send_message_stream.call_args
    # kwargs может быть в keyword args
    if kwargs.kwargs:
        assert kwargs.kwargs.get("max_output_tokens") == 512
    else:
        # позиционные — маловероятно, но проверим
        assert 512 in kwargs.args


@pytest.mark.asyncio
async def test_clear_session_called_twice(mock_openclaw_client: MagicMock) -> None:
    """clear_session вызывается 2 раза: до и после send_message_stream."""
    await translate_text(
        "Привет",
        src_lang="ru",
        tgt_lang="es",
        openclaw_client=mock_openclaw_client,
    )
    calls = mock_openclaw_client.clear_session.call_args_list
    assert len(calls) == 2, f"Ожидалось 2 вызова clear_session, получено {len(calls)}"
    # Оба вызова с одним и тем же chat_id
    assert calls[0] == call("translator_mvp")
    assert calls[1] == call("translator_mvp")


@pytest.mark.asyncio
async def test_pre_clear_happens_before_stream(mock_openclaw_client: MagicMock) -> None:
    """Первый clear_session вызывается ДО send_message_stream."""
    call_order: list[str] = []

    original_clear = mock_openclaw_client.clear_session

    def track_clear(chat_id):
        call_order.append("clear")
        return original_clear(chat_id)

    async def track_stream(**kwargs):
        call_order.append("stream")
        yield "resultado"

    mock_openclaw_client.clear_session = MagicMock(side_effect=track_clear)
    mock_openclaw_client.send_message_stream = MagicMock(side_effect=track_stream)

    await translate_text(
        "test",
        src_lang="en",
        tgt_lang="es",
        openclaw_client=mock_openclaw_client,
    )
    assert call_order[0] == "clear", "clear_session должен быть вызван ДО stream"
    assert call_order[1] == "stream"
    assert call_order[2] == "clear", "clear_session должен быть вызван И ПОСЛЕ stream"


@pytest.mark.asyncio
async def test_translation_result_correct(mock_openclaw_client: MagicMock) -> None:
    """Результат перевода собирается корректно из chunks."""
    result = await translate_text(
        "Hello world",
        src_lang="en",
        tgt_lang="es",
        openclaw_client=mock_openclaw_client,
    )
    assert result.translated == "Hola mundo"
    assert result.src_lang == "en"
    assert result.tgt_lang == "es"
    assert result.model_id == "gemini-3-flash"


@pytest.mark.asyncio
async def test_force_cloud_and_disable_tools(mock_openclaw_client: MagicMock) -> None:
    """Проверяем что force_cloud=True и disable_tools=True."""
    await translate_text(
        "test",
        src_lang="en",
        tgt_lang="ru",
        openclaw_client=mock_openclaw_client,
    )
    kwargs = mock_openclaw_client.send_message_stream.call_args.kwargs
    assert kwargs.get("force_cloud") is True
    assert kwargs.get("disable_tools") is True
