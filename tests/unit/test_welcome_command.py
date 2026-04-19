# -*- coding: utf-8 -*-
"""
Тесты для !welcome — автоприветствие новых участников.

Покрываем:
- _load_welcome_config / _save_welcome_config (файловый слой)
- _render_welcome_text (подстановка переменных)
- handle_welcome: set / off / status / test / неизвестная подкоманда
- handle_new_chat_members: приветствие отправляется / пропускается
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _load_welcome_config,
    _render_welcome_text,
    _save_welcome_config,
    handle_new_chat_members,
    handle_welcome,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(
    text: str,
    chat_id: int = 100,
    user_id: int = 42,
    first_name: str = "Тест",
    username: str | None = "testuser",
    chat_title: str = "Тестовый чат",
) -> SimpleNamespace:
    user = SimpleNamespace(id=user_id, first_name=first_name, username=username)
    chat = SimpleNamespace(id=chat_id, title=chat_title)
    return SimpleNamespace(
        text=text,
        from_user=user,
        chat=chat,
        new_chat_members=None,
        reply=AsyncMock(),
    )


def _make_bot() -> SimpleNamespace:
    return SimpleNamespace()


# ---------------------------------------------------------------------------
# _render_welcome_text
# ---------------------------------------------------------------------------


class TestRenderWelcomeText:
    def test_все_переменные_подставляются(self):
        result = _render_welcome_text(
            "Привет, {name} ({username})! Добро пожаловать в {chat}. Нас {count}.",
            name="Иван",
            username="@ivan",
            chat="Тусовка",
            count=5,
        )
        assert result == "Привет, Иван (@ivan)! Добро пожаловать в Тусовка. Нас 5."

    def test_пустой_шаблон(self):
        result = _render_welcome_text("", name="A", username="B", chat="C", count=0)
        assert result == ""

    def test_шаблон_без_переменных(self):
        result = _render_welcome_text("Привет всем!", name="A", username="B", chat="C", count=1)
        assert result == "Привет всем!"

    def test_только_name(self):
        result = _render_welcome_text(
            "Привет, {name}!", name="Оля", username="@olya", chat="X", count=1
        )
        assert result == "Привет, Оля!"

    def test_только_count(self):
        result = _render_welcome_text(
            "Участников: {count}", name="A", username="B", chat="C", count=42
        )
        assert result == "Участников: 42"

    def test_дубликаты_переменных(self):
        result = _render_welcome_text("{name} {name}", name="Коля", username="u", chat="c", count=1)
        assert result == "Коля Коля"


# ---------------------------------------------------------------------------
# _load_welcome_config / _save_welcome_config
# ---------------------------------------------------------------------------


class TestWelcomeConfigIO:
    def test_load_отсутствующего_файла(self, tmp_path):
        missing = tmp_path / "no_file.json"
        with patch("src.handlers.command_handlers._WELCOME_FILE", missing):
            assert _load_welcome_config() == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        data = {"100": {"enabled": True, "template": "Привет, {name}!"}}
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            _save_welcome_config(data)
            loaded = _load_welcome_config()
        assert loaded == data

    def test_load_битого_json(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text("not valid json", encoding="utf-8")
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            result = _load_welcome_config()
        assert result == {}

    def test_save_создаёт_папку(self, tmp_path):
        nested = tmp_path / "a" / "b" / "welcome_messages.json"
        with patch("src.handlers.command_handlers._WELCOME_FILE", nested):
            _save_welcome_config({"x": 1})
        assert nested.exists()
        assert json.loads(nested.read_text())["x"] == 1


# ---------------------------------------------------------------------------
# handle_welcome — !welcome set
# ---------------------------------------------------------------------------


class TestHandleWelcomeSet:
    @pytest.mark.asyncio
    async def test_set_сохраняет_шаблон(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome set Привет, {name}!")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        msg.reply.assert_awaited_once()
        text = msg.reply.await_args.args[0]
        assert "установлено" in text
        assert "Привет, {name}!" in text
        # Файл содержит правильные данные
        data = json.loads(f.read_text())
        assert "100" in data
        assert data["100"]["enabled"] is True
        assert data["100"]["template"] == "Привет, {name}!"

    @pytest.mark.asyncio
    async def test_set_без_текста_вызывает_ошибку(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome set")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            with pytest.raises(UserInputError):
                await handle_welcome(bot, msg)

    @pytest.mark.asyncio
    async def test_set_перезаписывает_старый_шаблон(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        existing = {"100": {"enabled": True, "template": "Старый"}}
        f.write_text(json.dumps(existing), encoding="utf-8")
        msg = _make_message("!welcome set Новый {name}!")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        data = json.loads(f.read_text())
        assert data["100"]["template"] == "Новый {name}!"


# ---------------------------------------------------------------------------
# handle_welcome — !welcome off
# ---------------------------------------------------------------------------


class TestHandleWelcomeOff:
    @pytest.mark.asyncio
    async def test_off_выключает_приветствие(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(json.dumps({"100": {"enabled": True, "template": "Hi"}}), encoding="utf-8")
        msg = _make_message("!welcome off")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        msg.reply.assert_awaited_once()
        assert "выключено" in msg.reply.await_args.args[0]
        data = json.loads(f.read_text())
        assert data["100"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_off_без_существующего_конфига(self, tmp_path):
        """off на чат без конфига — не падает, отвечает выключено."""
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome off")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        msg.reply.assert_awaited_once()
        assert "выключено" in msg.reply.await_args.args[0]


# ---------------------------------------------------------------------------
# handle_welcome — !welcome status / show
# ---------------------------------------------------------------------------


class TestHandleWelcomeStatus:
    @pytest.mark.asyncio
    async def test_status_когда_включено(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "Привет!"}}), encoding="utf-8"
        )
        msg = _make_message("!welcome status")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "включено" in text
        assert "Привет!" in text

    @pytest.mark.asyncio
    async def test_status_когда_выключено(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(json.dumps({"100": {"enabled": False, "template": "X"}}), encoding="utf-8")
        msg = _make_message("!welcome status")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "не настроено" in text or "выключено" in text

    @pytest.mark.asyncio
    async def test_status_без_конфига(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome status")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "не настроено" in text

    @pytest.mark.asyncio
    async def test_show_алиас_status(self, tmp_path):
        """!welcome show — алиас status."""
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome show")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_без_аргументов_показывает_статус(self, tmp_path):
        """!welcome без аргументов — статус."""
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle_welcome — !welcome test
# ---------------------------------------------------------------------------


class TestHandleWelcomeTest:
    @pytest.mark.asyncio
    async def test_test_preview_с_данными_юзера(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps(
                {"100": {"enabled": True, "template": "Привет, {name} ({username}) в {chat}!"}}
            ),
            encoding="utf-8",
        )
        msg = _make_message(
            "!welcome test", first_name="Вася", username="vasya", chat_title="Беседка"
        )
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Preview" in text
        assert "Вася" in text
        assert "@vasya" in text
        assert "Беседка" in text

    @pytest.mark.asyncio
    async def test_test_без_конфига_вызывает_ошибку(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome test")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            with pytest.raises(UserInputError):
                await handle_welcome(bot, msg)

    @pytest.mark.asyncio
    async def test_test_при_выключенном_приветствии_вызывает_ошибку(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(json.dumps({"100": {"enabled": False, "template": "X"}}), encoding="utf-8")
        msg = _make_message("!welcome test")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            with pytest.raises(UserInputError):
                await handle_welcome(bot, msg)

    @pytest.mark.asyncio
    async def test_test_юзер_без_username(self, tmp_path):
        """Если username None — используем first_name вместо @handle."""
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "{username}"}}),
            encoding="utf-8",
        )
        msg = _make_message("!welcome test", first_name="Аноним", username=None)
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_welcome(bot, msg)
        text = msg.reply.await_args.args[0]
        # Без username — используем first_name как fallback
        assert "Аноним" in text


# ---------------------------------------------------------------------------
# handle_welcome — неизвестная подкоманда
# ---------------------------------------------------------------------------


class TestHandleWelcomeUnknown:
    @pytest.mark.asyncio
    async def test_неизвестная_подкоманда_вызывает_ошибку(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("!welcome foobar")
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            with pytest.raises(UserInputError):
                await handle_welcome(bot, msg)


# ---------------------------------------------------------------------------
# handle_new_chat_members
# ---------------------------------------------------------------------------


def _make_member(first_name: str, username: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(first_name=first_name, username=username)


class TestHandleNewChatMembers:
    @pytest.mark.asyncio
    async def test_приветствие_отправляется_новому_участнику(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "Привет, {name}!"}}),
            encoding="utf-8",
        )
        member = _make_member("Петя", "petya")
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = [member]
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        msg.reply.assert_awaited_once()
        text = msg.reply.await_args.args[0]
        assert "Петя" in text

    @pytest.mark.asyncio
    async def test_несколько_участников_несколько_ответов(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "Привет, {name}!"}}),
            encoding="utf-8",
        )
        members = [_make_member("Аня"), _make_member("Боря"), _make_member("Вася")]
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = members
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        assert msg.reply.await_count == 3

    @pytest.mark.asyncio
    async def test_нет_конфига_не_отправляет(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = [_make_member("Петя")]
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_приветствие_выключено_не_отправляет(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(json.dumps({"100": {"enabled": False, "template": "X"}}), encoding="utf-8")
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = [_make_member("Коля")]
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_другой_чат_не_получает_приветствие(self, tmp_path):
        """Конфиг есть только для чата 100, событие пришло из 200."""
        f = tmp_path / "welcome_messages.json"
        f.write_text(json.dumps({"100": {"enabled": True, "template": "Hi"}}), encoding="utf-8")
        msg = _make_message("", chat_id=200)
        msg.new_chat_members = [_make_member("Люба")]
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_member_без_username_использует_имя(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "Привет, {username}!"}}),
            encoding="utf-8",
        )
        member = _make_member("Игорь", username=None)
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = [member]
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Игорь" in text
        assert "@" not in text

    @pytest.mark.asyncio
    async def test_ошибка_отправки_не_крашит_обработчик(self, tmp_path):
        """Если reply() бросает исключение — обработчик не падает (warning)."""
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "Hi {name}!"}}),
            encoding="utf-8",
        )
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = [_make_member("Кто-то")]
        msg.reply = AsyncMock(side_effect=RuntimeError("flood"))
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            # Не должно бросать исключение
            await handle_new_chat_members(bot, msg)

    @pytest.mark.asyncio
    async def test_count_в_шаблоне_равен_числу_вошедших(self, tmp_path):
        f = tmp_path / "welcome_messages.json"
        f.write_text(
            json.dumps({"100": {"enabled": True, "template": "Войдёт {count} чел."}}),
            encoding="utf-8",
        )
        members = [_make_member("А"), _make_member("Б")]
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = members
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        # Оба вызова должны содержать "2 чел."
        for call in msg.reply.await_args_list:
            assert "2 чел." in call.args[0]

    @pytest.mark.asyncio
    async def test_new_chat_members_none_не_крашит(self, tmp_path):
        """Если new_chat_members == None (edge case) — тихо выходим."""
        f = tmp_path / "welcome_messages.json"
        f.write_text(json.dumps({"100": {"enabled": True, "template": "Hi"}}), encoding="utf-8")
        msg = _make_message("", chat_id=100)
        msg.new_chat_members = None
        bot = _make_bot()
        with patch("src.handlers.command_handlers._WELCOME_FILE", f):
            await handle_new_chat_members(bot, msg)
        msg.reply.assert_not_awaited()
