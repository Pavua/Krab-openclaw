# -*- coding: utf-8 -*-
"""
Тесты для команды !contacts — управление кэшем контактов.

Полностью переписаны в Session 31: теперь !contacts использует
contact_cache + telegram_resolver вместо прямых API-вызовов.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_contacts

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

_MODULE = "src.handlers.commands.group_admin_commands"


def _make_bot(args: str = "") -> MagicMock:
    """Mock-бот с owner-level доступом."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.client = MagicMock()
    # owner access profile
    profile = MagicMock()
    profile.level = AccessLevel.OWNER
    bot._get_access_profile = MagicMock(return_value=profile)
    return bot


def _make_bot_non_owner(args: str = "") -> MagicMock:
    """Mock-бот с guest-level доступом (не owner)."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.client = MagicMock()
    profile = MagicMock()
    profile.level = AccessLevel.GUEST
    bot._get_access_profile = MagicMock(return_value=profile)
    return bot


def _make_message() -> MagicMock:
    """Mock-сообщение с async reply."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


def _make_cache_entry(
    *,
    username: str = "vasya",
    peer_id: int = 123456789,
    display_name: str = "Василий",
    aliases: list[str] | None = None,
) -> dict:
    """Создать запись кэша контактов."""
    return {
        "username": username,
        "peer_id": peer_id,
        "display_name": display_name,
        "aliases": aliases or [],
        "last_resolved_at": "2026-05-01T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Тесты: owner-only
# ---------------------------------------------------------------------------


class TestContactsOwnerOnly:
    """Команда !contacts доступна только владельцу."""

    @pytest.mark.asyncio
    async def test_не_owner_бросает_ошибку(self) -> None:
        """Не-owner получает UserInputError."""
        bot = _make_bot_non_owner("")
        msg = _make_message()

        with pytest.raises(UserInputError) as exc_info:
            await handle_contacts(bot, msg)

        assert "owner" in exc_info.value.user_message.lower() or "🔒" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_owner_проходит_проверку(self) -> None:
        """Owner без ошибки доступа выполняет команду."""
        bot = _make_bot("")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = []
            await handle_contacts(bot, msg)
        # Нет UserInputError — тест прошёл


# ---------------------------------------------------------------------------
# Тесты: !contacts / !contacts list — показ кэша
# ---------------------------------------------------------------------------


class TestContactsList:
    """Тесты !contacts (без аргументов) и !contacts list."""

    @pytest.mark.asyncio
    async def test_пустой_кэш_сообщает_о_пустоте(self) -> None:
        """Пустой кэш → сообщение о пустоте + помощь."""
        bot = _make_bot("")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = []
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "пуст" in reply_text.lower() or "пустой" in reply_text.lower() or "Кэш" in reply_text

    @pytest.mark.asyncio
    async def test_показывает_количество_записей(self) -> None:
        """Количество записей кэша отображается в заголовке."""
        bot = _make_bot("")
        msg = _make_message()
        entries = [_make_cache_entry(username=f"user{i}", peer_id=i) for i in range(3)]
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = entries
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "3" in reply_text

    @pytest.mark.asyncio
    async def test_показывает_display_name(self) -> None:
        """display_name контакта виден в выводе."""
        bot = _make_bot("")
        msg = _make_message()
        entry = _make_cache_entry(display_name="Алексей Иванов", peer_id=111)
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = [entry]
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Алексей Иванов" in reply_text

    @pytest.mark.asyncio
    async def test_показывает_peer_id(self) -> None:
        """peer_id контакта виден в выводе."""
        bot = _make_bot("")
        msg = _make_message()
        entry = _make_cache_entry(peer_id=987654321)
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = [entry]
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "987654321" in reply_text

    @pytest.mark.asyncio
    async def test_показывает_aliases(self) -> None:
        """aliases контакта отображаются в строке."""
        bot = _make_bot("")
        msg = _make_message()
        entry = _make_cache_entry(aliases=["Лёша из армии", "Боец"])
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = [entry]
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Лёша из армии" in reply_text

    @pytest.mark.asyncio
    async def test_ограничение_30_записей(self) -> None:
        """При >30 записях выводятся первые 30 + пометка."""
        bot = _make_bot("")
        msg = _make_message()
        entries = [_make_cache_entry(username=f"u{i}", peer_id=i) for i in range(35)]
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = entries
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        # 35 - 30 = 5 оставшихся
        assert "5" in reply_text

    @pytest.mark.asyncio
    async def test_list_subcmd_работает(self) -> None:
        """!contacts list эквивалентен !contacts."""
        bot = _make_bot("list")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.list_all.return_value = []
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Кэш" in reply_text or "пуст" in reply_text.lower()


# ---------------------------------------------------------------------------
# Тесты: !contacts search
# ---------------------------------------------------------------------------


class TestContactsSearch:
    """Тесты !contacts search <запрос>."""

    @pytest.mark.asyncio
    async def test_поиск_без_запроса_бросает_ошибку(self) -> None:
        """!contacts search без запроса → UserInputError."""
        bot = _make_bot("search")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"):
            with pytest.raises(UserInputError):
                await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_поиск_без_результатов(self) -> None:
        """Поиск без результатов → 'не найдено'."""
        bot = _make_bot("search Несуществующий")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.search.return_value = []
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "не найдено" in reply_text.lower() or "📭" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_возвращает_результаты(self) -> None:
        """При наличии результатов — отображение в ответе."""
        bot = _make_bot("search Алексей")
        msg = _make_message()
        entry = _make_cache_entry(display_name="Алексей Петров", username="alex_p", peer_id=111)
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.search.return_value = [entry]
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Алексей Петров" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_вызывает_cache_search(self) -> None:
        """contact_cache.search вызывается с переданным запросом."""
        bot = _make_bot("search Ваня")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.search.return_value = []
            await handle_contacts(bot, msg)

        mock_cache.search.assert_called_once_with("Ваня")

    @pytest.mark.asyncio
    async def test_поиск_показывает_количество_результатов(self) -> None:
        """Количество найденных записей указано в заголовке."""
        bot = _make_bot("search Тест")
        msg = _make_message()
        entries = [_make_cache_entry(username=f"u{i}", peer_id=i) for i in range(4)]
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.search.return_value = entries
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "4" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_ограничение_20_результатов(self) -> None:
        """При >20 результатах выводятся первые 20 + пометка."""
        bot = _make_bot("search Имя")
        msg = _make_message()
        entries = [_make_cache_entry(username=f"u{i}", peer_id=i) for i in range(25)]
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.search.return_value = entries
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        # 25 - 20 = 5 оставшихся
        assert "5" in reply_text

    @pytest.mark.asyncio
    async def test_поиск_показывает_alias(self) -> None:
        """aliases контакта видны в результатах поиска."""
        bot = _make_bot("search Боец")
        msg = _make_message()
        entry = _make_cache_entry(aliases=["Боец", "Лёша"])
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.search.return_value = [entry]
            await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Боец" in reply_text


# ---------------------------------------------------------------------------
# Тесты: !contacts alias
# ---------------------------------------------------------------------------


class TestContactsAlias:
    """Тесты !contacts alias <user> <псевдоним>."""

    @pytest.mark.asyncio
    async def test_alias_без_аргументов_бросает_ошибку(self) -> None:
        """!contacts alias без аргументов → UserInputError."""
        bot = _make_bot("alias")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError):
                await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_alias_только_target_без_псевдонима_бросает_ошибку(self) -> None:
        """!contacts alias @user без псевдонима → UserInputError."""
        bot = _make_bot("alias @vasya")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError):
                await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_alias_успешно_через_кэш(self) -> None:
        """Alias добавляется когда контакт есть в кэше."""
        bot = _make_bot("alias @vasya Вася из армии")
        msg = _make_message()
        cached = _make_cache_entry(username="vasya", peer_id=111)
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.lookup.return_value = cached
            mock_cache.add_alias.return_value = True
            with patch(f"{_MODULE}.telegram_resolver"):
                await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "✅" in reply_text
        assert "Вася из армии" in reply_text

    @pytest.mark.asyncio
    async def test_alias_вызывает_add_alias(self) -> None:
        """contact_cache.add_alias вызывается с правильными аргументами."""
        bot = _make_bot("alias @ivan Ваня лучший")
        msg = _make_message()
        cached = _make_cache_entry(username="ivan", peer_id=999)
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.lookup.return_value = cached
            mock_cache.add_alias.return_value = True
            with patch(f"{_MODULE}.telegram_resolver"):
                await handle_contacts(bot, msg)

        mock_cache.add_alias.assert_called_once_with(999, "Ваня лучший")

    @pytest.mark.asyncio
    async def test_alias_не_найден_в_кэше_резолвит(self) -> None:
        """Если контакт не в кэше — резолвится через telegram_resolver."""
        bot = _make_bot("alias @newuser Незнакомец")
        msg = _make_message()
        resolve_result = {
            "ok": True,
            "peer_id": 456,
            "username": "newuser",
            "display_name": "Новый",
            "strategy_used": "get_users",
        }
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.lookup.return_value = None
            mock_cache.add_alias.return_value = True
            with patch(f"{_MODULE}.telegram_resolver") as mock_resolver:
                mock_resolver.resolve_peer = AsyncMock(return_value=resolve_result)
                await handle_contacts(bot, msg)

        mock_resolver.resolve_peer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alias_resolve_fail_сообщает_об_ошибке(self) -> None:
        """Если resolve не удался — предлагает сначала resolve."""
        bot = _make_bot("alias @ghost Призрак")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.lookup.return_value = None
            with patch(f"{_MODULE}.telegram_resolver") as mock_resolver:
                mock_resolver.resolve_peer = AsyncMock(
                    return_value={"ok": False, "tried_strategies": [], "suggestions": []}
                )
                await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "❌" in reply_text or "resolve" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_alias_по_числовому_peer_id(self) -> None:
        """Alias добавляется по числовому peer_id напрямую."""
        bot = _make_bot("alias 123456 Числовой")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache") as mock_cache:
            mock_cache.lookup.return_value = None
            mock_cache.add_alias.return_value = True
            with patch(f"{_MODULE}.telegram_resolver"):
                await handle_contacts(bot, msg)

        mock_cache.add_alias.assert_called_once_with(123456, "Числовой")


# ---------------------------------------------------------------------------
# Тесты: !contacts resolve
# ---------------------------------------------------------------------------


class TestContactsResolve:
    """Тесты !contacts resolve <target>."""

    @pytest.mark.asyncio
    async def test_resolve_без_аргументов_бросает_ошибку(self) -> None:
        """!contacts resolve без аргументов → UserInputError."""
        bot = _make_bot("resolve")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError):
                await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_resolve_успешный(self) -> None:
        """Успешный resolve → сообщение с peer_id и именем."""
        bot = _make_bot("resolve @vasya")
        msg = _make_message()
        resolve_result = {
            "ok": True,
            "peer_id": 555000,
            "username": "vasya",
            "display_name": "Василий",
            "strategy_used": "get_users",
        }
        with patch(f"{_MODULE}.contact_cache"):
            with patch(f"{_MODULE}.telegram_resolver") as mock_resolver:
                mock_resolver.resolve_peer = AsyncMock(return_value=resolve_result)
                await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "✅" in reply_text
        assert "555000" in reply_text
        assert "Василий" in reply_text

    @pytest.mark.asyncio
    async def test_resolve_неудачный(self) -> None:
        """Неудачный resolve → сообщение об ошибке со стратегиями."""
        bot = _make_bot("resolve @nonexistent")
        msg = _make_message()
        resolve_result = {
            "ok": False,
            "peer_id": None,
            "tried_strategies": ["resolve_peer", "get_users", "dialog_scan"],
            "suggestions": ["Убедитесь что аккаунт существует"],
        }
        with patch(f"{_MODULE}.contact_cache"):
            with patch(f"{_MODULE}.telegram_resolver") as mock_resolver:
                mock_resolver.resolve_peer = AsyncMock(return_value=resolve_result)
                await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "❌" in reply_text

    @pytest.mark.asyncio
    async def test_resolve_вызывает_resolver(self) -> None:
        """telegram_resolver.resolve_peer вызывается с правильным target."""
        bot = _make_bot("resolve @testuser")
        msg = _make_message()
        resolve_result = {
            "ok": True,
            "peer_id": 111,
            "username": "testuser",
            "display_name": "Test",
            "strategy_used": "resolve_peer",
        }
        with patch(f"{_MODULE}.contact_cache"):
            with patch(f"{_MODULE}.telegram_resolver") as mock_resolver:
                mock_resolver.resolve_peer = AsyncMock(return_value=resolve_result)
                await handle_contacts(bot, msg)

        mock_resolver.resolve_peer.assert_awaited_once_with(bot.client, "@testuser")

    @pytest.mark.asyncio
    async def test_resolve_показывает_strategy(self) -> None:
        """Использованная стратегия видна в ответе."""
        bot = _make_bot("resolve @user")
        msg = _make_message()
        resolve_result = {
            "ok": True,
            "peer_id": 222,
            "username": "user",
            "display_name": "Юзер",
            "strategy_used": "dialog_scan",
        }
        with patch(f"{_MODULE}.contact_cache"):
            with patch(f"{_MODULE}.telegram_resolver") as mock_resolver:
                mock_resolver.resolve_peer = AsyncMock(return_value=resolve_result)
                await handle_contacts(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "dialog_scan" in reply_text


# ---------------------------------------------------------------------------
# Тесты: неизвестные подкоманды
# ---------------------------------------------------------------------------


class TestContactsUnknownSubcommand:
    """Тесты неизвестных подкоманд."""

    @pytest.mark.asyncio
    async def test_неизвестная_подкоманда_бросает_user_input_error(self) -> None:
        """Неизвестная подкоманда → UserInputError."""
        bot = _make_bot("delete @vasya")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError):
                await handle_contacts(bot, msg)

    @pytest.mark.asyncio
    async def test_ошибка_содержит_search(self) -> None:
        """UserInputError упоминает подкоманду search."""
        bot = _make_bot("unknown")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError) as exc_info:
                await handle_contacts(bot, msg)

        assert "search" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_ошибка_содержит_resolve(self) -> None:
        """UserInputError упоминает подкоманду resolve."""
        bot = _make_bot("badcmd")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError) as exc_info:
                await handle_contacts(bot, msg)

        assert "resolve" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_ошибка_содержит_alias(self) -> None:
        """UserInputError упоминает подкоманду alias."""
        bot = _make_bot("badcmd")
        msg = _make_message()
        with patch(f"{_MODULE}.contact_cache"), patch(f"{_MODULE}.telegram_resolver"):
            with pytest.raises(UserInputError) as exc_info:
                await handle_contacts(bot, msg)

        assert "alias" in exc_info.value.user_message


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
        from src.handlers.command_handlers import handle_contacts as hc

        assert asyncio.iscoroutinefunction(hc)

    def test_handle_contacts_в_all_handlers(self) -> None:
        """handle_contacts есть в __all__ модуля handlers."""
        from src.handlers import __all__ as all_handlers

        assert "handle_contacts" in all_handlers
