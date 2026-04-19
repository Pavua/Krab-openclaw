# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_team_listener.py.

Покрывают:
- is_listeners_enabled / set_listeners_enabled
- _check_cooldown
- _is_owner
- _build_header / TEAM_EMOJI
- _trim_response
- _handle_team_message (owner-only фильтр, group mention фильтр, cooldown, disabled)
- _stream_reply (streaming, edit_text, error handling)
- register_team_message_handler
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.swarm_team_listener as stl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    text: str = "привет",
    from_user_id: int = 111,
    chat_type: str = "private",
    chat_id: int = 100,
    reply_to_user_id: int | None = None,
    caption: str | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.from_user = SimpleNamespace(id=from_user_id, username="owner")
    msg.chat = SimpleNamespace(type=chat_type, id=chat_id)

    if reply_to_user_id is not None:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.from_user = SimpleNamespace(id=reply_to_user_id)
    else:
        msg.reply_to_message = None

    msg.reply = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    return msg


def _make_client(me_id: int = 999, me_username: str = "teambot") -> MagicMock:
    client = MagicMock()
    client.me = SimpleNamespace(id=me_id, username=me_username)
    client.get_me = AsyncMock(return_value=client.me)
    client.send_chat_action = AsyncMock()
    client.on_message = MagicMock(return_value=lambda f: f)
    return client


async def _empty_stream():
    """Пустой async-генератор."""
    return
    yield  # noqa: unreachable


async def _chunk_stream(*chunks: str):
    """Генератор с заданными чанками."""
    for c in chunks:
        yield c


# ---------------------------------------------------------------------------
# Тесты флагов и cooldown
# ---------------------------------------------------------------------------


def test_listeners_enabled_default():
    stl.set_listeners_enabled(True)
    assert stl.is_listeners_enabled() is True


def test_listeners_disabled():
    stl.set_listeners_enabled(False)
    assert stl.is_listeners_enabled() is False
    stl.set_listeners_enabled(True)  # восстанавливаем


def test_check_cooldown_first_call():
    team = "traders_test_cdwn"
    chat_id = 9999001
    stl._last_reply.pop(f"{team}:{chat_id}", None)
    assert stl._check_cooldown(team, chat_id) is True


def test_check_cooldown_too_soon():
    team = "traders_test_soon"
    chat_id = 9999002
    stl._last_reply[f"{team}:{chat_id}"] = time.monotonic()
    assert stl._check_cooldown(team, chat_id) is False


def test_check_cooldown_after_wait():
    team = "traders_test_wait"
    chat_id = 9999003
    stl._last_reply[f"{team}:{chat_id}"] = time.monotonic() - stl._COOLDOWN_SEC - 1
    assert stl._check_cooldown(team, chat_id) is True


# ---------------------------------------------------------------------------
# Тесты TEAM_EMOJI и _build_header
# ---------------------------------------------------------------------------


def test_team_emoji_all_teams():
    for team in ["traders", "coders", "analysts", "creative"]:
        assert team in stl.TEAM_EMOJI


def test_build_header_contains_emoji():
    header = stl._build_header("traders")
    assert "📈" in header
    assert "Traders" in header


def test_build_header_unknown_team():
    header = stl._build_header("unknown_team")
    assert "🤖" in header


# ---------------------------------------------------------------------------
# Тесты _is_owner
# ---------------------------------------------------------------------------


def test_is_owner_true():
    with patch.object(stl.config, "OWNER_USER_IDS", ["12345", "67890"]):
        assert stl._is_owner(12345) is True


def test_is_owner_false():
    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        assert stl._is_owner(99999) is False


def test_is_owner_zero_id():
    assert stl._is_owner(0) is False


# ---------------------------------------------------------------------------
# Тесты _trim_response
# ---------------------------------------------------------------------------


def test_trim_response_short():
    text = "short"
    assert stl._trim_response(text) == text


def test_trim_response_long():
    text = "x" * 5000
    trimmed = stl._trim_response(text)
    assert len(trimmed) < 5000
    assert trimmed.endswith("...")


# ---------------------------------------------------------------------------
# Тесты _handle_team_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_ignores_when_disabled():
    stl.set_listeners_enabled(False)
    client = _make_client()
    msg = _make_message()
    openclaw = MagicMock()
    await stl._handle_team_message("traders", client, msg, openclaw)
    msg.reply.assert_not_called()
    stl.set_listeners_enabled(True)


@pytest.mark.asyncio
async def test_handle_ignores_own_message():
    stl.set_listeners_enabled(True)
    client = _make_client(me_id=111)
    msg = _make_message(from_user_id=111)  # sender == me
    openclaw = MagicMock()
    await stl._handle_team_message("traders", client, msg, openclaw)
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_handle_private_non_owner_ignored():
    stl.set_listeners_enabled(True)
    client = _make_client(me_id=999)
    msg = _make_message(from_user_id=55555, chat_type="private")
    openclaw = MagicMock()
    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        await stl._handle_team_message("traders", client, msg, openclaw)
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_handle_private_owner_gets_reply():
    stl.set_listeners_enabled(True)
    stl._last_reply.clear()

    client = _make_client(me_id=999)
    msg = _make_message(from_user_id=12345, chat_type="private", chat_id=777001)
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_chunk_stream("Привет!", " Как дела?"))

    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        await stl._handle_team_message("traders", client, msg, openclaw)

    msg.reply.assert_called_once()
    # финальный edit_text должен содержать ответ
    assert sent_mock.edit_text.called


@pytest.mark.asyncio
async def test_handle_group_no_mention_ignored():
    stl.set_listeners_enabled(True)
    client = _make_client(me_id=999, me_username="teambot")
    msg = _make_message(
        text="просто сообщение без mention",
        from_user_id=12345,
        chat_type="group",
        chat_id=888001,
    )
    msg.reply_to_message = None
    openclaw = MagicMock()
    await stl._handle_team_message("coders", client, msg, openclaw)
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_handle_group_mention_replies():
    stl.set_listeners_enabled(True)
    stl._last_reply.clear()

    client = _make_client(me_id=999, me_username="teambot")
    msg = _make_message(
        text="@teambot как дела?",
        from_user_id=12345,
        chat_type="group",
        chat_id=888002,
    )
    msg.reply_to_message = None
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_chunk_stream("Всё хорошо!"))

    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        await stl._handle_team_message("coders", client, msg, openclaw)

    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_handle_group_reply_to_me_replies():
    stl.set_listeners_enabled(True)
    stl._last_reply.clear()

    client = _make_client(me_id=999, me_username="teambot")
    msg = _make_message(
        text="продолжай",
        from_user_id=12345,
        chat_type="group",
        chat_id=888003,
        reply_to_user_id=999,  # reply к me
    )
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_chunk_stream("Ок!"))

    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        await stl._handle_team_message("analysts", client, msg, openclaw)

    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_handle_empty_text_ignored():
    stl.set_listeners_enabled(True)
    client = _make_client(me_id=999)
    msg = _make_message(text="", from_user_id=12345, chat_type="private")
    openclaw = MagicMock()
    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        await stl._handle_team_message("traders", client, msg, openclaw)
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_handle_cooldown_blocks_second_call():
    stl.set_listeners_enabled(True)
    stl._last_reply.clear()

    client = _make_client(me_id=999)
    msg1 = _make_message(from_user_id=12345, chat_type="private", chat_id=999001)
    msg2 = _make_message(from_user_id=12345, chat_type="private", chat_id=999001)

    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg1.reply = AsyncMock(return_value=sent_mock)
    msg2.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_chunk_stream("ok"))

    with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
        await stl._handle_team_message("creative", client, msg1, openclaw)
        await stl._handle_team_message("creative", client, msg2, openclaw)

    # Только первый вызов должен отправить reply
    assert msg1.reply.call_count == 1
    assert msg2.reply.call_count == 0


# ---------------------------------------------------------------------------
# Тесты _stream_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_reply_success():
    """Успешный стриминг: edit_text вызывается с финальным текстом."""
    client = _make_client()
    msg = _make_message(from_user_id=12345)
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_chunk_stream("Привет!", " Всё хорошо."))

    await stl._stream_reply("traders", client, msg, openclaw, "как дела?")

    msg.reply.assert_called_once_with(stl._THINKING_TEXT, quote=True)
    # Финальный edit_text содержит emoji и ответ
    last_call_args = sent_mock.edit_text.call_args_list[-1][0][0]
    assert "📈" in last_call_args
    assert "Привет!" in last_call_args


@pytest.mark.asyncio
async def test_stream_reply_empty_response():
    """Пустой поток — сообщение-заглушка меняется на ошибку."""
    client = _make_client()
    msg = _make_message()
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_empty_stream())

    await stl._stream_reply("coders", client, msg, openclaw, "привет")

    # Должно выдать сообщение об ошибке
    last_text = sent_mock.edit_text.call_args_list[-1][0][0]
    assert "⚠️" in last_text


@pytest.mark.asyncio
async def test_stream_reply_stream_exception():
    """Исключение в потоке — edit_text с ошибкой."""

    async def _bad_stream():
        yield "partial"
        raise RuntimeError("stream broke")

    client = _make_client()
    msg = _make_message()
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_bad_stream())

    await stl._stream_reply("analysts", client, msg, openclaw, "вопрос")

    last_text = sent_mock.edit_text.call_args_list[-1][0][0]
    assert "⚠️" in last_text


@pytest.mark.asyncio
async def test_stream_reply_send_fails():
    """Если reply() падает — функция молча завершается."""
    client = _make_client()
    msg = _make_message()
    msg.reply = AsyncMock(side_effect=Exception("cannot send"))

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_chunk_stream("ok"))

    # Не должно бросить исключение
    await stl._stream_reply("creative", client, msg, openclaw, "текст")


@pytest.mark.asyncio
async def test_stream_reply_long_response_trimmed():
    """Очень длинный ответ обрезается до _TG_MAX_LEN."""
    long_text = "x" * 5000

    async def _long_stream():
        yield long_text

    client = _make_client()
    msg = _make_message()
    sent_mock = MagicMock()
    sent_mock.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=sent_mock)

    openclaw = MagicMock()
    openclaw.send_message_stream = MagicMock(return_value=_long_stream())

    await stl._stream_reply("coders", client, msg, openclaw, "вопрос")

    last_text = sent_mock.edit_text.call_args_list[-1][0][0]
    assert len(last_text) <= stl._TG_MAX_LEN + 50  # с учётом header


# ---------------------------------------------------------------------------
# Тесты register_team_message_handler
# ---------------------------------------------------------------------------


def test_register_team_message_handler():
    """register_team_message_handler регистрирует обработчик без исключений."""
    client = _make_client()
    openclaw = MagicMock()
    # Не должно бросить исключение
    stl.register_team_message_handler("traders", client, openclaw)
    # on_message должен был вызваться
    assert client.on_message.called


def test_register_all_teams():
    """Регистрация всех команд без ошибок."""
    for team in ["traders", "coders", "analysts", "creative"]:
        client = _make_client()
        openclaw = MagicMock()
        stl.register_team_message_handler(team, client, openclaw)
        assert client.on_message.called


# ---------------------------------------------------------------------------
# Тесты session_id уникальности
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_teams_different_sessions():
    """Разные команды используют разные session_id."""
    stl.set_listeners_enabled(True)
    stl._last_reply.clear()

    captured_sessions: list[str] = []

    async def _mock_stream(message, chat_id, **kwargs):
        captured_sessions.append(chat_id)
        yield "ok"

    for team in ["traders", "coders"]:
        client = _make_client(me_id=999)
        msg = _make_message(from_user_id=12345, chat_type="private", chat_id=555000)
        sent_mock = MagicMock()
        sent_mock.edit_text = AsyncMock()
        msg.reply = AsyncMock(return_value=sent_mock)

        openclaw = MagicMock()
        openclaw.send_message_stream = _mock_stream

        stl._last_reply.clear()
        with patch.object(stl.config, "OWNER_USER_IDS", ["12345"]):
            await stl._handle_team_message(team, client, msg, openclaw)

    assert len(captured_sessions) == 2
    assert captured_sessions[0] != captured_sessions[1]
    assert "traders" in captured_sessions[0]
    assert "coders" in captured_sessions[1]
