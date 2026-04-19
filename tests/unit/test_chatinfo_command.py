# -*- coding: utf-8 -*-
"""
Тесты для команды !chatinfo — подробная информация о чате.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_chatinfo

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "", chat_id: int = -1001234567890) -> MagicMock:
    """Mock-бот с _get_command_args и client."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.client = MagicMock()
    return bot


def _make_message(chat_id: int = -1001234567890) -> MagicMock:
    """Mock-сообщение с async reply."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


def _make_chat(
    *,
    chat_id: int = -1001234567890,
    title: str = "Test Group",
    chat_type: str = "supergroup",
    username: str | None = "testgroup",
    members_count: int | None = 1234,
    description: str = "Some description",
    date: int | None = 1705276800,  # 2024-01-15 UTC
    linked_chat=None,
    permissions=None,
) -> MagicMock:
    """Создать mock-объект чата."""
    chat = MagicMock()
    chat.id = chat_id
    chat.title = title
    chat.first_name = None
    chat.type = chat_type
    chat.username = username
    chat.members_count = members_count
    chat.description = description
    chat.date = date
    chat.linked_chat = linked_chat
    chat.permissions = permissions
    return chat


def _make_permissions(**kwargs) -> MagicMock:
    """Mock объекта ChatPermissions."""
    perms = MagicMock()
    defaults = {
        "can_send_messages": True,
        "can_send_media_messages": True,
        "can_send_polls": False,
        "can_add_web_page_previews": True,
        "can_change_info": False,
        "can_invite_users": True,
        "can_pin_messages": False,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(perms, k, v)
    return perms


# ---------------------------------------------------------------------------
# Тесты: получение текущего чата (без аргументов)
# ---------------------------------------------------------------------------


class TestChatInfoCurrentChat:
    """Тесты !chatinfo без аргументов — текущий чат."""

    @pytest.mark.asyncio
    async def test_базовый_вывод_содержит_заголовок(self) -> None:
        """Ответ содержит заголовок '📊 **Chat Info**'."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat()

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Chat Info" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_id_чата(self) -> None:
        """ID чата присутствует в ответе."""
        bot = _make_bot("")
        msg = _make_message(chat_id=-1001234567890)
        chat = _make_chat(chat_id=-1001234567890)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "-1001234567890" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_название(self) -> None:
        """Название чата отображается в ответе."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(title="Моя группа")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Моя группа" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_тип_чата(self) -> None:
        """Тип чата присутствует."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(chat_type="supergroup")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "supergroup" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_username(self) -> None:
        """Username с @ отображается в ответе."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(username="mygroup")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "@mygroup" in reply_text

    @pytest.mark.asyncio
    async def test_без_username_показывает_прочерк(self) -> None:
        """Без username — '—' в поле Username."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(username=None)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Username:** —" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_количество_участников(self) -> None:
        """Количество участников присутствует в ответе."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(members_count=1234)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "1,234" in reply_text

    @pytest.mark.asyncio
    async def test_дата_создания_форматируется(self) -> None:
        """Дата создания форматируется как YYYY-MM-DD."""
        bot = _make_bot("")
        msg = _make_message()
        # 2024-01-15 00:00:00 UTC
        chat = _make_chat(date=1705276800)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "2024-01-15" in reply_text

    @pytest.mark.asyncio
    async def test_без_даты_показывает_прочерк(self) -> None:
        """Без даты создания — '—' в поле Создан."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(date=None)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Создан:** —" in reply_text

    @pytest.mark.asyncio
    async def test_описание_присутствует_в_ответе(self) -> None:
        """Описание чата отображается."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(description="Тестовое описание группы")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Тестовое описание группы" in reply_text

    @pytest.mark.asyncio
    async def test_пустое_описание_не_показывается(self) -> None:
        """Пустое описание — строка Описание не выводится."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(description="")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Описание:" not in reply_text

    @pytest.mark.asyncio
    async def test_длинное_описание_обрезается(self) -> None:
        """Описание длиннее 200 символов обрезается с '…'."""
        bot = _make_bot("")
        msg = _make_message()
        long_desc = "А" * 300
        chat = _make_chat(description=long_desc)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "…" in reply_text

    @pytest.mark.asyncio
    async def test_linked_chat_присутствует(self) -> None:
        """Linked chat с username отображается."""
        bot = _make_bot("")
        msg = _make_message()
        linked = MagicMock()
        linked.username = "linkedchannel"
        linked.id = -1009876543210
        chat = _make_chat(linked_chat=linked)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "@linkedchannel" in reply_text

    @pytest.mark.asyncio
    async def test_linked_chat_без_username_показывает_id(self) -> None:
        """Linked chat без username — показывает ID."""
        bot = _make_bot("")
        msg = _make_message()
        linked = MagicMock()
        linked.username = None
        linked.id = -1009876543210
        chat = _make_chat(linked_chat=linked)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "-1009876543210" in reply_text

    @pytest.mark.asyncio
    async def test_нет_linked_chat_показывает_прочерк(self) -> None:
        """Без linked chat — '—'."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(linked_chat=None)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Linked chat:** —" in reply_text

    @pytest.mark.asyncio
    async def test_permissions_отображаются(self) -> None:
        """Права участников отображаются с иконками."""
        bot = _make_bot("")
        msg = _make_message()
        perms = _make_permissions(can_send_messages=True, can_send_polls=False)
        chat = _make_chat(permissions=perms)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Права участников" in reply_text
        assert "✅" in reply_text
        assert "❌" in reply_text

    @pytest.mark.asyncio
    async def test_количество_админов_отображается(self) -> None:
        """Число администраторов присутствует в ответе."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat()

        # 3 администратора
        admins = [MagicMock(), MagicMock(), MagicMock()]
        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter(admins))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Администраторов:** 3" in reply_text

    @pytest.mark.asyncio
    async def test_ошибка_получения_админов_не_ломает_ответ(self) -> None:
        """При ошибке get_chat_members — ответ всё равно отправляется."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat()

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(side_effect=Exception("forbidden"))

        await handle_chatinfo(bot, msg)

        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_используется_id_текущего_чата(self) -> None:
        """Без аргументов запрашивается текущий чат."""
        bot = _make_bot("")
        msg = _make_message(chat_id=-1001111111111)
        chat = _make_chat(chat_id=-1001111111111)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        bot.client.get_chat.assert_awaited_once_with(-1001111111111)

    @pytest.mark.asyncio
    async def test_разделитель_присутствует(self) -> None:
        """Ответ содержит разделитель '─────'."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat()

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "─────" in reply_text


# ---------------------------------------------------------------------------
# Тесты: указание другого чата по аргументу
# ---------------------------------------------------------------------------


class TestChatInfoByArgument:
    """Тесты !chatinfo <chat_id> и !chatinfo @username."""

    @pytest.mark.asyncio
    async def test_аргумент_числовой_id(self) -> None:
        """!chatinfo -100123 — запрашивает чат по числовому ID."""
        bot = _make_bot("-100123456789")
        msg = _make_message()
        chat = _make_chat(chat_id=-100123456789)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        bot.client.get_chat.assert_awaited_once_with(-100123456789)

    @pytest.mark.asyncio
    async def test_аргумент_username_без_собаки(self) -> None:
        """!chatinfo somegroup — запрашивает чат по username (без @)."""
        bot = _make_bot("somegroup")
        msg = _make_message()
        chat = _make_chat(username="somegroup")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        bot.client.get_chat.assert_awaited_once_with("somegroup")

    @pytest.mark.asyncio
    async def test_аргумент_username_с_собакой(self) -> None:
        """!chatinfo @somegroup — @ убирается, запрос по имени."""
        bot = _make_bot("@somegroup")
        msg = _make_message()
        chat = _make_chat(username="somegroup")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        bot.client.get_chat.assert_awaited_once_with("somegroup")

    @pytest.mark.asyncio
    async def test_несуществующий_чат_бросает_user_input_error(self) -> None:
        """При ошибке get_chat — UserInputError."""
        bot = _make_bot("nonexistent")
        msg = _make_message()

        bot.client.get_chat = AsyncMock(side_effect=Exception("Chat not found"))

        with pytest.raises(UserInputError) as exc_info:
            await handle_chatinfo(bot, msg)

        assert (
            "nonexistent" in exc_info.value.user_message
            or "Не удалось" in exc_info.value.user_message
        )


# ---------------------------------------------------------------------------
# Тесты: fallback для members_count
# ---------------------------------------------------------------------------


class TestChatInfoMembersCount:
    """Тесты поведения с members_count."""

    @pytest.mark.asyncio
    async def test_members_count_из_объекта_чата(self) -> None:
        """members_count берётся напрямую из объекта чата."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(members_count=500)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "500" in reply_text

    @pytest.mark.asyncio
    async def test_members_count_fallback_запрос(self) -> None:
        """Если members_count=None в чате — делается отдельный запрос."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(members_count=None)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members_count = AsyncMock(return_value=750)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "750" in reply_text

    @pytest.mark.asyncio
    async def test_members_count_не_показывается_если_недоступен(self) -> None:
        """Если счётчик недоступен — строка Участников не выводится."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(members_count=None)

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members_count = AsyncMock(side_effect=Exception("no access"))
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Участников" not in reply_text


# ---------------------------------------------------------------------------
# Тесты: различные типы чатов
# ---------------------------------------------------------------------------


class TestChatInfoTypes:
    """Тесты разных типов чатов."""

    @pytest.mark.asyncio
    async def test_channel_тип(self) -> None:
        """Канал — тип 'channel' отображается."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(chat_type="channel")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "channel" in reply_text

    @pytest.mark.asyncio
    async def test_group_тип(self) -> None:
        """Обычная группа — тип 'group'."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(chat_type="group")

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "group" in reply_text

    @pytest.mark.asyncio
    async def test_private_чат_без_title(self) -> None:
        """Приватный чат — нет title, используется first_name."""
        bot = _make_bot("")
        msg = _make_message()
        chat = _make_chat(title=None, chat_type="private")
        chat.title = None
        chat.first_name = "Иван"

        bot.client.get_chat = AsyncMock(return_value=chat)
        bot.client.get_chat_members = MagicMock(return_value=_aiter([]))

        await handle_chatinfo(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Иван" in reply_text


# ---------------------------------------------------------------------------
# Вспомогательная функция для async итераторов
# ---------------------------------------------------------------------------


async def _aiter_gen(items):
    for item in items:
        yield item


def _aiter(items):
    """Создать async iterable из списка."""
    return _aiter_gen(items)
