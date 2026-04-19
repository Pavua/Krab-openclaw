# -*- coding: utf-8 -*-
"""
Тесты для команды !context — показ / сброс / сохранение контекста чата.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import (
    _estimate_session_tokens,
    _format_time_ago,
    handle_context,
)

# ──────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.me = SimpleNamespace(id=999)
    return bot


def _make_message(text: str = "!context", chat_id: int = 12345) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=100, username="owner")
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    return msg


def _make_openclaw(sessions: dict | None = None) -> MagicMock:
    oc = MagicMock()
    oc._sessions = sessions or {}
    oc.clear_session = MagicMock()
    oc.get_last_runtime_route = MagicMock(return_value={"model": "gemini-3-pro-preview"})
    return oc


# ──────────────────────────────────────────────
# _estimate_session_tokens
# ──────────────────────────────────────────────


class TestEstimateSessionTokens:
    def test_empty_list(self) -> None:
        assert _estimate_session_tokens([]) == 0

    def test_simple_text(self) -> None:
        msgs = [{"role": "user", "content": "Hello world"}]
        result = _estimate_session_tokens(msgs)
        assert result > 0

    def test_multipart_content(self) -> None:
        msgs = [
            {
                "role": "user",
                "content": [
                    {"text": "Привет"},
                    {"text": " мир"},
                ],
            }
        ]
        result = _estimate_session_tokens(msgs)
        assert result > 0

    def test_multiple_messages(self) -> None:
        msgs = [
            {"role": "user", "content": "a" * 400},
            {"role": "assistant", "content": "b" * 400},
        ]
        result = _estimate_session_tokens(msgs)
        # 800 символов / 4 = 200 токенов
        assert result == 200

    def test_non_dict_part_in_list(self) -> None:
        msgs = [{"role": "user", "content": ["hello", "world"]}]
        result = _estimate_session_tokens(msgs)
        assert result > 0

    def test_none_content(self) -> None:
        msgs = [{"role": "user", "content": None}]
        result = _estimate_session_tokens(msgs)
        assert result == 0


# ──────────────────────────────────────────────
# _format_time_ago
# ──────────────────────────────────────────────


class TestFormatTimeAgo:
    def test_seconds(self) -> None:
        assert "сек назад" in _format_time_ago(30)

    def test_minutes(self) -> None:
        assert "мин назад" in _format_time_ago(300)

    def test_hours(self) -> None:
        assert "ч назад" in _format_time_ago(7200)

    def test_one_minute_boundary(self) -> None:
        # 60 секунд -> минуты
        assert "мин назад" in _format_time_ago(60)

    def test_zero_seconds(self) -> None:
        assert "сек назад" in _format_time_ago(0)


# ──────────────────────────────────────────────
# handle_context — показ
# ──────────────────────────────────────────────


class TestHandleContextShow:
    @pytest.mark.asyncio
    async def test_show_empty_session(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw(sessions={})

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch(
                "src.handlers.command_handlers.get_runtime_primary_model",
                return_value="gemini-3-pro-preview",
            ),
        ):
            await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Контекст чата" in reply_text
        assert "Сообщений:" in reply_text
        assert "`0`" in reply_text

    @pytest.mark.asyncio
    async def test_show_with_messages(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        sessions = {
            "12345": [
                {"role": "user", "content": "Привет"},
                {"role": "assistant", "content": "Привет! Чем помочь?"},
            ]
        }
        oc = _make_openclaw(sessions=sessions)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch(
                "src.handlers.command_handlers.get_runtime_primary_model",
                return_value="gemini-3-pro-preview",
            ),
        ):
            await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Сообщений:" in reply_text
        assert "`2`" in reply_text

    @pytest.mark.asyncio
    async def test_show_model_from_route(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw()
        oc.get_last_runtime_route.return_value = {"model": "gemini-3-flash"}

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch(
                "src.handlers.command_handlers.get_runtime_primary_model",
                return_value="fallback-model",
            ),
        ):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "gemini-3-flash" in reply_text

    @pytest.mark.asyncio
    async def test_show_model_fallback_to_runtime(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw()
        oc.get_last_runtime_route.return_value = {"model": ""}

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch(
                "src.handlers.command_handlers.get_runtime_primary_model",
                return_value="runtime-model",
            ),
        ):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "runtime-model" in reply_text

    @pytest.mark.asyncio
    async def test_show_system_messages_excluded_from_count(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        sessions = {
            "12345": [
                {"role": "system", "content": "Ты — Краб"},
                {"role": "user", "content": "Привет"},
                {"role": "assistant", "content": "Привет!"},
            ]
        }
        oc = _make_openclaw(sessions=sessions)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        ):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        # Системное сообщение не считается
        assert "`2`" in reply_text

    @pytest.mark.asyncio
    async def test_show_includes_session_id(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context", chat_id=99999)
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        ):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "telegram_99999" in reply_text

    @pytest.mark.asyncio
    async def test_show_includes_commands_hint(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw()

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        ):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "!context clear" in reply_text
        assert "!context save" in reply_text

    @pytest.mark.asyncio
    async def test_show_with_session_last_updated(self) -> None:
        """Если openclaw_client имеет _session_last_updated — показывает время."""
        import time

        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw()
        oc._session_last_updated = {"12345": time.time() - 120}  # 2 мин назад

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        ):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "мин назад" in reply_text


# ──────────────────────────────────────────────
# handle_context clear
# ──────────────────────────────────────────────


class TestHandleContextClear:
    @pytest.mark.asyncio
    async def test_clear_calls_clear_session(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context clear")
        oc = _make_openclaw()

        with patch("src.handlers.command_handlers.openclaw_client", oc):
            await handle_context(bot, msg)

        oc.clear_session.assert_called_once_with("12345")

    @pytest.mark.asyncio
    async def test_clear_replies_confirmation(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context clear")
        oc = _make_openclaw()

        with patch("src.handlers.command_handlers.openclaw_client", oc):
            await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "очищен" in reply_text.lower() or "Контекст очищен" in reply_text

    @pytest.mark.asyncio
    async def test_clear_alias_очисти(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context очисти")
        oc = _make_openclaw()

        with patch("src.handlers.command_handlers.openclaw_client", oc):
            await handle_context(bot, msg)

        oc.clear_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_alias_сброс(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context сброс")
        oc = _make_openclaw()

        with patch("src.handlers.command_handlers.openclaw_client", oc):
            await handle_context(bot, msg)

        oc.clear_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_does_not_show_context_info(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context clear")
        oc = _make_openclaw()

        with patch("src.handlers.command_handlers.openclaw_client", oc):
            await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        # Не должно быть блока показа контекста
        assert "Сообщений:" not in reply_text


# ──────────────────────────────────────────────
# handle_context save
# ──────────────────────────────────────────────


class TestHandleContextSave:
    @pytest.mark.asyncio
    async def test_save_empty_session_warns(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context save")
        oc = _make_openclaw(sessions={})

        with patch("src.handlers.command_handlers.openclaw_client", oc):
            await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "пуст" in reply_text

    @pytest.mark.asyncio
    async def test_save_creates_json_file(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context save")
        sessions = {
            "12345": [
                {"role": "user", "content": "Привет"},
                {"role": "assistant", "content": "Привет!"},
            ]
        }
        oc = _make_openclaw(sessions=sessions)

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Checkpoint" in reply_text

    @pytest.mark.asyncio
    async def test_save_file_contains_correct_data(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context save")
        sessions = {
            "12345": [
                {"role": "user", "content": "test message"},
            ]
        }
        oc = _make_openclaw(sessions=sessions)

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

            # Ищем созданный файл
            files = list(checkpoint_dir.glob("12345_*.json"))
            assert len(files) == 1
            data = json.loads(files[0].read_text())
            assert data["chat_id"] == "12345"
            assert data["message_count"] == 1
            assert len(data["messages"]) == 1

    @pytest.mark.asyncio
    async def test_save_alias_сохрани(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context сохрани")
        sessions = {"12345": [{"role": "user", "content": "hi"}]}
        oc = _make_openclaw(sessions=sessions)

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Checkpoint" in reply_text

    @pytest.mark.asyncio
    async def test_save_alias_checkpoint(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context checkpoint")
        sessions = {"12345": [{"role": "user", "content": "hi"}]}
        oc = _make_openclaw(sessions=sessions)

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_error_replies_error_message(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context save")
        sessions = {"12345": [{"role": "user", "content": "hi"}]}
        oc = _make_openclaw(sessions=sessions)

        # Симулируем ошибку записи файла
        bad_dir = MagicMock()
        bad_dir.mkdir = MagicMock(side_effect=PermissionError("no access"))
        bad_dir.exists = MagicMock(return_value=False)

        with (
            patch("src.handlers.command_handlers.openclaw_client", oc),
            patch("src.handlers.command_handlers._CHECKPOINTS_DIR", bad_dir),
        ):
            await handle_context(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "❌" in reply_text

    @pytest.mark.asyncio
    async def test_save_shows_message_count(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context save")
        sessions = {
            "12345": [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ]
        }
        oc = _make_openclaw(sessions=sessions)

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "3" in reply_text


# ──────────────────────────────────────────────
# handle_context — checkpoint count в show
# ──────────────────────────────────────────────


class TestHandleContextCheckpointCount:
    @pytest.mark.asyncio
    async def test_show_displays_checkpoint_count(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw()

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            checkpoint_dir.mkdir(parents=True)
            # Создаём 2 фейковых checkpoint файла для chat_id=12345
            (checkpoint_dir / "12345_20260101T000000.json").write_text("{}")
            (checkpoint_dir / "12345_20260102T000000.json").write_text("{}")
            # Файл другого чата — не должен считаться
            (checkpoint_dir / "99999_20260101T000000.json").write_text("{}")

            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Checkpoints: `2`" in reply_text

    @pytest.mark.asyncio
    async def test_show_no_checkpoint_line_if_zero(self) -> None:
        bot = _make_bot()
        msg = _make_message("!context")
        oc = _make_openclaw()

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = pathlib.Path(tmpdir) / "checkpoints"
            # Директория существует, но файлов нет

            with (
                patch("src.handlers.command_handlers.openclaw_client", oc),
                patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
                patch("src.handlers.command_handlers._CHECKPOINTS_DIR", checkpoint_dir),
            ):
                await handle_context(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Checkpoints" not in reply_text
