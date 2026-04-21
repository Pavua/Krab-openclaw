# -*- coding: utf-8 -*-
"""
Тесты !chado — cross-AI sync статус с Chado (Chado §9).

Покрытие:
1. !chado status — возвращает ожидаемую структуру ответа
2. !chado ping — вызывает broadcast через swarm_channels (mock)
3. !chado digest — вызывает dry_run_preview из cron_chado_sync.py (mock)
4. !chado <неизвестное> — возвращает ошибку
5. CommandRegistry: запись chado существует со stage=beta
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> MagicMock:
    """Создаёт мок userbot."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    return bot


def _make_message() -> MagicMock:
    """Создаёт мок Telegram-сообщения."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = 12345
    return msg


# ---------------------------------------------------------------------------
# 1. !chado status — структура ответа
# ---------------------------------------------------------------------------


class TestChadoStatus:
    """!chado / !chado status — возвращает блок с ключевыми полями."""

    @pytest.mark.asyncio
    async def test_status_contains_header(self) -> None:
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("")
        msg = _make_message()

        # Патчим archive.db (нет файла) и swarm_channels
        with (
            patch("src.core.swarm_channels.swarm_channels") as mock_sc,
            patch("src.core.scheduler.krab_scheduler") as mock_sched,
        ):
            mock_sc._forum_chat_id = None
            mock_sc._team_topics = {}
            mock_sc._resolve_destination = MagicMock(return_value=(None, None))
            mock_sched.list_jobs = MagicMock(return_value=[])

            await handle_chado(bot, msg)

        msg.reply.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "Chado Cross-AI Sync" in reply_text
        assert "Последний sync" in reply_text
        assert "archive.db" in reply_text

    @pytest.mark.asyncio
    async def test_status_with_archive_db(self, tmp_path: Path) -> None:
        """archive.db отсутствует — handler не падает, reply вызывается."""
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("status")
        msg = _make_message()

        # Патчим Path.home чтобы указать на tmp_path (без archive.db)
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("src.core.swarm_channels.swarm_channels") as mock_sc,
            patch("src.core.scheduler.krab_scheduler") as mock_sched,
        ):
            mock_sc._forum_chat_id = None
            mock_sc._team_topics = {}
            mock_sched.list_jobs = MagicMock(return_value=[])
            await handle_chado(bot, msg)

        msg.reply.assert_called_once()
        # Без archive.db счётчик — 0
        text: str = msg.reply.call_args[0][0]
        assert "0" in text or "archive.db" in text

    @pytest.mark.asyncio
    async def test_status_shows_crossteam_link_when_configured(self) -> None:
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("status")
        msg = _make_message()

        with (
            patch("src.core.swarm_channels.swarm_channels") as mock_sc,
            patch("src.core.scheduler.krab_scheduler") as mock_sched,
        ):
            mock_sc._forum_chat_id = -1003703978531
            mock_sc._team_topics = {"crossteam": 42}
            mock_sc._resolve_destination = MagicMock(return_value=(-1003703978531, 42))
            mock_sched.list_jobs = MagicMock(return_value=[])

            await handle_chado(bot, msg)

        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        # Должна быть ссылка или упоминание crossteam
        assert "crossteam" in text.lower() or "3703978531" in text or "42" in text


# ---------------------------------------------------------------------------
# 2. !chado ping — вызывает broadcast в crossteam
# ---------------------------------------------------------------------------


class TestChadoPing:
    """!chado ping — отправляет ping через swarm_channels."""

    @pytest.mark.asyncio
    async def test_ping_calls_send_message_when_topic_configured(self) -> None:
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("ping")
        msg = _make_message()

        with patch("src.core.swarm_channels.swarm_channels") as mock_sc:
            mock_sc._resolve_destination = MagicMock(return_value=(-1003703978531, 42))
            mock_sc._send_message = AsyncMock()

            await handle_chado(bot, msg)

        # _send_message должен быть вызван
        mock_sc._send_message.assert_called_once()
        call_args = mock_sc._send_message.call_args
        assert call_args[0][0] == -1003703978531  # chat_id
        assert "Chado" in call_args[0][1] or "chado" in call_args[0][1].lower()
        # Успех-reply
        msg.reply.assert_called_once()
        assert "отправлен" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_ping_warns_when_no_crossteam_topic(self) -> None:
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("ping")
        msg = _make_message()

        with patch("src.core.swarm_channels.swarm_channels") as mock_sc:
            mock_sc._resolve_destination = MagicMock(return_value=(None, None))
            mock_sc._send_message = AsyncMock()

            await handle_chado(bot, msg)

        mock_sc._send_message.assert_not_called()
        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "не настроен" in text or "setup" in text.lower()


# ---------------------------------------------------------------------------
# 3. !chado digest — dry_run_preview из cron_chado_sync.py
# ---------------------------------------------------------------------------


class TestChadoDigest:
    """!chado digest — вызывает dry_run_preview если скрипт существует."""

    @pytest.mark.asyncio
    async def test_digest_warns_when_script_missing(self, tmp_path: Path) -> None:
        """Если cron_chado_sync.py не существует — предупреждение."""
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("digest")
        msg = _make_message()

        # tmp_path не содержит scripts/cron_chado_sync.py → handler покажет предупреждение
        # Патчим Path(__file__) через pathlib.Path.exists → всегда False для нашего случая
        with patch("pathlib.Path.exists", return_value=False):
            await handle_chado(bot, msg)

        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "cron_chado_sync" in text or "не найден" in text

    @pytest.mark.asyncio
    async def test_digest_calls_dry_run_preview(self, tmp_path: Path) -> None:
        """Если скрипт есть с dry_run_preview() — вызывает и отображает результат."""
        import importlib.util

        from src.handlers.command_handlers import handle_chado

        # Создаём временный cron_chado_sync.py с dry_run_preview
        script = tmp_path / "cron_chado_sync.py"
        script.write_text("def dry_run_preview():\n    return 'chado_sync_preview_ok'\n")

        bot = _make_bot("digest")
        msg = _make_message()

        real_spec = importlib.util.spec_from_file_location("cron_chado_sync", str(script))

        # Патчим exists → True, и spec_from_file_location → реальный spec из tmp_path
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("importlib.util.spec_from_file_location", return_value=real_spec),
        ):
            await handle_chado(bot, msg)

        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        # dry_run_preview вернул 'chado_sync_preview_ok' или digest-блок
        assert "Digest" in text or "dry" in text.lower() or "preview" in text.lower()


# ---------------------------------------------------------------------------
# 4. !chado <unknown> — возвращает ошибку
# ---------------------------------------------------------------------------


class TestChadoUnknown:
    """Неизвестная субкоманда → сообщение об ошибке."""

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self) -> None:
        from src.handlers.command_handlers import handle_chado

        bot = _make_bot("foobar")
        msg = _make_message()

        await handle_chado(bot, msg)

        msg.reply.assert_called_once()
        text: str = msg.reply.call_args[0][0]
        assert "❌" in text
        assert "status" in text


# ---------------------------------------------------------------------------
# 5. CommandRegistry: chado со stage=beta
# ---------------------------------------------------------------------------


class TestChadoRegistryEntry:
    """CommandRegistry содержит запись chado с stage=beta."""

    def test_chado_in_registry(self) -> None:
        from src.core.command_registry import registry

        cmd = registry.get("chado")
        assert cmd is not None, "Команда 'chado' не найдена в CommandRegistry"
        assert cmd.name == "chado"
        assert cmd.stage == "beta"
        assert cmd.owner_only is True
        assert cmd.category == "swarm"

    def test_chado_to_dict_includes_stage(self) -> None:
        from src.core.command_registry import registry

        cmd = registry.get("chado")
        assert cmd is not None
        d = cmd.to_dict()
        assert d["stage"] == "beta"
        assert "chado" in d["name"]
