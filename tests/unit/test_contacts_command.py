# -*- coding: utf-8 -*-
"""
Тесты для команды !contacts — управление контактами адресной книги.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_contacts


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> MagicMock:
    """Mock-бот с _get_command_args и client."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.client = MagicMock()
    return bot


def _make_message() -> MagicMock:
    """Mock-сообщение с async reply."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


def _make_user(
    *,
    user_id: int = 123456789,
    first_name: str = "Иван",
    last_name: str | None = None,
    username: str | None = None,
    phone_number: str | None = None,
) -> MagicMock:
    """Создать mock-объект пользователя (контакта)."""
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.phone_number = phone_number
    return user


# ---------------------------------------------------------------------------
# Тесты: !contacts без аргументов — количество контактов
# ---------------------------------------------------------------------------


class TestContactsCount:
    """Тесты !contacts — показ количества контактов."""

    @pytest.mark.asyncio
    async def test_отображает_количество_контактов(self) -> None:
        """Ответ содержит количество контактов."""
        bot = _make_bot("")
        msg = _make_message()
        contacts = [_make_user(user_id=i) for i in range(42)]
        bot.client.get_contacts = AsyncMock(return_value=contacts)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "42" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_заголовок_контакты(self) -> None:
        """Ответ содержит слово 'Контакты'."""
        bot = _make_bot("")
        msg = _make_message()
        bot.client.get_contacts = AsyncMock(return_value=[])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Контакты" in reply_text

    @pytest.mark.asyncio
    async def test_ноль_контактов(self) -> None:
        """Пустая адресная книга — 0 контактов."""
        bot = _make_bot("")
        msg = _make_message()
        bot.client.get_contacts = AsyncMock(return_value=[])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "0" in reply_text

    @pytest.mark.asyncio
    async def test_один_контакт(self) -> None:
        """Один контакт — 1 в ответе."""
        bot = _make_bot("")
        msg = _make_message()
        bot.client.get_contacts = AsyncMock(return_value=[_make_user()])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "1" in reply_text

    @pytest.mark.asyncio
    async def test_ответ_содержит_подсказки_команд(self) -> None:
        """Ответ содержит подсказки поиска и добавления."""
        bot = _make_bot("")
        msg = _make_message()
        bot.client.get_contacts = AsyncMock(return_value=[])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "search" in reply_text
        assert "add" in reply_text

    @pytest.mark.asyncio
    async def test_ошибка_get_contacts_отправляет_ошибку(self) -> None:
        """При ошибке API — сообщение об ошибке."""
        bot = _make_bot("")
        msg = _make_message()
        bot.client.get_contacts = AsyncMock(side_effect=Exception("network error"))

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "❌" in reply_text or "Не удалось" in reply_text

    @pytest.mark.asyncio
    async def test_get_contacts_вызывается_без_аргументов(self) -> None:
        """get_contacts вызывается при пустых аргументах."""
        bot = _make_bot("")
        msg = _make_message()
        bot.client.get_contacts = AsyncMock(return_value=[])

        await handle_contacts(bot, msg)

        bot.client.get_contacts.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: !contacts search — поиск контактов
# ---------------------------------------------------------------------------


class TestContactsSearch:
    """Тесты !contacts search <запрос>."""

    @pytest.mark.asyncio
    async def test_поиск_возвращает_результаты(self) -> None:
        """Результаты поиска отображаются в ответе."""
        bot = _make_bot("search Иван")
        msg = _make_message()
        contacts = [
            _make_user(user_id=1, first_name="Иван", last_name="Петров", phone_number="+79001234567"),
        ]
        bot.client.search_contacts = AsyncMock(return_value=contacts)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Иван" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_содержит_телефон_контакта(self) -> None:
        """Телефон пользователя показывается в результатах поиска."""
        bot = _make_bot("search Мария")
        msg = _make_message()
        contacts = [
            _make_user(first_name="Мария", phone_number="+79119876543"),
        ]
        bot.client.search_contacts = AsyncMock(return_value=contacts)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "+79119876543" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_без_телефона_показывает_скрыт(self) -> None:
        """Если телефон не доступен — 'скрыт'."""
        bot = _make_bot("search Анон")
        msg = _make_message()
        user = _make_user(first_name="Анон", phone_number=None)
        bot.client.search_contacts = AsyncMock(return_value=[user])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "скрыт" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_показывает_username(self) -> None:
        """Username контакта отображается с @."""
        bot = _make_bot("search Дима")
        msg = _make_message()
        user = _make_user(first_name="Дима", username="dima_dev")
        bot.client.search_contacts = AsyncMock(return_value=[user])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "@dima_dev" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_без_результатов(self) -> None:
        """Поиск без результатов — сообщение 'ничего не найдено'."""
        bot = _make_bot("search НесуществующийЧеловек")
        msg = _make_message()
        bot.client.search_contacts = AsyncMock(return_value=[])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "не найдено" in reply_text.lower() or "📭" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_без_запроса_бросает_user_input_error(self) -> None:
        """!contacts search без запроса — UserInputError."""
        bot = _make_bot("search")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_поиск_передает_запрос_в_api(self) -> None:
        """search_contacts вызывается с переданным запросом."""
        bot = _make_bot("search Алексей")
        msg = _make_message()
        bot.client.search_contacts = AsyncMock(return_value=[])

        await handle_contacts(bot, msg)

        bot.client.search_contacts.assert_awaited_once_with("Алексей")

    @pytest.mark.asyncio
    async def test_поиск_ошибка_api_сообщает_об_ошибке(self) -> None:
        """При ошибке search_contacts — сообщение об ошибке."""
        bot = _make_bot("search Тест")
        msg = _make_message()
        bot.client.search_contacts = AsyncMock(side_effect=Exception("timeout"))

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "❌" in reply_text or "Ошибка" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_показывает_количество_результатов(self) -> None:
        """В заголовке поиска указано количество найденных контактов."""
        bot = _make_bot("search Ник")
        msg = _make_message()
        users = [_make_user(user_id=i, first_name=f"Ник{i}") for i in range(3)]
        bot.client.search_contacts = AsyncMock(return_value=users)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "3" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_ограничение_20_результатов(self) -> None:
        """При >20 результатах выводятся первые 20 + пометка об остальных."""
        bot = _make_bot("search Имя")
        msg = _make_message()
        users = [_make_user(user_id=i, first_name=f"Имя{i}") for i in range(25)]
        bot.client.search_contacts = AsyncMock(return_value=users)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        # 25 - 20 = 5 оставшихся
        assert "5" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_fullname_с_фамилией(self) -> None:
        """Полное имя (имя + фамилия) отображается в ответе."""
        bot = _make_bot("search Петров")
        msg = _make_message()
        user = _make_user(first_name="Иван", last_name="Петров")
        bot.client.search_contacts = AsyncMock(return_value=[user])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Иван" in reply_text
        assert "Петров" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_без_имени_показывает_прочерк(self) -> None:
        """Без first_name и last_name — '—' вместо имени."""
        bot = _make_bot("search test")
        msg = _make_message()
        user = _make_user(first_name="", last_name=None)
        bot.client.search_contacts = AsyncMock(return_value=[user])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "—" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_содержит_id_пользователя(self) -> None:
        """ID пользователя присутствует в результатах поиска."""
        bot = _make_bot("search Тест")
        msg = _make_message()
        user = _make_user(user_id=999888777, first_name="Тест")
        bot.client.search_contacts = AsyncMock(return_value=[user])

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "999888777" in reply_text


# ---------------------------------------------------------------------------
# Тесты: !contacts add — добавление контакта
# ---------------------------------------------------------------------------


class TestContactsAdd:
    """Тесты !contacts add <phone> <имя>."""

    @pytest.mark.asyncio
    async def test_добавление_успешно_сообщает_имя(self) -> None:
        """При успешном добавлении — подтверждение с именем."""
        bot = _make_bot("add +79001234567 Иван")
        msg = _make_message()
        added_user = _make_user(user_id=111, first_name="Иван")
        bot.client.add_contact = AsyncMock(return_value=added_user)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "✅" in reply_text
        assert "Иван" in reply_text

    @pytest.mark.asyncio
    async def test_добавление_сообщает_телефон(self) -> None:
        """Подтверждение содержит номер телефона."""
        bot = _make_bot("add +79001234567 Иван")
        msg = _make_message()
        added_user = _make_user(user_id=111, first_name="Иван")
        bot.client.add_contact = AsyncMock(return_value=added_user)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "+79001234567" in reply_text

    @pytest.mark.asyncio
    async def test_добавление_показывает_id_пользователя(self) -> None:
        """Подтверждение содержит ID добавленного контакта."""
        bot = _make_bot("add +79001234567 Иван")
        msg = _make_message()
        added_user = _make_user(user_id=777123, first_name="Иван")
        bot.client.add_contact = AsyncMock(return_value=added_user)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "777123" in reply_text

    @pytest.mark.asyncio
    async def test_добавление_возвращает_none_всё_равно_подтверждает(self) -> None:
        """Если add_contact вернул None — всё равно подтверждение."""
        bot = _make_bot("add +79001234567 Тест")
        msg = _make_message()
        bot.client.add_contact = AsyncMock(return_value=None)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "✅" in reply_text

    @pytest.mark.asyncio
    async def test_добавление_передает_телефон_в_api(self) -> None:
        """add_contact вызывается с правильным номером телефона."""
        bot = _make_bot("add +79005556677 Мария")
        msg = _make_message()
        bot.client.add_contact = AsyncMock(return_value=None)

        await handle_contacts(bot, msg)

        bot.client.add_contact.assert_awaited_once_with("+79005556677", "Мария")

    @pytest.mark.asyncio
    async def test_добавление_передает_имя_в_api(self) -> None:
        """add_contact вызывается с правильным именем контакта."""
        bot = _make_bot("add +79001111111 Алексей")
        msg = _make_message()
        bot.client.add_contact = AsyncMock(return_value=None)

        await handle_contacts(bot, msg)

        bot.client.add_contact.assert_awaited_once_with("+79001111111", "Алексей")

    @pytest.mark.asyncio
    async def test_добавление_без_аргументов_бросает_user_input_error(self) -> None:
        """!contacts add без аргументов — UserInputError."""
        bot = _make_bot("add")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_добавление_только_телефон_без_имени_бросает_ошибку(self) -> None:
        """!contacts add <phone> без имени — UserInputError."""
        bot = _make_bot("add +79001234567")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_добавление_ошибка_api_сообщает_об_ошибке(self) -> None:
        """При ошибке add_contact — сообщение об ошибке."""
        bot = _make_bot("add +79001234567 Тест")
        msg = _make_message()
        bot.client.add_contact = AsyncMock(side_effect=Exception("user not found"))

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "❌" in reply_text or "Не удалось" in reply_text

    @pytest.mark.asyncio
    async def test_добавление_fullname_с_фамилией(self) -> None:
        """Если у добавленного пользователя есть фамилия — полное имя в ответе."""
        bot = _make_bot("add +79001234567 Иван")
        msg = _make_message()
        added_user = _make_user(user_id=11, first_name="Иван", last_name="Сидоров")
        bot.client.add_contact = AsyncMock(return_value=added_user)

        await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Иван" in reply_text
        assert "Сидоров" in reply_text

    @pytest.mark.asyncio
    async def test_добавление_имя_из_нескольких_слов(self) -> None:
        """Имя контакта может содержать несколько слов."""
        bot = _make_bot("add +79001234567 Иван Иванович")
        msg = _make_message()
        bot.client.add_contact = AsyncMock(return_value=None)

        await handle_contacts(bot, msg)

        # Должно вызываться с "Иван Иванович"
        call_args = bot.client.add_contact.call_args[0]
        assert call_args[1] == "Иван Иванович"


# ---------------------------------------------------------------------------
# Тесты: неизвестные подкоманды
# ---------------------------------------------------------------------------


class TestContactsUnknownSubcommand:
    """Тесты неизвестных подкоманд."""

    @pytest.mark.asyncio
    async def test_неизвестная_подкоманда_бросает_user_input_error(self) -> None:
        """Неизвестная подкоманда — UserInputError."""
        bot = _make_bot("delete +79001234567")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_ошибка_содержит_подсказки(self) -> None:
        """UserInputError содержит подсказки по использованию."""
        bot = _make_bot("unknown")
        msg = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_contacts(bot, msg)

        assert "search" in exc_info.value.user_message
        assert "add" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_ошибка_содержит_contacts(self) -> None:
        """UserInputError упоминает команду contacts."""
        bot = _make_bot("list")
        msg = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_contacts(bot, msg)

        assert "contacts" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# Тесты: импорт и регистрация
# ---------------------------------------------------------------------------


class TestContactsImport:
    """Тесты импортируемости обработчика."""

    def test_handle_contacts_импортируется_из_handlers(self) -> None:
        """handle_contacts можно импортировать из src.handlers."""
        from src.handlers import handle_contacts as hc

        assert callable(hc)

    def test_handle_contacts_является_корутиной(self) -> None:
        """handle_contacts — это async функция."""
        import asyncio

        from src.handlers.command_handlers import handle_contacts as hc

        assert asyncio.iscoroutinefunction(hc)

    def test_handle_contacts_в_all_handlers(self) -> None:
        """handle_contacts есть в __all__ модуля handlers."""
        from src.handlers import __all__ as all_handlers

        assert "handle_contacts" in all_handlers
