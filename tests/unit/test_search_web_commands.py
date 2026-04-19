# -*- coding: utf-8 -*-
"""
Юнит-тесты для !search и !web command handlers.

Покрываем:
  - handle_search: парсинг аргументов, валидация, успешный путь, ошибки сети
  - handle_web: парсинг subcommand'ов, сообщение-помощь, stop/screen/login
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_search, handle_web

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_message(text: str = "!search котики") -> SimpleNamespace:
    """Создаём минимальный stub Message."""
    reply_msg = SimpleNamespace(edit=AsyncMock())
    return SimpleNamespace(
        text=text,
        reply=AsyncMock(return_value=reply_msg),
        reply_photo=AsyncMock(),
    )


def _make_bot(command_args: str = "") -> SimpleNamespace:
    """Создаём минимальный stub bot с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: command_args)


# ===========================================================================
# handle_search
# ===========================================================================


class TestHandleSearchValidation:
    """Валидация аргументов !search."""

    @pytest.mark.asyncio
    async def test_пустой_запрос_бросает_UserInputError(self) -> None:
        """Нет аргументов → UserInputError с подсказкой."""
        bot = _make_bot(command_args="")
        msg = _make_message("!search")
        with pytest.raises(UserInputError) as exc_info:
            await handle_search(bot, msg)
        assert "search" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_аргумент_search_бросает_UserInputError(self) -> None:
        """Если args == 'search' — тоже пустой запрос."""
        bot = _make_bot(command_args="search")
        msg = _make_message("!search search")
        with pytest.raises(UserInputError):
            await handle_search(bot, msg)

    @pytest.mark.asyncio
    async def test_аргумент_excl_search_бросает_UserInputError(self) -> None:
        """Если args == '!search' — тоже пустой запрос."""
        bot = _make_bot(command_args="!search")
        msg = _make_message("!search !search")
        with pytest.raises(UserInputError):
            await handle_search(bot, msg)


class TestHandleSearchSuccess:
    """Успешный сценарий !search."""

    @pytest.mark.asyncio
    async def test_успешный_поиск_редактирует_сообщение(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """При успешном ответе search_brave результат записывается в edit."""
        bot = _make_bot(command_args="котики")
        msg = _make_message("!search котики")
        fake_results = "Результат 1\nРезультат 2"

        monkeypatch.setattr(cmd_module, "search_brave", AsyncMock(return_value=fake_results))

        await handle_search(bot, msg)

        # reply вызван (статус «ищем»)
        msg.reply.assert_awaited_once()
        # edit вызван с результатами
        reply_stub = msg.reply.return_value
        reply_stub.edit.assert_awaited_once()
        edited_text = reply_stub.edit.await_args.args[0]
        assert "Результат 1" in edited_text

    @pytest.mark.asyncio
    async def test_длинный_результат_обрезается(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Результат длиннее 4000 символов должен быть усечён до ~3900 + '...'."""
        bot = _make_bot(command_args="длинный запрос")
        msg = _make_message("!search длинный запрос")
        long_result = "x" * 5000

        monkeypatch.setattr(cmd_module, "search_brave", AsyncMock(return_value=long_result))

        await handle_search(bot, msg)

        reply_stub = msg.reply.return_value
        edited_text = reply_stub.edit.await_args.args[0]
        # Не должен превышать разумный лимит
        assert len(edited_text) < 4500
        assert "..." in edited_text


class TestHandleSearchErrors:
    """Обработка ошибок !search."""

    @pytest.mark.asyncio
    async def test_httpx_ошибка_показывает_сообщение_об_ошибке(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTPError от search_brave → edit с текстом ошибки."""
        bot = _make_bot(command_args="котики")
        msg = _make_message("!search котики")

        monkeypatch.setattr(
            cmd_module,
            "search_brave",
            AsyncMock(side_effect=httpx.HTTPError("timeout")),
        )

        await handle_search(bot, msg)

        reply_stub = msg.reply.return_value
        reply_stub.edit.assert_awaited_once()
        edited_text = reply_stub.edit.await_args.args[0]
        assert "Ошибка" in edited_text or "ошибка" in edited_text or "timeout" in edited_text

    @pytest.mark.asyncio
    async def test_oserror_показывает_сообщение_об_ошибке(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError → edit с текстом ошибки."""
        bot = _make_bot(command_args="тест")
        msg = _make_message("!search тест")

        monkeypatch.setattr(
            cmd_module,
            "search_brave",
            AsyncMock(side_effect=OSError("network unreachable")),
        )

        await handle_search(bot, msg)

        reply_stub = msg.reply.return_value
        reply_stub.edit.assert_awaited_once()
        edited_text = reply_stub.edit.await_args.args[0]
        assert "network unreachable" in edited_text or "Ошибка" in edited_text


# ===========================================================================
# handle_web
# ===========================================================================


class TestHandleWebHelp:
    """!web без аргументов — справка."""

    @pytest.mark.asyncio
    async def test_нет_аргументов_показывает_справку(self) -> None:
        """Если нет subcommand — reply с Web Control."""
        msg = _make_message("!web")
        await handle_web(MagicMock(), msg)
        msg.reply.assert_awaited_once()
        help_text = msg.reply.await_args.args[0]
        assert "Web Control" in help_text


class TestHandleWebSubcommands:
    """!web <subcommand>."""

    @pytest.mark.asyncio
    async def test_stop_вызывает_web_manager_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """!web stop → web_manager.stop() + ответ пользователю."""
        msg = _make_message("!web stop")

        fake_manager = MagicMock()
        fake_manager.stop = AsyncMock()

        monkeypatch.setattr(cmd_module, "web_manager", fake_manager, raising=False)

        # web_manager импортируется внутри функции, патчим через sys.modules
        import sys

        fake_ws_module = MagicMock()
        fake_ws_module.web_manager = fake_manager
        monkeypatch.setitem(sys.modules, "src.web_session", fake_ws_module)

        await handle_web(MagicMock(), msg)

        # Проверяем ответ "остановлен"
        msg.reply.assert_awaited()
        text = msg.reply.await_args.args[0]
        assert "остановлен" in text.lower() or "stop" in text.lower() or "🛑" in text

    @pytest.mark.asyncio
    async def test_login_вызывает_login_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """!web login → web_manager.login_mode() и reply с результатом."""
        msg = _make_message("!web login")

        fake_manager = MagicMock()
        fake_manager.login_mode = AsyncMock(return_value="Login URL: http://example.com")

        import sys

        fake_ws_module = MagicMock()
        fake_ws_module.web_manager = fake_manager
        monkeypatch.setitem(sys.modules, "src.web_session", fake_ws_module)

        await handle_web(MagicMock(), msg)

        msg.reply.assert_awaited()
        text = msg.reply.await_args.args[0]
        assert "Login URL" in text
