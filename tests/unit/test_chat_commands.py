# -*- coding: utf-8 -*-
"""
Тесты для extracted chat_commands domain — Phase 2 Wave 2 (Session 27).

Проверяют:
1. Хендлеры доступны через src.handlers.commands.chat_commands.
2. Re-exports через src.handlers.command_handlers сохранились.
3. Lightweight smoke tests — сами хендлеры с mock-ом bot/message.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.commands.chat_commands import (
    _WHOIS_FIELD_PATTERNS,
    _parse_whois_output,
    handle_chatinfo,
    handle_history,
    handle_monitor,
    handle_who,
    handle_whois,
)

# ---------------------------------------------------------------------------
# Re-export verification
# ---------------------------------------------------------------------------


class TestReExports:
    """API stability: command_handlers re-exports chat_commands handlers/helpers."""

    def test_command_handlers_reexports_handlers(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch.handle_who is handle_who
        assert ch.handle_chatinfo is handle_chatinfo
        assert ch.handle_history is handle_history
        assert ch.handle_monitor is handle_monitor
        assert ch.handle_whois is handle_whois

    def test_command_handlers_reexports_helpers(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch._parse_whois_output is _parse_whois_output
        assert ch._WHOIS_FIELD_PATTERNS is _WHOIS_FIELD_PATTERNS


# ---------------------------------------------------------------------------
# _parse_whois_output — pure helper
# ---------------------------------------------------------------------------


class TestParseWhoisOutput:
    def test_parses_registrar_and_dates(self) -> None:
        raw = (
            "Domain Name: EXAMPLE.COM\n"
            "Registrar: MarkMonitor Inc.\n"
            "Creation Date: 1995-08-14T04:00:00Z\n"
            "Registry Expiry Date: 2027-08-13T04:00:00Z\n"
            "Name Server: A.IANA-SERVERS.NET\n"
            "Name Server: B.IANA-SERVERS.NET\n"
        )
        fields = _parse_whois_output(raw)
        assert fields["registrar"] == "MarkMonitor Inc."
        assert fields["created"] == "1995-08-14"
        assert fields["expires"] == "2027-08-13"
        assert "a.iana-servers.net" in fields["nameservers"]
        assert "b.iana-servers.net" in fields["nameservers"]

    def test_handles_lowercase_keys(self) -> None:
        raw = "registrar: SomeCorp\ncreated: 2020-01-01\nexpires: 2030-01-01\nnserver: ns1.test.\n"
        fields = _parse_whois_output(raw)
        assert fields["registrar"] == "SomeCorp"
        assert fields["created"] == "2020-01-01"
        assert fields["expires"] == "2030-01-01"
        assert fields["nameservers"] == ["ns1.test"]

    def test_empty_input_returns_empty_nameservers(self) -> None:
        fields = _parse_whois_output("")
        assert fields["nameservers"] == []

    def test_dedup_nameservers(self) -> None:
        raw = "Name Server: NS1.EXAMPLE.COM\nName Server: ns1.example.com\n"
        fields = _parse_whois_output(raw)
        assert fields["nameservers"].count("ns1.example.com") == 1


# ---------------------------------------------------------------------------
# Smoke tests for async handlers
# ---------------------------------------------------------------------------


def _make_message(*, args: str = "", chat_id: int = -100, command: list[str] | None = None):
    """Создаёт mock Message с минимально нужными атрибутами."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    msg.text = f"!cmd {args}".strip()
    msg.command = command or ["cmd"]
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply_to_message = None
    return msg


def _make_bot(args: str = ""):
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args)
    bot.client = MagicMock()
    return bot


@pytest.mark.asyncio
async def test_handle_whois_empty_raises():
    bot = _make_bot(args="")
    msg = _make_message()
    with pytest.raises(UserInputError):
        await handle_whois(bot, msg)


@pytest.mark.asyncio
async def test_handle_chatinfo_invalid_chat_raises():
    bot = _make_bot(args="")
    bot.client.get_chat = AsyncMock(side_effect=RuntimeError("not found"))
    msg = _make_message(chat_id=-1001)
    with pytest.raises(UserInputError):
        await handle_chatinfo(bot, msg)


@pytest.mark.asyncio
async def test_handle_chatinfo_basic_chat():
    bot = _make_bot(args="")
    chat_obj = SimpleNamespace(
        id=-1001,
        type="ChatType.SUPERGROUP",
        username=None,
        title="Test Chat",
        first_name=None,
        description="",
        date=None,
        linked_chat=None,
        members_count=42,
        permissions=None,
    )
    bot.client.get_chat = AsyncMock(return_value=chat_obj)

    # async-iterator для get_chat_members
    async def _empty_iter(*a, **kw):
        return
        yield  # pragma: no cover

    bot.client.get_chat_members = lambda *a, **kw: _empty_iter()
    msg = _make_message(chat_id=-1001)
    await handle_chatinfo(bot, msg)
    assert msg.reply.call_count == 1
    body = msg.reply.call_args.args[0]
    assert "Test Chat" in body
    assert "-1001" in body


@pytest.mark.asyncio
async def test_handle_history_empty_chat():
    bot = _make_bot()

    async def _empty_history(*a, **kw):
        return
        yield  # pragma: no cover

    bot.client.get_chat_history = lambda *a, **kw: _empty_history()
    msg = _make_message(chat_id=-1001)
    await handle_history(bot, msg)
    msg.reply.assert_called_once()
    assert "нет сообщений" in msg.reply.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_monitor_help_when_no_args():
    bot = _make_bot()
    msg = _make_message(command=["monitor"])
    await handle_monitor(bot, msg)
    msg.reply.assert_called_once()
    assert "Chat Monitor" in msg.reply.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_monitor_remove_unknown_subcommand_raises():
    bot = _make_bot()
    msg = _make_message(command=["monitor", "weirdsub"])
    with pytest.raises(UserInputError):
        await handle_monitor(bot, msg)


@pytest.mark.asyncio
async def test_handle_monitor_remove_missing_id_raises():
    bot = _make_bot()
    msg = _make_message(command=["monitor", "remove"])
    with pytest.raises(UserInputError):
        await handle_monitor(bot, msg)


@pytest.mark.asyncio
async def test_handle_monitor_list_empty(monkeypatch):
    bot = _make_bot()
    msg = _make_message(command=["monitor", "list"])
    # Patch chat_monitor_service.list_monitors → []
    from src.core import chat_monitor as cm

    monkeypatch.setattr(cm.chat_monitor_service, "list_monitors", lambda: [])
    await handle_monitor(bot, msg)
    msg.reply.assert_called_once()
    assert "нет" in msg.reply.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_handle_who_user_not_resolvable():
    bot = _make_bot(args="@nonexistent_user_xyz")
    bot.client.get_users = AsyncMock(side_effect=RuntimeError("nope"))
    msg = _make_message()
    await handle_who(bot, msg)
    msg.reply.assert_called_once()
    assert "Ошибка" in msg.reply.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_who_no_args_shows_chat():
    bot = _make_bot(args="")
    chat_obj = SimpleNamespace(
        id=-555,
        type="ChatType.GROUP",
        username=None,
        title="Group X",
        first_name=None,
        description=None,
        members_count=10,
    )
    bot.client.get_chat = AsyncMock(return_value=chat_obj)
    msg = _make_message(chat_id=-555)
    await handle_who(bot, msg)
    msg.reply.assert_called_once()
    body = msg.reply.call_args.args[0]
    assert "Group X" in body or "Chat Info" in body
