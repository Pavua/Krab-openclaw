# -*- coding: utf-8 -*-
"""
Тесты команды !top — лидерборд активности чата.

Покрываем:
- !top (default) — топ-10 за 24ч
- !top N — топ-N участников
- !top week — за неделю
- !top all — за всё время
- пустая история — правильное сообщение
- сообщения без from_user (каналы/сервисные) — пропускаются
- фильтрация по дате — старые сообщения не учитываются
- правильная сортировка по убыванию
- правильные медали для топ-3
- _plural_messages — склонение слова «сообщение»
- неверный аргумент — UserInputError
- ошибка get_chat_history — graceful error
- лимит top_n ограничен 50
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _plural_messages, handle_top

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_user(
    user_id: int = 1,
    username: str | None = None,
    first_name: str = "User",
    last_name: str | None = None,
) -> SimpleNamespace:
    """Mock pyrogram User."""
    return SimpleNamespace(
        id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )


def _make_msg(
    user: SimpleNamespace | None,
    date: datetime.datetime | None = None,
) -> SimpleNamespace:
    """Mock pyrogram Message."""
    if date is None:
        date = datetime.datetime.now(datetime.timezone.utc)
    return SimpleNamespace(from_user=user, date=date)


async def _async_gen(items):
    """Вспомогательный async generator из списка."""
    for item in items:
        yield item


def _make_bot(command_args: str = "") -> SimpleNamespace:
    """Минимальный mock-бот."""
    bot = SimpleNamespace(
        _get_command_args=lambda msg: command_args,
        client=MagicMock(),
        me=SimpleNamespace(id=999),
    )
    return bot


def _make_message(chat_id: int = -100100) -> SimpleNamespace:
    """Минимальный mock-Message."""
    status_msg = AsyncMock()
    status_msg.edit = AsyncMock()
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(return_value=status_msg),
        _status_msg=status_msg,
    )


# ---------------------------------------------------------------------------
# _plural_messages — юнит-тесты склонения
# ---------------------------------------------------------------------------


class TestPluralMessages:
    """Тесты функции склонения _plural_messages."""

    def test_1_сообщение(self):
        assert _plural_messages(1) == "сообщение"

    def test_2_сообщения(self):
        assert _plural_messages(2) == "сообщения"

    def test_4_сообщения(self):
        assert _plural_messages(4) == "сообщения"

    def test_5_сообщений(self):
        assert _plural_messages(5) == "сообщений"

    def test_10_сообщений(self):
        assert _plural_messages(10) == "сообщений"

    def test_11_сообщений_исключение(self):
        """11-19 — всегда «сообщений»."""
        for n in range(11, 20):
            assert _plural_messages(n) == "сообщений", f"failed for {n}"

    def test_21_сообщение(self):
        assert _plural_messages(21) == "сообщение"

    def test_22_сообщения(self):
        assert _plural_messages(22) == "сообщения"

    def test_100_сообщений(self):
        assert _plural_messages(100) == "сообщений"

    def test_101_сообщение(self):
        assert _plural_messages(101) == "сообщение"


# ---------------------------------------------------------------------------
# handle_top — интеграционные тесты с mock-историей
# ---------------------------------------------------------------------------


class TestHandleTopBasic:
    """Базовые сценарии !top."""

    @pytest.mark.asyncio
    async def test_top_default_показывает_заголовок_24ч(self) -> None:
        """!top без аргументов — в ответе есть заголовок с 24ч."""
        bot = _make_bot("")
        user = _make_user(1, username="alice")
        history = [_make_msg(user) for _ in range(5)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        status_msg = msg.reply.return_value
        status_msg.edit.assert_awaited_once()
        text = status_msg.edit.call_args[0][0]
        assert "24ч" in text
        assert "alice" in text

    @pytest.mark.asyncio
    async def test_top_week_показывает_заголовок_неделя(self) -> None:
        """!top week — в ответе есть «неделя»."""
        bot = _make_bot("week")
        user = _make_user(2, username="bob")
        history = [_make_msg(user)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "неделя" in text

    @pytest.mark.asyncio
    async def test_top_all_показывает_всё_время(self) -> None:
        """!top all — в ответе есть «всё время»."""
        bot = _make_bot("all")
        user = _make_user(3, username="charlie")
        history = [_make_msg(user)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "всё время" in text

    @pytest.mark.asyncio
    async def test_top_n_ограничивает_количество(self) -> None:
        """!top 3 — показывает не более 3 участников."""
        bot = _make_bot("3")
        users = [_make_user(i, username=f"user{i}") for i in range(10)]
        # Каждый пользователь шлёт по 1 сообщению
        history = [_make_msg(u) for u in users]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        # В топ-3 должны быть только user0, user1, user2 (все по 1 сообщению, но limit=3)
        lines = [l for l in text.split("\n") if l.strip() and "─" not in l and "Топ" not in l]
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_top_n_больше_50_ограничивается(self) -> None:
        """!top 100 — top_n ограничивается до 50."""
        bot = _make_bot("100")
        # 60 разных пользователей
        users = [_make_user(i, username=f"u{i}") for i in range(60)]
        history = [_make_msg(u) for u in users]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        lines = [l for l in text.split("\n") if l.strip() and "─" not in l and "Топ" not in l]
        assert len(lines) <= 50


class TestHandleTopSorting:
    """Тесты сортировки и правильного подсчёта."""

    @pytest.mark.asyncio
    async def test_сортировка_по_убыванию(self) -> None:
        """Участник с наибольшим количеством сообщений идёт первым."""
        bot = _make_bot("all")
        alice = _make_user(1, username="alice")
        bob = _make_user(2, username="bob")
        charlie = _make_user(3, username="charlie")

        # alice — 3, bob — 1, charlie — 2
        history = [
            _make_msg(alice),
            _make_msg(alice),
            _make_msg(alice),
            _make_msg(charlie),
            _make_msg(charlie),
            _make_msg(bob),
        ]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        lines = text.split("\n")
        # Первая строка с данными (после заголовка и разделителя) — alice
        data_lines = [l for l in lines if "@" in l or "user_" in l]
        assert "alice" in data_lines[0]
        assert "charlie" in data_lines[1]
        assert "bob" in data_lines[2]

    @pytest.mark.asyncio
    async def test_медали_для_топ3(self) -> None:
        """Топ-3 отмечены медалями."""
        bot = _make_bot("all")
        users = [_make_user(i, username=f"u{i}") for i in range(5)]
        # u0 — 5 сообщений, u1 — 4, u2 — 3, u3 — 2, u4 — 1
        history = []
        for i, u in enumerate(users):
            for _ in range(5 - i):
                history.append(_make_msg(u))

        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "🥇" in text
        assert "🥈" in text
        assert "🥉" in text

    @pytest.mark.asyncio
    async def test_счётчик_накапливается_для_одного_пользователя(self) -> None:
        """Один пользователь с несколькими сообщениями — правильный счётчик."""
        bot = _make_bot("all")
        alice = _make_user(1, username="alice")
        history = [_make_msg(alice) for _ in range(42)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "42" in text
        assert "сообщения" in text  # 42 → «сообщения» (42 % 10 == 2)


class TestHandleTopFiltering:
    """Тесты фильтрации сообщений."""

    @pytest.mark.asyncio
    async def test_сообщения_без_from_user_пропускаются(self) -> None:
        """Сообщения каналов/сервисные (from_user=None) не считаются."""
        bot = _make_bot("all")
        alice = _make_user(1, username="alice")
        history = [
            _make_msg(None),  # канал — пропустить
            _make_msg(None),  # сервисное — пропустить
            _make_msg(alice),  # живой пользователь
        ]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "alice" in text
        assert "1 " in text  # 1 сообщение

    @pytest.mark.asyncio
    async def test_пустая_история_возвращает_заглушку(self) -> None:
        """Пустой чат — «нет сообщений»."""
        bot = _make_bot("")
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([]))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "Нет" in text or "нет" in text or "📭" in text

    @pytest.mark.asyncio
    async def test_только_канальные_сообщения_возвращает_заглушку(self) -> None:
        """Все сообщения от каналов — «нет сообщений»."""
        bot = _make_bot("all")
        history = [_make_msg(None) for _ in range(10)]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "📭" in text or "Нет" in text or "нет" in text

    @pytest.mark.asyncio
    async def test_фильтрация_по_дате_24ч(self) -> None:
        """!top — старые сообщения (> 24ч) не считаются при прерывании итерации."""
        bot = _make_bot("")
        alice = _make_user(1, username="alice")

        now = datetime.datetime.now(datetime.timezone.utc)
        old = now - datetime.timedelta(hours=48)

        # Одно свежее и одно старое
        # Но т.к. история идёт от новых к старым и мы break при cutoff,
        # старое сообщение вообще не попадёт в счёт
        history = [
            _make_msg(alice, date=now - datetime.timedelta(minutes=30)),
            _make_msg(alice, date=old),  # это прекратит итерацию
        ]
        bot.client.get_chat_history = MagicMock(return_value=_async_gen(history))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        # alice с 1 сообщением (только свежее)
        assert "alice" in text
        assert "1 " in text


class TestHandleTopDisplayNames:
    """Тесты форматирования имён участников."""

    @pytest.mark.asyncio
    async def test_username_отображается_с_собакой(self) -> None:
        """@username — отображается с @."""
        bot = _make_bot("all")
        user = _make_user(1, username="testuser")
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([_make_msg(user)]))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "@testuser" in text

    @pytest.mark.asyncio
    async def test_имя_фамилия_без_username(self) -> None:
        """Без username — показывается first_name + last_name."""
        bot = _make_bot("all")
        user = _make_user(2, username=None, first_name="Иван", last_name="Иванов")
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([_make_msg(user)]))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "Иван Иванов" in text

    @pytest.mark.asyncio
    async def test_только_first_name(self) -> None:
        """Только first_name, без last_name и username."""
        bot = _make_bot("all")
        user = _make_user(3, username=None, first_name="Мария", last_name=None)
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([_make_msg(user)]))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "Мария" in text

    @pytest.mark.asyncio
    async def test_fallback_user_id(self) -> None:
        """Без имени и username — показывается user_<id>."""
        bot = _make_bot("all")
        user = _make_user(42, username=None, first_name=None, last_name=None)
        user.first_name = None
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([_make_msg(user)]))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "user_42" in text


class TestHandleTopErrors:
    """Тесты ошибочных входных данных и обработки исключений."""

    @pytest.mark.asyncio
    async def test_неверный_аргумент_raises_userinputerror(self) -> None:
        """!top foobar — UserInputError."""
        bot = _make_bot("foobar")
        msg = _make_message()

        with pytest.raises(UserInputError):
            await handle_top(bot, msg)

    @pytest.mark.asyncio
    async def test_отрицательное_число_нормализуется(self) -> None:
        """!top -5 — ограничивается до 1."""
        bot = _make_bot("-5")
        user = _make_user(1, username="a")
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([_make_msg(user)]))

        msg = _make_message()
        # Не должно упасть — нормализуется до max(1, min(-5, 50)) = 1
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        lines = [l for l in text.split("\n") if "@" in l or "user_" in l]
        assert len(lines) <= 1

    @pytest.mark.asyncio
    async def test_ошибка_get_chat_history_graceful(self) -> None:
        """Если get_chat_history бросает исключение — graceful error в edit."""
        bot = _make_bot("all")

        async def _broken_gen(*args, **kwargs):
            raise RuntimeError("Telegram error")
            yield  # noqa: unreachable

        bot.client.get_chat_history = MagicMock(return_value=_broken_gen())

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_0_аргумент_нормализуется_до_1(self) -> None:
        """!top 0 — нормализуется до 1."""
        bot = _make_bot("0")
        user = _make_user(1, username="zeta")
        bot.client.get_chat_history = MagicMock(return_value=_async_gen([_make_msg(user)]))

        msg = _make_message()
        await handle_top(bot, msg)

        text = msg.reply.return_value.edit.call_args[0][0]
        assert "zeta" in text
