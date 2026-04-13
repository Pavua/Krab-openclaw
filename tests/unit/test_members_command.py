# -*- coding: utf-8 -*-
"""
Тесты команды !members — управление участниками группы.

Покрываем:
1) !members (без аргументов) — количество участников
2) !members list — список с дефолтным лимитом 10
3) !members list N — список с явным лимитом
4) !members list <невалидное N> — UserInputError
5) !members list — пустой список
6) !members list — длинный список (обрезается)
7) !members kick (reply) — кик участника
8) !members kick (нет reply) — UserInputError
9) !members kick (reply бот) — UserInputError
10) !members kick (CHAT_ADMIN_REQUIRED) — UserInputError
11) !members kick (прочая ошибка) — UserInputError
12) !members ban (reply) — бан участника
13) !members ban (нет reply) — UserInputError
14) !members ban (reply бот) — UserInputError
15) !members ban (CHAT_ADMIN_REQUIRED) — UserInputError
16) !members ban (прочая ошибка) — UserInputError
17) !members unban @username — разбан по username
18) !members unban <user_id> — разбан по числовому ID
19) !members unban (без аргумента) — UserInputError
20) !members unban (CHAT_ADMIN_REQUIRED) — UserInputError
21) !members unban (прочая ошибка) — UserInputError
22) !members <неизвестная подкоманда> — UserInputError (справка)
23) Не группа (PRIVATE) — UserInputError
24) Канал — UserInputError
25) !members — ошибка API get_chat_members_count — UserInputError
26) !members list — ошибка API get_chat_members — UserInputError
27) !members list — лимит клипуется к 200
28) !members list — пользователь без username показывается через id
29) !members list — удалённые пользователи пропускаются
30) !members kick — admin-keyword в тексте ошибки детектируется
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_members

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


@asynccontextmanager
async def raises_user_input(match_text: str) -> AsyncIterator[None]:
    """Контекст-менеджер: ожидаем UserInputError с user_message содержащим match_text."""
    try:
        yield
    except UserInputError as exc:
        assert match_text in exc.user_message, (
            f"Ожидали '{match_text}' в user_message, получили: {exc.user_message!r}"
        )
    else:
        pytest.fail(f"Ожидали UserInputError с '{match_text}', но исключение не было выброшено")


# ---------------------------------------------------------------------------
# Фабрики для mock-объектов
# ---------------------------------------------------------------------------


def _make_user(
    user_id: int = 111,
    first_name: str = "Иван",
    last_name: str | None = None,
    username: str | None = "ivan_user",
    is_bot: bool = False,
    is_deleted: bool = False,
) -> SimpleNamespace:
    """Минимальный mock User."""
    return SimpleNamespace(
        id=user_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        is_bot=is_bot,
        is_deleted=is_deleted,
    )


def _make_member(user: SimpleNamespace) -> SimpleNamespace:
    """Обёртка ChatMember."""
    return SimpleNamespace(user=user)


async def _async_iter(items):
    """Вспомогательный асинхронный генератор."""
    for item in items:
        yield item


def _make_bot(
    members_count: int = 42,
    members_list=None,
    ban_side_effect=None,
    unban_side_effect=None,
    count_side_effect=None,
    list_side_effect=None,
) -> SimpleNamespace:
    """Минимальный mock KraabUserbot."""
    if members_list is None:
        members_list = [_make_member(_make_user())]

    get_count_mock = AsyncMock(
        return_value=members_count, side_effect=count_side_effect
    )
    ban_mock = AsyncMock(side_effect=ban_side_effect)
    unban_mock = AsyncMock(side_effect=unban_side_effect)

    if list_side_effect is not None:
        # Если передан side_effect — кидаем исключение при итерации
        async def _erroring_iter(*args, **kwargs):
            raise list_side_effect
            yield  # noqa: RET505 — делает функцию генератором

        get_members_mock = MagicMock(side_effect=lambda *a, **kw: _erroring_iter())
    else:
        get_members_mock = MagicMock(return_value=_async_iter(members_list))

    bot = SimpleNamespace(
        client=SimpleNamespace(
            get_chat_members_count=get_count_mock,
            get_chat_members=get_members_mock,
            ban_chat_member=ban_mock,
            unban_chat_member=unban_mock,
        ),
    )
    return bot


def _make_message(
    text: str = "!members",
    chat_type: str = "SUPERGROUP",
    chat_id: int = -100123456,
    chat_title: str = "Test Group",
    reply_user=None,
) -> SimpleNamespace:
    """Минимальный mock pyrogram.Message."""
    chat_type_obj = MagicMock()
    chat_type_obj.name = chat_type

    if reply_user is not None:
        reply_msg = SimpleNamespace(from_user=reply_user)
    else:
        reply_msg = None

    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(
            id=chat_id,
            type=chat_type_obj,
            title=chat_title,
        ),
        reply_to_message=reply_msg,
        reply=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Тесты: !members (без аргументов)
# ---------------------------------------------------------------------------


class TestMembersCount:
    @pytest.mark.asyncio
    async def test_returns_member_count(self) -> None:
        """!members возвращает количество участников."""
        bot = _make_bot(members_count=150)
        msg = _make_message("!members")

        await handle_members(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.call_args[0][0]
        assert "150" in text
        assert "👥" in text

    @pytest.mark.asyncio
    async def test_count_api_error_raises(self) -> None:
        """Ошибка API get_chat_members_count → UserInputError."""
        bot = _make_bot(count_side_effect=Exception("RPC error"))
        msg = _make_message("!members")

        async with raises_user_input("Не удалось получить количество"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_chat_title_in_reply(self) -> None:
        """Название чата присутствует в ответе."""
        bot = _make_bot(members_count=7)
        msg = _make_message("!members", chat_title="Моя группа")

        await handle_members(bot, msg)

        assert "Моя группа" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_chat_id_when_no_title(self) -> None:
        """Если title=None, используется chat_id."""
        bot = _make_bot(members_count=7)
        msg = _make_message("!members")
        msg.chat.title = None

        await handle_members(bot, msg)

        assert str(msg.chat.id) in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Тесты: !members list
# ---------------------------------------------------------------------------


class TestMembersList:
    @pytest.mark.asyncio
    async def test_default_limit_10(self) -> None:
        """!members list — дефолтный лимит 10."""
        user = _make_user()
        bot = _make_bot(members_list=[_make_member(user)])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        # Проверяем что get_chat_members вызван с limit=10
        call_kwargs = bot.client.get_chat_members.call_args
        assert call_kwargs.kwargs.get("limit") == 10 or (
            len(call_kwargs.args) >= 2 and call_kwargs.args[1] == 10
        )

    @pytest.mark.asyncio
    async def test_explicit_limit(self) -> None:
        """!members list 25 — явный лимит 25."""
        user = _make_user()
        bot = _make_bot(members_list=[_make_member(user)])
        msg = _make_message("!members list 25")

        await handle_members(bot, msg)

        call_kwargs = bot.client.get_chat_members.call_args
        assert call_kwargs.kwargs.get("limit") == 25 or (
            len(call_kwargs.args) >= 2 and call_kwargs.args[1] == 25
        )

    @pytest.mark.asyncio
    async def test_limit_capped_at_200(self) -> None:
        """Лимит > 200 клипуется к 200."""
        user = _make_user()
        bot = _make_bot(members_list=[_make_member(user)])
        msg = _make_message("!members list 999")

        await handle_members(bot, msg)

        call_kwargs = bot.client.get_chat_members.call_args
        actual_limit = call_kwargs.kwargs.get("limit") or (
            call_kwargs.args[1] if len(call_kwargs.args) >= 2 else None
        )
        assert actual_limit == 200

    @pytest.mark.asyncio
    async def test_invalid_limit_raises(self) -> None:
        """Нечисловой лимит → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!members list abc")

        async with raises_user_input("Укажи число участников"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_negative_limit_raises(self) -> None:
        """Отрицательный лимит → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!members list -5")

        async with raises_user_input("Укажи число участников"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_list_shows_user_info(self) -> None:
        """Список содержит имя и username участника."""
        user = _make_user(first_name="Мария", username="masha")
        bot = _make_bot(members_list=[_make_member(user)])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "Мария" in text
        assert "@masha" in text

    @pytest.mark.asyncio
    async def test_user_without_username_shows_id(self) -> None:
        """Пользователь без username — отображается как id<N>."""
        user = _make_user(user_id=55555, first_name="Аноним", username=None)
        bot = _make_bot(members_list=[_make_member(user)])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "id55555" in text

    @pytest.mark.asyncio
    async def test_user_with_last_name(self) -> None:
        """Фамилия добавляется к имени."""
        user = _make_user(first_name="Иван", last_name="Петров", username="ivan")
        bot = _make_bot(members_list=[_make_member(user)])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "Иван Петров" in text

    @pytest.mark.asyncio
    async def test_deleted_users_skipped(self) -> None:
        """Удалённые пользователи пропускаются."""
        deleted_user = _make_user(is_deleted=True, first_name="Deleted")
        normal_user = _make_user(user_id=222, first_name="Нормальный", username="normal")
        bot = _make_bot(members_list=[
            _make_member(deleted_user),
            _make_member(normal_user),
        ])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "Deleted" not in text
        assert "Нормальный" in text

    @pytest.mark.asyncio
    async def test_none_user_skipped(self) -> None:
        """Участник без user объекта (None) пропускается."""
        bot = _make_bot(members_list=[SimpleNamespace(user=None)])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        # Список пуст — должно прийти сообщение «пуст или недоступен»
        text = msg.reply.call_args[0][0]
        assert "пуст" in text

    @pytest.mark.asyncio
    async def test_empty_list_message(self) -> None:
        """Пустой список → сообщение о недоступности."""
        bot = _make_bot(members_list=[])
        msg = _make_message("!members list")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "пуст" in text

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        """Ошибка get_chat_members → UserInputError."""
        bot = _make_bot(list_side_effect=Exception("FLOOD_WAIT_5"))
        msg = _make_message("!members list")

        async with raises_user_input("Не удалось получить список"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_long_list_truncated(self) -> None:
        """Длинный список обрезается до 4096 символов."""
        # Создаём 100 участников с длинными именами
        members = [
            _make_member(_make_user(
                user_id=i,
                first_name="А" * 50,
                last_name="Б" * 50,
                username=f"user{i}",
            ))
            for i in range(100)
        ]
        bot = _make_bot(members_list=members)
        msg = _make_message("!members list 100")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert len(text) <= 4096 + 5  # +5 для символа «…»


# ---------------------------------------------------------------------------
# Тесты: !members kick
# ---------------------------------------------------------------------------


class TestMembersKick:
    @pytest.mark.asyncio
    async def test_kick_bans_and_unbans(self) -> None:
        """!members kick — ban + unban участника."""
        user = _make_user(user_id=999, first_name="Нарушитель")
        bot = _make_bot()
        msg = _make_message("!members kick", reply_user=user)

        await handle_members(bot, msg)

        bot.client.ban_chat_member.assert_awaited_once_with(msg.chat.id, 999)
        bot.client.unban_chat_member.assert_awaited_once_with(msg.chat.id, 999)

    @pytest.mark.asyncio
    async def test_kick_success_reply(self) -> None:
        """Успешный кик → подтверждение с именем."""
        user = _make_user(user_id=999, first_name="Нарушитель")
        bot = _make_bot()
        msg = _make_message("!members kick", reply_user=user)

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "Нарушитель" in text
        assert "кикнут" in text

    @pytest.mark.asyncio
    async def test_kick_no_reply_raises(self) -> None:
        """!members kick без reply → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!members kick")  # reply_user=None

        async with raises_user_input("Ответь на сообщение"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_kick_bot_raises(self) -> None:
        """Кик бота → UserInputError."""
        bot_user = _make_user(is_bot=True)
        bot = _make_bot()
        msg = _make_message("!members kick", reply_user=bot_user)

        async with raises_user_input("Нельзя кикнуть бота"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_kick_admin_required_raises(self) -> None:
        """CHAT_ADMIN_REQUIRED при кике → UserInputError."""
        user = _make_user()
        bot = _make_bot(ban_side_effect=Exception("CHAT_ADMIN_REQUIRED: need admin"))
        msg = _make_message("!members kick", reply_user=user)

        async with raises_user_input("Нет прав администратора для кика"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_kick_admin_keyword_detected(self) -> None:
        """Слово 'admin' в тексте ошибки → UserInputError о правах."""
        user = _make_user()
        bot = _make_bot(ban_side_effect=Exception("you must be an admin"))
        msg = _make_message("!members kick", reply_user=user)

        async with raises_user_input("Нет прав администратора для кика"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_kick_generic_error_raises(self) -> None:
        """Произвольная ошибка при кике → UserInputError."""
        user = _make_user()
        bot = _make_bot(ban_side_effect=Exception("FLOOD_WAIT_5"))
        msg = _make_message("!members kick", reply_user=user)

        async with raises_user_input("Не удалось кикнуть"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_kick_user_without_name_uses_id(self) -> None:
        """Если first_name отсутствует — используется ID в ответе."""
        user = _make_user(user_id=777, first_name="")
        bot = _make_bot()
        msg = _make_message("!members kick", reply_user=user)

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "777" in text


# ---------------------------------------------------------------------------
# Тесты: !members ban
# ---------------------------------------------------------------------------


class TestMembersBan:
    @pytest.mark.asyncio
    async def test_ban_calls_api(self) -> None:
        """!members ban — вызов ban_chat_member."""
        user = _make_user(user_id=888, first_name="Спамер")
        bot = _make_bot()
        msg = _make_message("!members ban", reply_user=user)

        await handle_members(bot, msg)

        bot.client.ban_chat_member.assert_awaited_once_with(msg.chat.id, 888)
        # unban не должен вызываться — это настоящий бан
        bot.client.unban_chat_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ban_success_reply(self) -> None:
        """Успешный бан → подтверждение с именем."""
        user = _make_user(user_id=888, first_name="Спамер")
        bot = _make_bot()
        msg = _make_message("!members ban", reply_user=user)

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "Спамер" in text
        assert "забанен" in text

    @pytest.mark.asyncio
    async def test_ban_no_reply_raises(self) -> None:
        """!members ban без reply → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!members ban")

        async with raises_user_input("Ответь на сообщение"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_ban_bot_raises(self) -> None:
        """Бан бота → UserInputError."""
        bot_user = _make_user(is_bot=True)
        bot = _make_bot()
        msg = _make_message("!members ban", reply_user=bot_user)

        async with raises_user_input("Нельзя забанить бота"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_ban_admin_required_raises(self) -> None:
        """CHAT_ADMIN_REQUIRED при бане → UserInputError."""
        user = _make_user()
        bot = _make_bot(ban_side_effect=Exception("CHAT_ADMIN_REQUIRED"))
        msg = _make_message("!members ban", reply_user=user)

        async with raises_user_input("Нет прав администратора для бана"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_ban_generic_error_raises(self) -> None:
        """Произвольная ошибка при бане → UserInputError."""
        user = _make_user()
        bot = _make_bot(ban_side_effect=Exception("USER_NOT_MUTUAL_CONTACT"))
        msg = _make_message("!members ban", reply_user=user)

        async with raises_user_input("Не удалось забанить"):
            await handle_members(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: !members unban
# ---------------------------------------------------------------------------


class TestMembersUnban:
    @pytest.mark.asyncio
    async def test_unban_by_username(self) -> None:
        """!members unban @username — вызывает unban_chat_member с @username."""
        bot = _make_bot()
        msg = _make_message("!members unban @bad_guy")

        await handle_members(bot, msg)

        bot.client.unban_chat_member.assert_awaited_once_with(msg.chat.id, "@bad_guy")

    @pytest.mark.asyncio
    async def test_unban_by_numeric_id(self) -> None:
        """!members unban 12345 — вызывает unban_chat_member с int."""
        bot = _make_bot()
        msg = _make_message("!members unban 12345")

        await handle_members(bot, msg)

        bot.client.unban_chat_member.assert_awaited_once_with(msg.chat.id, 12345)

    @pytest.mark.asyncio
    async def test_unban_by_string_reference(self) -> None:
        """!members unban badguy — без @ передаётся как строка."""
        bot = _make_bot()
        msg = _make_message("!members unban badguy")

        await handle_members(bot, msg)

        bot.client.unban_chat_member.assert_awaited_once_with(msg.chat.id, "badguy")

    @pytest.mark.asyncio
    async def test_unban_success_reply(self) -> None:
        """Успешный разбан → подтверждение."""
        bot = _make_bot()
        msg = _make_message("!members unban @good_guy")

        await handle_members(bot, msg)

        text = msg.reply.call_args[0][0]
        assert "разбанен" in text
        assert "@good_guy" in text

    @pytest.mark.asyncio
    async def test_unban_no_arg_raises(self) -> None:
        """!members unban без аргумента → UserInputError."""
        bot = _make_bot()
        msg = _make_message("!members unban")

        async with raises_user_input("Укажи пользователя"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_unban_admin_required_raises(self) -> None:
        """CHAT_ADMIN_REQUIRED при разбане → UserInputError."""
        bot = _make_bot(unban_side_effect=Exception("CHAT_ADMIN_REQUIRED"))
        msg = _make_message("!members unban @someone")

        async with raises_user_input("Нет прав администратора для разбана"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_unban_generic_error_raises(self) -> None:
        """Произвольная ошибка при разбане → UserInputError."""
        bot = _make_bot(unban_side_effect=Exception("USER_ID_INVALID"))
        msg = _make_message("!members unban @someone")

        async with raises_user_input("Не удалось разбанить"):
            await handle_members(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: тип чата
# ---------------------------------------------------------------------------


class TestMembersChatType:
    @pytest.mark.asyncio
    async def test_private_chat_raises(self) -> None:
        """В приватном чате команда недоступна."""
        bot = _make_bot()
        msg = _make_message("!members", chat_type="PRIVATE")

        async with raises_user_input("только в группах"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_channel_raises(self) -> None:
        """В канале команда недоступна."""
        bot = _make_bot()
        msg = _make_message("!members", chat_type="CHANNEL")

        async with raises_user_input("только в группах"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_group_allowed(self) -> None:
        """В GROUP команда работает."""
        bot = _make_bot(members_count=5)
        msg = _make_message("!members", chat_type="GROUP")

        await handle_members(bot, msg)

        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_supergroup_allowed(self) -> None:
        """В SUPERGROUP команда работает."""
        bot = _make_bot(members_count=100)
        msg = _make_message("!members", chat_type="SUPERGROUP")

        await handle_members(bot, msg)

        msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: неизвестная подкоманда
# ---------------------------------------------------------------------------


class TestMembersUnknownSubcommand:
    @pytest.mark.asyncio
    async def test_unknown_subcommand_shows_help(self) -> None:
        """Неизвестная подкоманда → UserInputError со справкой."""
        bot = _make_bot()
        msg = _make_message("!members promote")

        async with raises_user_input("!members"):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_unknown_subcommand_contains_all_subcmds(self) -> None:
        """Справка содержит все подкоманды."""
        bot = _make_bot()
        msg = _make_message("!members whatever")

        try:
            await handle_members(bot, msg)
        except UserInputError as exc:
            assert "list" in exc.user_message
            assert "kick" in exc.user_message
            assert "ban" in exc.user_message
            assert "unban" in exc.user_message
        else:
            pytest.fail("Ожидали UserInputError")
