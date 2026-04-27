# -*- coding: utf-8 -*-
"""
Тесты для fileio_commands (Phase 2 Wave 13).

Проверяем:
  - Импортируемость всех символов из fileio_commands напрямую
  - Re-export из command_handlers (patch surface intact)
  - Базовые сценарии handle_ls / handle_read / handle_write / handle_paste / handle_export
  - handle_export: хелперы _sanitize_filename, _format_sender, _msg_text, _render_export_markdown
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Проверка импортов
# ---------------------------------------------------------------------------


class TestFileioCommandsImports:
    """Все публичные символы доступны из обоих namespace."""

    def test_direct_imports(self):
        from src.handlers.commands.fileio_commands import (
            EXPORT_DEFAULT_LIMIT,
            EXPORT_MAX_LIMIT,
            EXPORT_VAULT_DIR,
            _format_sender,
            _msg_text,
            _render_export_markdown,
            _sanitize_filename,
            handle_export,
            handle_ls,
            handle_paste,
            handle_read,
            handle_write,
        )

        assert callable(handle_ls)
        assert callable(handle_read)
        assert callable(handle_write)
        assert callable(handle_paste)
        assert callable(handle_export)
        assert callable(_sanitize_filename)
        assert callable(_format_sender)
        assert callable(_msg_text)
        assert callable(_render_export_markdown)
        assert EXPORT_DEFAULT_LIMIT == 100
        assert EXPORT_MAX_LIMIT == 1000
        assert "Chats" in str(EXPORT_VAULT_DIR)

    def test_reexport_from_command_handlers(self):
        from src.handlers.command_handlers import (
            EXPORT_DEFAULT_LIMIT,
            EXPORT_MAX_LIMIT,
            EXPORT_VAULT_DIR,
            _format_sender,
            _msg_text,
            _render_export_markdown,
            _sanitize_filename,
            handle_export,
            handle_ls,
            handle_paste,
            handle_read,
            handle_write,
        )

        assert callable(handle_ls)
        assert callable(handle_read)
        assert callable(handle_write)
        assert callable(handle_paste)
        assert callable(handle_export)
        assert callable(_sanitize_filename)
        assert callable(_format_sender)
        assert callable(_msg_text)
        assert callable(_render_export_markdown)
        assert EXPORT_DEFAULT_LIMIT == 100
        assert EXPORT_MAX_LIMIT == 1000


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.client = MagicMock()
    bot.client.send_document = AsyncMock()
    bot.client.get_chat_history = MagicMock(return_value=iter([]))
    return bot


def _make_msg(text: str = "!ls", chat_id: int = 1) -> AsyncMock:
    msg = AsyncMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.title = "Test Chat"
    msg.reply_to_message = None
    return msg


# ---------------------------------------------------------------------------
# handle_ls
# ---------------------------------------------------------------------------


class TestHandleLs:
    @pytest.mark.asyncio
    async def test_ls_calls_mcp_list_directory(self):
        from src.handlers.commands.fileio_commands import handle_ls

        bot = _make_bot("")
        msg = _make_msg()
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("src.handlers.command_handlers.mcp_manager") as mock_mcp,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            mock_mcp.list_directory = AsyncMock(return_value="file1.py\nfile2.py")
            await handle_ls(bot, msg)

        status.edit.assert_called_once()
        call_arg = status.edit.call_args[0][0]
        assert "Files in" in call_arg

    @pytest.mark.asyncio
    async def test_ls_with_path_arg(self):
        from src.handlers.commands.fileio_commands import handle_ls

        bot = _make_bot("/tmp/foo")
        msg = _make_msg()
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("src.handlers.command_handlers.mcp_manager") as mock_mcp,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            mock_mcp.list_directory = AsyncMock(return_value="a.py")
            await handle_ls(bot, msg)

        mock_mcp.list_directory.assert_called_once_with("/tmp/foo")

    @pytest.mark.asyncio
    async def test_ls_error_handled(self):
        import httpx

        from src.handlers.commands.fileio_commands import handle_ls

        bot = _make_bot("")
        msg = _make_msg()
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("src.handlers.command_handlers.mcp_manager") as mock_mcp,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            mock_mcp.list_directory = AsyncMock(side_effect=OSError("fail"))
            await handle_ls(bot, msg)

        status.edit.assert_called_once()
        assert "Error" in status.edit.call_args[0][0]


# ---------------------------------------------------------------------------
# handle_read
# ---------------------------------------------------------------------------


class TestHandleRead:
    @pytest.mark.asyncio
    async def test_read_calls_mcp_read_file(self):
        from src.handlers.commands.fileio_commands import handle_read

        bot = _make_bot("/tmp/test.py")
        msg = _make_msg()
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("src.handlers.command_handlers.mcp_manager") as mock_mcp,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            mock_mcp.read_file = AsyncMock(return_value="print('hello')")
            await handle_read(bot, msg)

        mock_mcp.read_file.assert_called_once_with("/tmp/test.py")
        call_arg = status.edit.call_args[0][0]
        assert "test.py" in call_arg

    @pytest.mark.asyncio
    async def test_read_no_args_raises(self):
        from src.core.exceptions import UserInputError
        from src.handlers.commands.fileio_commands import handle_read

        bot = _make_bot("")
        msg = _make_msg()

        with pytest.raises(UserInputError):
            await handle_read(bot, msg)

    @pytest.mark.asyncio
    async def test_read_long_content_truncated(self):
        from src.handlers.commands.fileio_commands import handle_read

        bot = _make_bot("/tmp/big.txt")
        msg = _make_msg()
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)
        big_content = "x" * 5000

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("src.handlers.command_handlers.mcp_manager") as mock_mcp,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            mock_mcp.read_file = AsyncMock(return_value=big_content)
            await handle_read(bot, msg)

        call_arg = status.edit.call_args[0][0]
        assert "truncated" in call_arg


# ---------------------------------------------------------------------------
# handle_write
# ---------------------------------------------------------------------------


class TestHandleWrite:
    @pytest.mark.asyncio
    async def test_write_calls_mcp_write_file(self):
        from src.handlers.commands.fileio_commands import handle_write

        bot = _make_bot("test.txt\nhello world")
        msg = _make_msg()

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("src.handlers.command_handlers.mcp_manager") as mock_mcp,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            mock_mcp.write_file = AsyncMock(return_value="OK")
            await handle_write(bot, msg)

        mock_mcp.write_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_no_args_raises(self):
        from src.core.exceptions import UserInputError
        from src.handlers.commands.fileio_commands import handle_write

        bot = _make_bot("")
        msg = _make_msg()

        with pytest.raises(UserInputError):
            await handle_write(bot, msg)

    @pytest.mark.asyncio
    async def test_write_no_content_raises(self):
        from src.core.exceptions import UserInputError
        from src.handlers.commands.fileio_commands import handle_write

        bot = _make_bot("filename_only")
        msg = _make_msg()

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
        ):
            mock_cfg.BASE_DIR = "/tmp"
            with pytest.raises(UserInputError):
                await handle_write(bot, msg)


# ---------------------------------------------------------------------------
# handle_paste
# ---------------------------------------------------------------------------


class TestHandlePaste:
    @pytest.mark.asyncio
    async def test_paste_from_args_sends_document(self, tmp_path):
        from src.handlers.commands.fileio_commands import handle_paste

        bot = _make_bot("Hello world")
        msg = _make_msg()

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            mock_cfg.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        bot.client.send_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_paste_from_reply_sends_document(self, tmp_path):
        from src.handlers.commands.fileio_commands import handle_paste

        bot = _make_bot("")
        msg = _make_msg()
        reply = MagicMock()
        reply.text = "Reply text content"
        msg.reply_to_message = reply

        with (
            patch("src.handlers.command_handlers.config") as mock_cfg,
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("pathlib.Path.unlink"),
        ):
            mock_cfg.BASE_DIR = str(tmp_path)
            await handle_paste(bot, msg)

        bot.client.send_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_paste_no_args_no_reply_raises(self):
        from src.core.exceptions import UserInputError
        from src.handlers.commands.fileio_commands import handle_paste

        bot = _make_bot("")
        msg = _make_msg()

        with pytest.raises(UserInputError):
            await handle_paste(bot, msg)


# ---------------------------------------------------------------------------
# Хелперы !export
# ---------------------------------------------------------------------------


class TestExportHelpers:
    def test_sanitize_filename(self):
        from src.handlers.commands.fileio_commands import _sanitize_filename

        assert _sanitize_filename("Hello World") == "Hello World"
        assert "/" not in _sanitize_filename("file/name")
        assert ":" not in _sanitize_filename("file:name")

    def test_format_sender_user(self):
        from src.handlers.commands.fileio_commands import _format_sender

        msg = SimpleNamespace(
            from_user=SimpleNamespace(
                first_name="Pavel",
                last_name="R",
                username="pavelr",
                id=123,
            ),
            sender_chat=None,
        )
        result = _format_sender(msg)
        assert "Pavel" in result

    def test_format_sender_chat(self):
        from src.handlers.commands.fileio_commands import _format_sender

        msg = SimpleNamespace(
            from_user=None,
            sender_chat=SimpleNamespace(title="My Channel", id=456),
        )
        assert _format_sender(msg) == "My Channel"

    def test_format_sender_unknown(self):
        from src.handlers.commands.fileio_commands import _format_sender

        msg = SimpleNamespace(from_user=None, sender_chat=None)
        assert _format_sender(msg) == "Unknown"

    def test_msg_text_from_text(self):
        from src.handlers.commands.fileio_commands import _msg_text

        msg = SimpleNamespace(text="Hello", caption=None)
        assert _msg_text(msg) == "Hello"

    def test_msg_text_from_caption(self):
        from src.handlers.commands.fileio_commands import _msg_text

        msg = SimpleNamespace(text=None, caption="Caption here")
        assert _msg_text(msg) == "Caption here"

    def test_msg_text_empty(self):
        from src.handlers.commands.fileio_commands import _msg_text

        msg = SimpleNamespace(text=None, caption=None)
        assert _msg_text(msg) == ""

    def test_render_export_markdown_structure(self):
        from src.handlers.commands.fileio_commands import _render_export_markdown

        dt = datetime.datetime(2026, 4, 27, 12, 0, 0)
        msg_date = datetime.datetime(2026, 4, 27, 10, 30, 0)
        fake_msg = SimpleNamespace(
            date=msg_date,
            text="Test message",
            caption=None,
            from_user=SimpleNamespace(
                first_name="Pavel", last_name=None, username="p", id=1
            ),
            sender_chat=None,
            photo=None,
            video=None,
            audio=None,
            voice=None,
            document=None,
            sticker=None,
        )
        result = _render_export_markdown("Test Chat", 123, [fake_msg], dt)
        assert "chat_title: Test Chat" in result
        assert "Test message" in result
        assert "2026-04-27" in result


# ---------------------------------------------------------------------------
# handle_export (интеграционные)
# ---------------------------------------------------------------------------


class TestHandleExport:
    @pytest.mark.asyncio
    async def test_export_no_messages(self, tmp_path):
        from src.handlers.commands.fileio_commands import handle_export

        bot = _make_bot()
        msg = _make_msg("!export")
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)

        async def _empty_history(chat_id, limit):
            return
            yield  # noqa: unreachable

        bot.client.get_chat_history = _empty_history

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, msg)

        status.edit.assert_called_once()
        assert "Нет" in status.edit.call_args[0][0]

    @pytest.mark.asyncio
    async def test_export_invalid_arg_replies_error(self):
        from src.handlers.commands.fileio_commands import handle_export

        bot = _make_bot()
        msg = _make_msg("!export badarg")

        await handle_export(bot, msg)

        msg.reply.assert_called_once()
        assert "Неверный" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_export_sends_document(self, tmp_path):
        from src.handlers.commands.fileio_commands import handle_export

        bot = _make_bot()
        msg = _make_msg("!export 5")
        status = AsyncMock()
        msg.reply = AsyncMock(return_value=status)

        msg_date = datetime.datetime(2026, 4, 27, 10, 0, 0)
        fake_msg = SimpleNamespace(
            date=msg_date,
            text="Hello",
            caption=None,
            from_user=SimpleNamespace(
                first_name="P", last_name=None, username="p", id=1
            ),
            sender_chat=None,
            photo=None,
            video=None,
            audio=None,
            voice=None,
            document=None,
            sticker=None,
        )

        async def _gen_history(chat_id, limit):
            yield fake_msg

        bot.client.get_chat_history = _gen_history

        with patch("src.handlers.command_handlers.EXPORT_VAULT_DIR", tmp_path):
            await handle_export(bot, msg)

        bot.client.send_document.assert_called_once()
