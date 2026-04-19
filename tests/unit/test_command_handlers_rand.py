# -*- coding: utf-8 -*-
"""
Тесты обработчика !rand — генератор случайных значений.
"""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_rand


def _make_bot(args: str = "") -> SimpleNamespace:
    """Создаёт упрощённый мок бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: args)


def _make_message() -> SimpleNamespace:
    """Создаёт мок сообщения с AsyncMock reply."""
    return SimpleNamespace(reply=AsyncMock())


# ---------------------------------------------------------------------------
# Базовые числовые режимы
# ---------------------------------------------------------------------------


class TestRandNoArgs:
    """!rand без аргументов — число 1–100."""

    @pytest.mark.asyncio
    async def test_ответ_содержит_число(self):
        bot = _make_bot("")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "🎲" in text
        # Извлекаем число из ответа
        m = re.search(r"\d+", text)
        assert m is not None
        n = int(m.group())
        assert 1 <= n <= 100

    @pytest.mark.asyncio
    async def test_несколько_вызовов_дают_разброс(self):
        """10 вызовов должны дать хотя бы 2 разных результата (крайне маловероятно, что все одинаковы)."""
        results = set()
        for _ in range(10):
            bot = _make_bot("")
            msg = _make_message()
            await handle_rand(bot, msg)
            text = msg.reply.await_args.args[0]
            m = re.search(r"\d+", text)
            results.add(int(m.group()))
        assert len(results) > 1


class TestRandN:
    """!rand N — число 1..N."""

    @pytest.mark.asyncio
    async def test_rand_50_в_диапазоне(self):
        bot = _make_bot("50")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        n = int(re.search(r"\d+", text).group())
        assert 1 <= n <= 50

    @pytest.mark.asyncio
    async def test_rand_1_всегда_1(self):
        bot = _make_bot("1")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "1" in text

    @pytest.mark.asyncio
    async def test_rand_0_вызывает_ошибку(self):
        bot = _make_bot("0")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_rand_отрицательное_вызывает_ошибку(self):
        bot = _make_bot("-5")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_rand_большое_n(self):
        bot = _make_bot("1000000")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        n = int(re.search(r"\d+", text).group())
        assert 1 <= n <= 1_000_000


class TestRandNM:
    """!rand N M — число N..M."""

    @pytest.mark.asyncio
    async def test_rand_5_10_в_диапазоне(self):
        bot = _make_bot("5 10")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        n = int(re.search(r"\d+", text).group())
        assert 5 <= n <= 10

    @pytest.mark.asyncio
    async def test_rand_обратный_порядок(self):
        """!rand 10 5 — порядок не важен, результат 5..10."""
        bot = _make_bot("10 5")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        n = int(re.search(r"\d+", text).group())
        assert 5 <= n <= 10

    @pytest.mark.asyncio
    async def test_rand_отрицательные_диапазон(self):
        """!rand -10 -3 — отрицательный диапазон."""
        bot = _make_bot("-10 -3")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        m = re.search(r"-?\d+", text)
        n = int(m.group())
        assert -10 <= n <= -3

    @pytest.mark.asyncio
    async def test_rand_nm_второй_аргумент_не_число_вызывает_ошибку(self):
        bot = _make_bot("5 abc")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)


# ---------------------------------------------------------------------------
# Специальные режимы
# ---------------------------------------------------------------------------


class TestRandCoin:
    """!rand coin — орёл или решка."""

    @pytest.mark.asyncio
    async def test_результат_орёл_или_решка(self):
        for _ in range(5):
            bot = _make_bot("coin")
            msg = _make_message()
            await handle_rand(bot, msg)
            text = msg.reply.await_args.args[0]
            assert text in ("Орёл 🦅", "Решка 🪙")

    @pytest.mark.asyncio
    async def test_coin_дает_разброс(self):
        """10 бросков — должны быть оба результата."""
        results = set()
        for _ in range(10):
            bot = _make_bot("coin")
            msg = _make_message()
            await handle_rand(bot, msg)
            results.add(msg.reply.await_args.args[0])
        assert len(results) == 2


class TestRandDice:
    """!rand dice — кубик 1–6."""

    @pytest.mark.asyncio
    async def test_результат_1_6(self):
        for _ in range(10):
            bot = _make_bot("dice")
            msg = _make_message()
            await handle_rand(bot, msg)
            text = msg.reply.await_args.args[0]
            assert "🎲" in text
            n = int(re.search(r"\d+", text).group())
            assert 1 <= n <= 6

    @pytest.mark.asyncio
    async def test_dice_дает_разброс(self):
        results = set()
        for _ in range(30):
            bot = _make_bot("dice")
            msg = _make_message()
            await handle_rand(bot, msg)
            text = msg.reply.await_args.args[0]
            results.add(int(re.search(r"\d+", text).group()))
        assert len(results) > 1


class TestRandPick:
    """!rand pick — выбор из списка."""

    @pytest.mark.asyncio
    async def test_выбирает_из_списка(self):
        bot = _make_bot("pick яблоко, банан, вишня")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert any(item in text for item in ["яблоко", "банан", "вишня"])

    @pytest.mark.asyncio
    async def test_выбирает_один_элемент(self):
        """Только один элемент должен попасть в ответ."""
        bot = _make_bot("pick яблоко, банан")
        msg = _make_message()
        # Подменяем random.choice, чтобы зафиксировать результат
        with patch("random.choice", return_value="яблоко"):
            await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "яблоко" in text

    @pytest.mark.asyncio
    async def test_pick_без_аргументов_вызывает_ошибку(self):
        bot = _make_bot("pick")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_pick_один_элемент_вызывает_ошибку(self):
        """Один элемент без запятой — ошибка."""
        bot = _make_bot("pick яблоко")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_pick_пробелы_вокруг_элементов_игнорируются(self):
        bot = _make_bot("pick   один  ,   два  ,   три  ")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert any(item in text for item in ["один", "два", "три"])

    @pytest.mark.asyncio
    async def test_pick_два_элемента(self):
        bot = _make_bot("pick a, b")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "a" in text or "b" in text


class TestRandPass:
    """!rand pass — генерация пароля."""

    @pytest.mark.asyncio
    async def test_пароль_по_умолчанию_16_символов(self):
        bot = _make_bot("pass")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        # Извлекаем пароль из backtick-блока
        m = re.search(r"`([^`]+)`", text)
        assert m is not None
        assert len(m.group(1)) == 16

    @pytest.mark.asyncio
    async def test_пароль_заданной_длины(self):
        bot = _make_bot("pass 32")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        m = re.search(r"`([^`]+)`", text)
        assert m is not None
        assert len(m.group(1)) == 32

    @pytest.mark.asyncio
    async def test_пароль_минимальная_длина_4(self):
        bot = _make_bot("pass 4")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        m = re.search(r"`([^`]+)`", text)
        assert m is not None
        assert len(m.group(1)) == 4

    @pytest.mark.asyncio
    async def test_пароль_максимальная_длина_128(self):
        bot = _make_bot("pass 128")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        m = re.search(r"`([^`]+)`", text)
        assert m is not None
        assert len(m.group(1)) == 128

    @pytest.mark.asyncio
    async def test_пароль_длина_3_слишком_мала(self):
        bot = _make_bot("pass 3")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_пароль_длина_129_слишком_велика(self):
        bot = _make_bot("pass 129")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_пароль_нечисловая_длина_вызывает_ошибку(self):
        bot = _make_bot("pass abc")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_rand(bot, msg)

    @pytest.mark.asyncio
    async def test_пароль_содержит_только_допустимые_символы(self):
        import string

        allowed = set(string.ascii_letters + string.digits + "!@#$%^&*-_=+")
        bot = _make_bot("pass 64")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        m = re.search(r"`([^`]+)`", text)
        assert m is not None
        for ch in m.group(1):
            assert ch in allowed

    @pytest.mark.asyncio
    async def test_пароль_ответ_содержит_ключ_эмодзи(self):
        bot = _make_bot("pass")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "🔑" in text


class TestRandUUID:
    """!rand uuid — генерация UUID4."""

    @pytest.mark.asyncio
    async def test_ответ_содержит_корректный_uuid4(self):
        bot = _make_bot("uuid")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        # UUID должен быть в формате xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        m = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            text,
            re.IGNORECASE,
        )
        assert m is not None
        # Проверяем что это валидный UUID
        uid = uuid.UUID(m.group())
        assert uid.version == 4

    @pytest.mark.asyncio
    async def test_два_uuid_различаются(self):
        results = set()
        for _ in range(3):
            bot = _make_bot("uuid")
            msg = _make_message()
            await handle_rand(bot, msg)
            text = msg.reply.await_args.args[0]
            m = re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.IGNORECASE
            )
            results.add(m.group())
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_uuid_в_backtick_блоке(self):
        bot = _make_bot("uuid")
        msg = _make_message()
        await handle_rand(bot, msg)
        text = msg.reply.await_args.args[0]
        assert text.startswith("`") and text.endswith("`")


# ---------------------------------------------------------------------------
# Неизвестная подкоманда — справка
# ---------------------------------------------------------------------------


class TestRandHelpOnUnknown:
    """Неизвестная подкоманда → UserInputError со справкой."""

    @pytest.mark.asyncio
    async def test_неизвестная_команда_вызывает_ошибку(self):
        bot = _make_bot("unknown_subcmd")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_rand(bot, msg)
        assert "rand" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_справка_содержит_все_подкоманды(self):
        bot = _make_bot("invalid")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_rand(bot, msg)
        text = exc_info.value.user_message
        for keyword in ["coin", "dice", "pick", "pass", "uuid"]:
            assert keyword in text
