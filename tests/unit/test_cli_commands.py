# -*- coding: utf-8 -*-
"""
test_cli_commands — Phase 2 Wave 12 (Session 27).

Проверяем:
1. Re-exports из command_handlers доступны (TestReExports).
2. Модуль cli_commands корректно экспортирует все handlers.
3. Базовые сценарии: handle_codex, handle_gemini_cli, handle_claude_cli,
   handle_opencode, handle_hs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.commands.cli_commands as cli_commands_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _cli_keepalive,
    _run_cli_with_progress,
    handle_claude_cli,
    handle_codex,
    handle_gemini_cli,
    handle_hs,
    handle_opencode,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(text: str = "!codex hello") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=1, username="owner"),
        chat=SimpleNamespace(id=100),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="test prompt")
    return bot


# ---------------------------------------------------------------------------
# 1. Re-exports
# ---------------------------------------------------------------------------


class TestReExports:
    """Проверяем что command_handlers корректно re-экспортирует всё из cli_commands."""

    def test_handle_codex_importable(self):
        assert callable(handle_codex)

    def test_handle_gemini_cli_importable(self):
        assert callable(handle_gemini_cli)

    def test_handle_claude_cli_importable(self):
        assert callable(handle_claude_cli)

    def test_handle_opencode_importable(self):
        assert callable(handle_opencode)

    def test_handle_hs_importable(self):
        assert callable(handle_hs)

    def test_run_cli_with_progress_importable(self):
        assert callable(_run_cli_with_progress)

    def test_cli_keepalive_importable(self):
        assert callable(_cli_keepalive)


# ---------------------------------------------------------------------------
# 2. Module exports
# ---------------------------------------------------------------------------


class TestModuleExports:
    """Проверяем что cli_commands экспортирует ожидаемые символы."""

    def test_has_handle_codex(self):
        assert hasattr(cli_commands_module, "handle_codex")

    def test_has_handle_gemini_cli(self):
        assert hasattr(cli_commands_module, "handle_gemini_cli")

    def test_has_handle_claude_cli(self):
        assert hasattr(cli_commands_module, "handle_claude_cli")

    def test_has_handle_opencode(self):
        assert hasattr(cli_commands_module, "handle_opencode")

    def test_has_handle_hs(self):
        assert hasattr(cli_commands_module, "handle_hs")

    def test_has_run_cli_with_progress(self):
        assert hasattr(cli_commands_module, "_run_cli_with_progress")

    def test_has_cli_keepalive(self):
        assert hasattr(cli_commands_module, "_cli_keepalive")


# ---------------------------------------------------------------------------
# 3. handle_codex
# ---------------------------------------------------------------------------


class TestHandleCodex:
    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self):
        bot = _make_bot()
        bot._get_command_args.return_value = ""
        msg = _make_message("!codex")
        with pytest.raises(UserInputError):
            await handle_codex(bot, msg)

    @pytest.mark.asyncio
    async def test_calls_run_cli(self):
        bot = _make_bot()
        msg = _make_message("!codex hello")

        fake_result = SimpleNamespace(output="ok", exit_code=0, timed_out=False)

        with patch("src.integrations.cli_runner.run_cli", new=AsyncMock(return_value=fake_result)) as mock_run:
            # Patch _split_text_for_telegram via command_handlers namespace
            import src.handlers.command_handlers as _ch
            original_split = _ch._split_text_for_telegram
            try:
                _ch._split_text_for_telegram = lambda t, limit=3900: [t]
                await handle_codex(bot, msg)
            finally:
                _ch._split_text_for_telegram = original_split

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == "codex"


# ---------------------------------------------------------------------------
# 4. handle_gemini_cli
# ---------------------------------------------------------------------------


class TestHandleGeminiCli:
    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self):
        bot = _make_bot()
        bot._get_command_args.return_value = ""
        msg = _make_message("!gemini")
        with pytest.raises(UserInputError):
            await handle_gemini_cli(bot, msg)

    @pytest.mark.asyncio
    async def test_calls_run_cli_with_gemini(self):
        bot = _make_bot()
        msg = _make_message("!gemini hello")

        fake_result = SimpleNamespace(output="ok", exit_code=0, timed_out=False)
        with patch("src.integrations.cli_runner.run_cli", new=AsyncMock(return_value=fake_result)) as mock_run:
            import src.handlers.command_handlers as _ch
            original_split = _ch._split_text_for_telegram
            try:
                _ch._split_text_for_telegram = lambda t, limit=3900: [t]
                await handle_gemini_cli(bot, msg)
            finally:
                _ch._split_text_for_telegram = original_split

            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == "gemini"


# ---------------------------------------------------------------------------
# 5. handle_claude_cli
# ---------------------------------------------------------------------------


class TestHandleClaudeCli:
    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self):
        bot = _make_bot()
        bot._get_command_args.return_value = ""
        msg = _make_message("!claude_cli")
        with pytest.raises(UserInputError):
            await handle_claude_cli(bot, msg)

    @pytest.mark.asyncio
    async def test_calls_run_cli_with_claude(self):
        bot = _make_bot()
        msg = _make_message("!claude_cli hello")

        fake_result = SimpleNamespace(output="ok", exit_code=0, timed_out=False)
        with patch("src.integrations.cli_runner.run_cli", new=AsyncMock(return_value=fake_result)) as mock_run:
            import src.handlers.command_handlers as _ch
            original_split = _ch._split_text_for_telegram
            try:
                _ch._split_text_for_telegram = lambda t, limit=3900: [t]
                await handle_claude_cli(bot, msg)
            finally:
                _ch._split_text_for_telegram = original_split

            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == "claude"


# ---------------------------------------------------------------------------
# 6. handle_opencode
# ---------------------------------------------------------------------------


class TestHandleOpencode:
    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self):
        bot = _make_bot()
        bot._get_command_args.return_value = ""
        msg = _make_message("!opencode")
        with pytest.raises(UserInputError):
            await handle_opencode(bot, msg)

    @pytest.mark.asyncio
    async def test_calls_run_cli_with_opencode(self):
        bot = _make_bot()
        msg = _make_message("!opencode hello")

        fake_result = SimpleNamespace(output="ok", exit_code=0, timed_out=False)
        with patch("src.integrations.cli_runner.run_cli", new=AsyncMock(return_value=fake_result)) as mock_run:
            import src.handlers.command_handlers as _ch
            original_split = _ch._split_text_for_telegram
            try:
                _ch._split_text_for_telegram = lambda t, limit=3900: [t]
                await handle_opencode(bot, msg)
            finally:
                _ch._split_text_for_telegram = original_split

            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == "opencode"


# ---------------------------------------------------------------------------
# 7. handle_hs
# ---------------------------------------------------------------------------


class TestHandleHs:
    @pytest.mark.asyncio
    async def test_no_args_shows_help(self):
        """Без аргументов показывает справку."""
        bot = MagicMock()
        msg = _make_message("!hs")
        await handle_hs(bot, msg)
        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Hammerspoon" in reply_text

    @pytest.mark.asyncio
    async def test_unavailable_hammerspoon(self):
        """Если Hammerspoon недоступен — сообщение об ошибке."""
        bot = MagicMock()
        msg = _make_message("!hs status")

        fake_hs = MagicMock()
        fake_hs.is_available.return_value = False

        import src.handlers.command_handlers as _ch
        original_hs = _ch.hammerspoon
        try:
            _ch.hammerspoon = fake_hs
            await handle_hs(bot, msg)
        finally:
            _ch.hammerspoon = original_hs

        msg.reply.assert_awaited_once()
        assert "недоступен" in msg.reply.call_args[0][0].lower() or "Hammerspoon" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_subcommand(self):
        """!hs status — выводит версию и статус."""
        bot = MagicMock()
        msg = _make_message("!hs status")

        fake_hs = MagicMock()
        fake_hs.is_available.return_value = True
        fake_hs.status = AsyncMock(return_value={"version": "1.0", "build": "1234", "screens": 2})

        import src.handlers.command_handlers as _ch
        original_hs = _ch.hammerspoon
        try:
            _ch.hammerspoon = fake_hs
            await handle_hs(bot, msg)
        finally:
            _ch.hammerspoon = original_hs

        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "1.0" in reply_text

    @pytest.mark.asyncio
    async def test_focus_no_app_raises(self):
        """!hs focus без имени приложения — UserInputError."""
        bot = MagicMock()
        msg = _make_message("!hs focus")

        fake_hs = MagicMock()
        fake_hs.is_available.return_value = True

        import src.handlers.command_handlers as _ch
        original_hs = _ch.hammerspoon
        try:
            _ch.hammerspoon = fake_hs
            with pytest.raises(UserInputError):
                await handle_hs(bot, msg)
        finally:
            _ch.hammerspoon = original_hs

    @pytest.mark.asyncio
    async def test_move_insufficient_floats_raises(self):
        """!hs move без 4 чисел — UserInputError."""
        bot = MagicMock()
        msg = _make_message("!hs move 0.5 0.5")

        fake_hs = MagicMock()
        fake_hs.is_available.return_value = True

        import src.handlers.command_handlers as _ch
        original_hs = _ch.hammerspoon
        try:
            _ch.hammerspoon = fake_hs
            with pytest.raises(UserInputError):
                await handle_hs(bot, msg)
        finally:
            _ch.hammerspoon = original_hs

    @pytest.mark.asyncio
    async def test_unknown_subcommand_shows_help(self):
        """Неизвестная субкоманда — показывает справку."""
        bot = MagicMock()
        msg = _make_message("!hs unknown_cmd")

        fake_hs = MagicMock()
        fake_hs.is_available.return_value = True

        import src.handlers.command_handlers as _ch
        original_hs = _ch.hammerspoon
        try:
            _ch.hammerspoon = fake_hs
            await handle_hs(bot, msg)
        finally:
            _ch.hammerspoon = original_hs

        msg.reply.assert_awaited_once()
