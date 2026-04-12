# -*- coding: utf-8 -*-
"""
Тесты команд !fwd и !collect — умный форвард без метки «Forwarded».

Покрываем:
1) !fwd <chat_id> в ответ на сообщение — copy_message вызывается корректно;
2) !fwd <chat_id> last N — копирует N последних сообщений в хронологическом порядке;
3) !fwd без аргументов — UserInputError;
4) !fwd с невалидным chat_id — UserInputError;
5) !fwd без reply (одиночный режим) — UserInputError;
6) !fwd last с невалидным N — UserInputError;
7) !fwd last N=0 — UserInputError;
8) !fwd last N>200 — UserInputError;
9) !fwd не-owner — UserInputError;
10) !fwd при ошибке pyrogram — ответ с ❌;
11) !fwd self-message — edit вместо reply;
12) !collect <chat_id> <N> — копирует сообщения из src в dst;
13) !collect без аргументов — UserInputError;
14) !collect с невалидным chat_id — UserInputError;
15) !collect с невалидным N — UserInputError;
16) !collect N=0 — UserInputError;
17) !collect N>100 — UserInputError;
18) !collect не-owner — UserInputError;
19) !collect пустой чат — ответ с 📭;
20) !collect частичный успех — ⚠️ сообщение;
21) !collect при ошибке get_chat_history — ответ с ❌;
22) !collect self-message — edit вместо reply;
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_fwd, handle_collect


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_bot(args: str = "", *, access_level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""

    async def _fake_get_chat_history(chat_id, limit=10):
        """Генератор: возвращает limit фейковых сообщений (newest-first, как Pyrogram)."""
        for i in range(limit, 0, -1):
            yield SimpleNamespace(id=i)

    bot = SimpleNamespace(
        me=SimpleNamespace(id=999),
        client=SimpleNamespace(
            copy_message=AsyncMock(return_value=SimpleNamespace(id=100)),
            get_chat_history=_fake_get_chat_history,
        ),
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(level=access_level, source="test"),
    )
    return bot


def _make_message(
    *,
    reply_to: SimpleNamespace | None = None,
    from_user_id: int = 1,
    chat_id: int = 100,
) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=from_user_id),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=reply_to,
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _make_reply_msg(msg_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(id=msg_id)


# ---------------------------------------------------------------------------
# handle_fwd — режим reply
# ---------------------------------------------------------------------------

class TestHandleFwdReply:
    @pytest.mark.asyncio
    async def test_fwd_reply_calls_copy_message(self) -> None:
        """!fwd в ответ на сообщение вызывает copy_message с правильными аргументами."""
        bot = _make_bot("200")
        target = _make_reply_msg(42)
        message = _make_message(reply_to=target, chat_id=100)

        await handle_fwd(bot, message)

        bot.client.copy_message.assert_awaited_once_with(200, 100, 42)

    @pytest.mark.asyncio
    async def test_fwd_reply_confirmation_text(self) -> None:
        """После успешного копирования ответ содержит подтверждение."""
        bot = _make_bot("200")
        message = _make_message(reply_to=_make_reply_msg())

        await handle_fwd(bot, message)

        text = message.reply.await_args.args[0]
        assert "📤" in text
        assert "200" in text

    @pytest.mark.asyncio
    async def test_fwd_no_reply_raises_user_input_error(self) -> None:
        """!fwd без reply_to и без last — UserInputError."""
        bot = _make_bot("200")
        message = _make_message(reply_to=None)

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

        bot.client.copy_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fwd_pyrogram_exception_returns_error(self) -> None:
        """При ошибке copy_message ответ содержит ❌."""
        bot = _make_bot("200")
        bot.client.copy_message = AsyncMock(side_effect=RuntimeError("Forbidden"))
        message = _make_message(reply_to=_make_reply_msg())

        await handle_fwd(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_fwd_uses_edit_for_self_message(self) -> None:
        """Если сообщение от самого бота — edit вместо reply."""
        bot = _make_bot("200")
        message = _make_message(reply_to=_make_reply_msg(), from_user_id=bot.me.id)

        await handle_fwd(bot, message)

        message.edit.assert_awaited_once()
        message.reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_fwd — режим last N
# ---------------------------------------------------------------------------

class TestHandleFwdLastN:
    @pytest.mark.asyncio
    async def test_fwd_last_copies_n_messages(self) -> None:
        """!fwd last N вызывает copy_message N раз."""
        bot = _make_bot("200 last 3")
        message = _make_message(chat_id=100)

        await handle_fwd(bot, message)

        assert bot.client.copy_message.await_count == 3

    @pytest.mark.asyncio
    async def test_fwd_last_copies_in_chronological_order(self) -> None:
        """Сообщения копируются в хронологическом порядке (oldest first)."""
        bot = _make_bot("200 last 3")
        message = _make_message(chat_id=100)

        await handle_fwd(bot, message)

        calls = bot.client.copy_message.call_args_list
        # get_chat_history даёт newest-first (id=3,2,1), reverse → oldest-first (id=1,2,3)
        msg_ids = [c.args[2] for c in calls]
        assert msg_ids == sorted(msg_ids)

    @pytest.mark.asyncio
    async def test_fwd_last_confirmation_text(self) -> None:
        """Ответ содержит количество скопированных сообщений."""
        bot = _make_bot("200 last 5")
        message = _make_message()

        await handle_fwd(bot, message)

        text = message.reply.await_args.args[0]
        assert "5" in text
        assert "📤" in text

    @pytest.mark.asyncio
    async def test_fwd_last_invalid_n_string(self) -> None:
        """!fwd <chat_id> last abc — UserInputError."""
        bot = _make_bot("200 last abc")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

    @pytest.mark.asyncio
    async def test_fwd_last_n_zero_raises(self) -> None:
        """!fwd last 0 — UserInputError."""
        bot = _make_bot("200 last 0")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

    @pytest.mark.asyncio
    async def test_fwd_last_n_too_large_raises(self) -> None:
        """!fwd last 201 — UserInputError."""
        bot = _make_bot("200 last 201")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

    @pytest.mark.asyncio
    async def test_fwd_last_n_boundary_200(self) -> None:
        """!fwd last 200 — граница, не должен бросать ошибку."""
        bot = _make_bot("200 last 200")
        message = _make_message(chat_id=100)

        # Не бросает UserInputError
        await handle_fwd(bot, message)

    @pytest.mark.asyncio
    async def test_fwd_last_partial_failure_shows_count(self) -> None:
        """Если часть copy_message падает — в тексте отражается реальный счётчик."""
        call_count = 0

        async def _copy_sometimes_fail(to_chat, from_chat, msg_id):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("fail")
            return SimpleNamespace(id=msg_id)

        bot = _make_bot("200 last 4")
        bot.client.copy_message = _copy_sometimes_fail
        message = _make_message(chat_id=100)

        await handle_fwd(bot, message)

        text = message.reply.await_args.args[0]
        # 4 сообщения, 2 успешно → "2/4"
        assert "2/4" in text


# ---------------------------------------------------------------------------
# handle_fwd — валидация аргументов
# ---------------------------------------------------------------------------

class TestHandleFwdValidation:
    @pytest.mark.asyncio
    async def test_fwd_no_args_raises(self) -> None:
        """!fwd без аргументов — UserInputError."""
        bot = _make_bot("")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

    @pytest.mark.asyncio
    async def test_fwd_invalid_chat_id_raises(self) -> None:
        """!fwd abc — UserInputError (невалидный chat_id)."""
        bot = _make_bot("not_a_number")
        message = _make_message(reply_to=_make_reply_msg())

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

    @pytest.mark.asyncio
    async def test_fwd_non_owner_raises(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot("200", access_level=AccessLevel.FULL)
        message = _make_message(reply_to=_make_reply_msg())

        with pytest.raises(UserInputError):
            await handle_fwd(bot, message)

        bot.client.copy_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_collect
# ---------------------------------------------------------------------------

class TestHandleCollect:
    @pytest.mark.asyncio
    async def test_collect_copies_messages(self) -> None:
        """!collect вызывает copy_message N раз из src_chat в dst_chat."""
        bot = _make_bot("500 3")
        message = _make_message(chat_id=100)

        await handle_collect(bot, message)

        assert bot.client.copy_message.await_count == 3
        # Все вызовы: to=100, from=500
        for call in bot.client.copy_message.call_args_list:
            assert call.args[0] == 100
            assert call.args[1] == 500

    @pytest.mark.asyncio
    async def test_collect_sends_header(self) -> None:
        """Перед копированием отправляется header-сообщение."""
        bot = _make_bot("500 2")
        message = _make_message()

        await handle_collect(bot, message)

        # reply вызван хотя бы раз (header)
        assert message.reply.await_count >= 1
        first_text = message.reply.call_args_list[0].args[0]
        assert "500" in first_text

    @pytest.mark.asyncio
    async def test_collect_no_args_raises(self) -> None:
        """!collect без аргументов — UserInputError."""
        bot = _make_bot("")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

    @pytest.mark.asyncio
    async def test_collect_only_chat_id_raises(self) -> None:
        """!collect <chat_id> без N — UserInputError."""
        bot = _make_bot("500")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

    @pytest.mark.asyncio
    async def test_collect_invalid_chat_id_raises(self) -> None:
        """!collect abc 5 — UserInputError."""
        bot = _make_bot("abc 5")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

    @pytest.mark.asyncio
    async def test_collect_invalid_n_raises(self) -> None:
        """!collect 500 abc — UserInputError."""
        bot = _make_bot("500 abc")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

    @pytest.mark.asyncio
    async def test_collect_n_zero_raises(self) -> None:
        """!collect 500 0 — UserInputError."""
        bot = _make_bot("500 0")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

    @pytest.mark.asyncio
    async def test_collect_n_too_large_raises(self) -> None:
        """!collect 500 101 — UserInputError."""
        bot = _make_bot("500 101")
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

    @pytest.mark.asyncio
    async def test_collect_n_boundary_100(self) -> None:
        """!collect 500 100 — граница, не бросает ошибку."""
        bot = _make_bot("500 100")
        message = _make_message(chat_id=100)

        await handle_collect(bot, message)  # не должно бросить UserInputError

    @pytest.mark.asyncio
    async def test_collect_non_owner_raises(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot("500 5", access_level=AccessLevel.PARTIAL)
        message = _make_message()

        with pytest.raises(UserInputError):
            await handle_collect(bot, message)

        bot.client.copy_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_collect_empty_chat_shows_empty_reply(self) -> None:
        """Если в чате нет сообщений — ответ содержит 📭."""
        async def _empty_history(chat_id, limit=10):
            return
            yield  # делает функцию генератором

        bot = _make_bot("500 5")
        bot.client.get_chat_history = _empty_history
        message = _make_message()

        await handle_collect(bot, message)

        text = message.reply.await_args.args[0]
        assert "📭" in text

    @pytest.mark.asyncio
    async def test_collect_partial_failure_warns(self) -> None:
        """Если часть copy_message падает — ответ содержит ⚠️."""
        call_count = 0

        async def _copy_sometimes_fail(to_chat, from_chat, msg_id):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("fail")
            return SimpleNamespace(id=msg_id)

        bot = _make_bot("500 4")
        bot.client.copy_message = _copy_sometimes_fail
        message = _make_message(chat_id=100)

        await handle_collect(bot, message)

        # Ищем ⚠️ среди всех reply-вызовов
        all_texts = [c.args[0] for c in message.reply.call_args_list]
        assert any("⚠️" in t for t in all_texts)

    @pytest.mark.asyncio
    async def test_collect_history_exception_returns_error(self) -> None:
        """Если get_chat_history падает — ответ содержит ❌."""
        async def _fail_history(chat_id, limit=10):
            raise RuntimeError("Access denied")
            yield

        bot = _make_bot("500 5")
        bot.client.get_chat_history = _fail_history
        message = _make_message()

        await handle_collect(bot, message)

        text = message.reply.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_collect_uses_edit_for_self_message(self) -> None:
        """Если сообщение от самого бота — header через edit."""
        bot = _make_bot("500 2")
        message = _make_message(from_user_id=bot.me.id, chat_id=100)

        await handle_collect(bot, message)

        # edit вызван для header
        message.edit.assert_awaited()

    @pytest.mark.asyncio
    async def test_collect_chronological_order(self) -> None:
        """Сообщения копируются в хронологическом порядке."""
        bot = _make_bot("500 3")
        message = _make_message(chat_id=100)

        await handle_collect(bot, message)

        calls = bot.client.copy_message.call_args_list
        msg_ids = [c.args[2] for c in calls]
        assert msg_ids == sorted(msg_ids)
