# -*- coding: utf-8 -*-
"""
Тесты vision-регрессии в DM owner (is_self=True) — W16.3 fix.

Root cause: при is_self=True _safe_edit(message, ...) возвращал Message без photo
(Pyrogram text-edit обнуляет photo в новом объекте). download_media вызывался
на уже-текстовом сообщении → photo_obj=None → LLM не видел изображение.

Fix: _photo_source_msg сохраняется ДО edit'а и передаётся в download_media.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_photo_message(is_self: bool, photo=None, text: str = ""):
    """Конструирует минимальный pyrogram-подобный Message с фото."""
    photo_obj = photo if photo is not None else object()
    user_id = 777 if is_self else 42
    username = "owner" if is_self else "tester"
    msg = SimpleNamespace(
        id=100,
        from_user=SimpleNamespace(id=user_id, username=username, is_bot=False),
        text=text,
        caption=None,
        photo=photo_obj,
        voice=None,
        document=None,
        chat=SimpleNamespace(id=999, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        outgoing=is_self,
    )
    # reply — async чтобы работало с _safe_reply_or_send_new
    msg.reply = AsyncMock(return_value=SimpleNamespace(
        id=101,
        text="",
        caption="",
        chat=SimpleNamespace(id=999),
        photo=None,
    ))
    return msg


def _make_bot(owner_id: int = 777):
    """Конструирует минимальный KraabUserbot без __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=owner_id, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()
    bot._session_messages_processed = 0
    return bot


# ---------------------------------------------------------------------------
# Тест 1: is_self=True, фото скачивается успешно — изображение попадает в LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_dm_photo_is_self_reaches_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    W16.3: при is_self=True (owner отправил фото в свой чат / Saved Messages)
    изображение должно дойти до LLM, невзирая на edit message в процессе.
    """
    fake_bytes = b"fake-jpeg-data"
    photo_io = SimpleNamespace(getvalue=lambda: fake_bytes)

    # message ПОСЛЕ edit — уже без фото (симуляция Pyrogram behavior)
    edited_msg = SimpleNamespace(
        id=100,
        text="🦀 \n\n👀 *Разглядываю фото...*",
        caption=None,
        photo=None,  # <-- key: edit обнулил photo
        chat=SimpleNamespace(id=999, type=enums.ChatType.PRIVATE),
    )

    incoming = _make_photo_message(is_self=True)
    original_photo = incoming.photo  # сохраняем ref для проверки

    bot = _make_bot()
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
    bot._get_chat_context = AsyncMock(return_value="")
    # _safe_edit симулирует Pyrogram: возвращает edited_msg (без фото)
    bot._safe_edit = AsyncMock(return_value=edited_msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="999")

    download_calls: list = []

    async def _fake_download(msg, in_memory=False):
        download_calls.append(msg)
        return photo_io

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_fake_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        captured["force_cloud"] = kwargs.get("force_cloud")
        yield "Описание изображения: фотография"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    # Проверяем: download_media вызван на ОРИГИНАЛЬНОМ сообщении с фото
    assert len(download_calls) == 1, "download_media должен быть вызван ровно один раз"
    assert getattr(download_calls[0], "photo", None) is original_photo, (
        "download_media должен вызываться на оригинальном сообщении с photo, "
        "а НЕ на edited_msg (где photo=None)"
    )

    # Проверяем: images попали в LLM-запрос
    assert len(captured.get("images", [])) == 1, "Изображение должно передаваться в LLM"
    b64_expected = base64.b64encode(fake_bytes).decode()
    assert captured["images"][0] == b64_expected, "base64 изображения должен совпадать"


# ---------------------------------------------------------------------------
# Тест 2: is_self=False (гость/owner в чужом чате) — старый путь не сломан
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_self_photo_edit_preserves_source_for_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    W16.3 регрессионный тест: _safe_edit возвращает другой Message (без photo).
    Проверяем что download_media вызывается на _photo_source_msg (до edit),
    а НЕ на post-edit Message с photo=None.
    """
    fake_bytes = b"pixels"
    photo_io = SimpleNamespace(getvalue=lambda: fake_bytes)

    incoming = _make_photo_message(is_self=True, text="")
    original_photo_obj = incoming.photo  # ref на оригинальный photo

    # Симуляция Pyrogram edit: возвращает msg с photo=None И с типом чата
    edited_msg_no_photo = SimpleNamespace(
        id=incoming.id,
        text="🦀 \n\n👀 *Разглядываю фото...*",
        caption=None,
        photo=None,  # Pyrogram text edit → photo атрибут исчезает
        chat=SimpleNamespace(id=999, type=enums.ChatType.PRIVATE),
    )

    bot = _make_bot()
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
    bot._get_chat_context = AsyncMock(return_value="")
    # Первый вызов _safe_edit — возвращаем edited_msg (без photo)
    bot._safe_edit = AsyncMock(return_value=edited_msg_no_photo)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="999")

    download_calls: list = []

    async def _fake_download(msg, in_memory=False):
        download_calls.append(msg)
        return photo_io

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=_fake_download,
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    captured: dict = {}

    async def _fake_stream(**kwargs):
        captured["images"] = kwargs.get("images", [])
        yield "result"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    # download_media вызван с исходным объектом (с photo), не с edited_msg_no_photo
    assert len(download_calls) == 1, "download_media должен быть вызван ровно один раз"
    assert getattr(download_calls[0], "photo", None) is original_photo_obj, (
        "download_media должен получить оригинальный msg до edit (где photo != None)"
    )
    assert len(captured.get("images", [])) == 1, "LLM должен получить изображение"


# ---------------------------------------------------------------------------
# Тест 3: download_media возвращает None — явный user-visible error, LLM не вызван
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_photo_download_none_returns_error_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Если download_media возвращает None (empty), LLM не запускается,
    пользователю приходит сообщение об ошибке.
    """
    incoming = _make_photo_message(is_self=True)

    bot = _make_bot()
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="999")

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(return_value=None),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    stream_called = []

    async def _fake_stream(**kwargs):
        stream_called.append(True)
        yield "should not reach"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    assert not stream_called, "LLM не должен вызываться при пустом photo_obj"
    # safe_edit вызван с ошибкой
    error_texts = [
        call.args[1]
        for call in bot._safe_edit.await_args_list
        if "удалось" in call.args[1] or "Фото" in call.args[1]
    ]
    assert error_texts, "Должно быть сообщение об ошибке"


# ---------------------------------------------------------------------------
# Тест 4: asyncio.TimeoutError — явный timeout-error, LLM не вызван
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_photo_download_timeout_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_media таймаут → LLM не вызывается."""
    incoming = _make_photo_message(is_self=True)

    bot = _make_bot()
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="999")

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(side_effect=asyncio.TimeoutError()),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    stream_called = []

    async def _fake_stream(**kwargs):
        stream_called.append(True)
        yield "should not reach"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)
    monkeypatch.setattr(userbot_bridge_module.config, "PHOTO_DOWNLOAD_TIMEOUT_SEC", 0.01, raising=False)

    await bot._process_message(incoming)

    assert not stream_called
    # Проверяем наличие timeout-текста в каком-либо _safe_edit
    timeout_texts = [
        call.args[1]
        for call in bot._safe_edit.await_args_list
        if "Таймаут" in call.args[1] or "таймаут" in call.args[1].lower()
    ]
    assert timeout_texts, "Сообщение о таймауте должно быть отправлено"


# ---------------------------------------------------------------------------
# Тест 5: Exception в download_media — graceful error, LLM не вызван
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_photo_download_exception_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_media кидает Exception → graceful error, no LLM."""
    incoming = _make_photo_message(is_self=True)

    bot = _make_bot()
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(return_value="")
    bot._get_chat_context = AsyncMock(return_value="")
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kw: kw["text"])
    bot._looks_like_model_status_question = Mock(return_value=False)
    bot._looks_like_runtime_truth_question = Mock(return_value=False)
    bot._looks_like_capability_status_question = Mock(return_value=False)
    bot._looks_like_commands_question = Mock(return_value=False)
    bot._looks_like_integrations_question = Mock(return_value=False)
    bot._build_system_prompt_for_sender = Mock(return_value="SYS")
    bot._build_runtime_chat_scope_id = Mock(return_value="999")

    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        download_media=AsyncMock(side_effect=OSError("network error")),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
    )

    stream_called = []

    async def _fake_stream(**kwargs):
        stream_called.append(True)
        yield "should not reach"

    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", _fake_stream)

    await bot._process_message(incoming)

    assert not stream_called
    # Должна быть ошибка в _safe_edit
    error_texts = [
        call.args[1]
        for call in bot._safe_edit.await_args_list
        if "Ошибка" in call.args[1] or "обработки" in call.args[1]
    ]
    assert error_texts, "Сообщение об ошибке обработки должно быть отправлено"
