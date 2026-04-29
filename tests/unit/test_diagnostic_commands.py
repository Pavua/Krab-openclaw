# -*- coding: utf-8 -*-
"""Phase 2 Wave 21 (Session 28): regression-тесты для diagnostic_commands.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_diagnostic_commands_module_imports() -> None:
    """Все 13 handlers должны импортироваться из diagnostic_commands."""
    from src.handlers.commands import diagnostic_commands as dc

    expected = [
        "handle_help", "handle_screenshot", "handle_bench",
        "handle_eval", "handle_run", "handle_link", "handle_time",
        "handle_typing", "handle_say", "handle_listen", "handle_filter",
        "handle_chado", "handle_e2e_smoke", "safe_eval",
    ]
    for name in expected:
        assert hasattr(dc, name), f"missing {name}"


def test_command_handlers_reexports_diagnostic() -> None:
    """Старый namespace command_handlers экспортирует все handlers + helpers."""
    from src.handlers import command_handlers as ch

    expected_attrs = [
        "handle_help", "handle_screenshot", "handle_bench", "handle_eval",
        "safe_eval", "handle_run", "handle_link", "handle_time",
        "handle_typing", "handle_say", "handle_listen", "handle_filter",
        "handle_chado", "handle_e2e_smoke",
        "_fetch_link_meta", "_expand_url", "_format_link_preview", "_URL_RE",
        "_TIME_CITY_MAP", "_TIME_DEFAULT_CITIES",
        "_TYPING_ACTION_MAP", "_TYPING_LABEL_MAP",
        "_TYPING_DEFAULT_SECONDS", "_TYPING_MAX_SECONDS",
        "_EVAL_ALLOWED_NODES", "_EVAL_FORBIDDEN_NAMES", "_EVAL_NAMESPACE",
        "_eval_check_node", "_is_short_url",
        "_handle_listen_list", "_handle_listen_stats",
        "_handle_chado_status", "_handle_chado_ping", "_handle_chado_digest",
    ]
    for name in expected_attrs:
        assert hasattr(ch, name), f"missing re-export: {name}"


def test_safe_eval_basic_arithmetic() -> None:
    from src.handlers.commands.diagnostic_commands import safe_eval

    assert safe_eval("2 + 2") == 4
    assert safe_eval("len([1,2,3])") == 3
    assert safe_eval("sorted([3,1,2])") == [1, 2, 3]


def test_safe_eval_rejects_forbidden_name() -> None:
    """import — запрещено через _EVAL_FORBIDDEN_NAMES."""
    from src.core.exceptions import UserInputError
    from src.handlers.commands.diagnostic_commands import safe_eval

    with pytest.raises(UserInputError):
        safe_eval("import os")


def test_safe_eval_rejects_dunder_attr() -> None:
    from src.core.exceptions import UserInputError
    from src.handlers.commands.diagnostic_commands import safe_eval

    with pytest.raises(UserInputError):
        safe_eval("().__class__")


@pytest.mark.asyncio
async def test_handle_link_uses_command_handlers_namespace(monkeypatch) -> None:
    """!link должен лукапить _fetch_link_meta через command_handlers (dual-namespace)."""
    from src.handlers import command_handlers as ch
    from src.handlers.commands.diagnostic_commands import handle_link

    captured: dict[str, str] = {}

    async def _fake_fetch(url: str, *, timeout: float = 10.0) -> dict:
        captured["url"] = url
        return {
            "title": "Mock", "description": "Mock description",
            "image": "", "final_url": url,
        }

    monkeypatch.setattr(ch, "_fetch_link_meta", _fake_fetch)

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="preview https://example.com")
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.reply_to_message = None

    await handle_link(bot, msg)

    assert captured["url"] == "https://example.com"
    assert msg.reply.await_count >= 1


@pytest.mark.asyncio
async def test_handle_listen_no_args_returns_current_mode() -> None:
    from src.handlers.commands.diagnostic_commands import handle_listen

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    msg = MagicMock()
    msg.chat.id = 12345
    msg.chat.type = "private"
    msg.reply = AsyncMock()

    await handle_listen(bot, msg)
    assert msg.reply.await_count == 1
    args, _ = msg.reply.await_args
    assert "Текущий режим" in args[0]


@pytest.mark.asyncio
async def test_handle_filter_no_args_returns_current_mode() -> None:
    from src.handlers.commands.diagnostic_commands import handle_filter

    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    msg = MagicMock()
    msg.chat.id = 99
    msg.chat.type = "private"
    msg.reply = AsyncMock()

    await handle_filter(bot, msg)
    assert msg.reply.await_count == 1
    args, _ = msg.reply.await_args
    text = args[0]
    assert ("режим" in text.lower()) or ("mode" in text.lower())
