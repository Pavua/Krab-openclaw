# -*- coding: utf-8 -*-
"""
Тесты команды !listen и alias !mode.

Покрываем:
1) !listen без аргументов — показать текущий режим
2) !listen active/mention-only/muted — установить режим
3) !listen reset — вернуть к дефолту
4) !listen list — показать все правила
5) !listen stats — статистика по режимам
6) !mode alias — должен работать как !listen
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import handle_listen

# ─── фикстуры ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_bot() -> MagicMock:
    """Mock KraabUserbot."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    return bot


@pytest.fixture
def mock_message() -> MagicMock:
    """Mock Message с reply."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = -100123456
    msg.chat.type = "supergroup"
    msg.reply = AsyncMock()
    return msg


# ─── !listen без аргументов ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listen_show_current_mode(mock_bot: MagicMock, mock_message: MagicMock) -> None:
    """!listen без аргументов показывает текущий режим."""
    mock_bot._get_command_args.return_value = ""

    with patch("src.core.chat_filter_config.chat_filter_config") as mock_cfg:
        mock_cfg.get_mode.return_value = "mention-only"

        await handle_listen(mock_bot, mock_message)

        mock_message.reply.assert_called()


# ─── !listen mode ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listen_set_active(mock_bot: MagicMock, mock_message: MagicMock) -> None:
    """!listen active устанавливает режим."""
    mock_bot._get_command_args.return_value = "active"

    with patch("src.core.chat_filter_config.chat_filter_config") as mock_cfg:
        mock_cfg.set_mode = MagicMock()

        await handle_listen(mock_bot, mock_message)

        mock_cfg.set_mode.assert_called_once()
        mock_message.reply.assert_called()


@pytest.mark.asyncio
async def test_listen_set_muted(mock_bot: MagicMock, mock_message: MagicMock) -> None:
    """!listen muted устанавливает режим молчания."""
    mock_bot._get_command_args.return_value = "muted"

    with patch("src.core.chat_filter_config.chat_filter_config") as mock_cfg:
        mock_cfg.set_mode = MagicMock()

        await handle_listen(mock_bot, mock_message)

        mock_cfg.set_mode.assert_called_once()


# ─── !listen reset ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listen_reset(mock_bot: MagicMock, mock_message: MagicMock) -> None:
    """!listen reset возвращает к дефолту."""
    mock_bot._get_command_args.return_value = "reset"

    with patch("src.core.chat_filter_config.chat_filter_config") as mock_cfg:
        mock_cfg.reset = MagicMock()

        await handle_listen(mock_bot, mock_message)

        mock_cfg.reset.assert_called_once()


# ─── !listen list ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listen_list_empty(mock_bot: MagicMock, mock_message: MagicMock) -> None:
    """!listen list когда нет правил."""
    mock_bot._get_command_args.return_value = "list"

    with patch("src.core.chat_filter_config.chat_filter_config") as mock_cfg:
        mock_cfg.list_rules.return_value = []

        await handle_listen(mock_bot, mock_message)

        mock_message.reply.assert_called()


@pytest.mark.asyncio
async def test_listen_stats_shows_counts(mock_bot: MagicMock, mock_message: MagicMock) -> None:
    """!listen stats показывает статистику."""
    mock_bot._get_command_args.return_value = "stats"

    with patch("src.core.chat_filter_config.chat_filter_config") as mock_cfg:
        mock_cfg.stats.return_value = {"total_rules": 3, "by_mode": {"active": 1, "muted": 2}}

        await handle_listen(mock_bot, mock_message)

        mock_message.reply.assert_called()


# ─── регистрация команды в runtime ────────────────────────────────────────────


def test_listen_and_mode_registered_for_acl() -> None:
    """!listen и !mode должны проходить ACL-фильтр userbot, иначе хендлер не вызовется."""
    from src.core.access_control import OWNER_ONLY_COMMANDS, USERBOT_KNOWN_COMMANDS

    assert "listen" in USERBOT_KNOWN_COMMANDS
    assert "mode" in USERBOT_KNOWN_COMMANDS
    assert "listen" in OWNER_ONLY_COMMANDS
    assert "mode" in OWNER_ONLY_COMMANDS


def test_listen_registered_in_command_registry() -> None:
    """!listen должен быть виден в help/owner panel, а !mode — как alias."""
    from src.core.command_registry import registry

    info = registry.get("listen")
    assert info is not None
    assert "mode" in info.aliases
