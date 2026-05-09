# -*- coding: utf-8 -*-
"""Wave 49-D: тесты !replay (manual on-demand message replay).

Background:
- Wave 46-A + 48-A автоматически catchup на startup.
- Между restart'ами Krab может потерять messages (split-brain windows
  Wave 39-D detection).
- Wave 49-D: !replay даёт owner manual control — on-demand replay
  любого chat'а с custom lookback.

Note: Имя ``!catchup`` уже занято в ``ai_commands.py`` (alias для
``!summary 100``), поэтому новая команда называется ``!replay``.

Coverage:
- Args parsing (no-args/here/numeric/explicit chat_id/with lookback).
- Default chats path → invokes _resolve_catchup_target_chats.
- 'here' path → uses message.chat.id.
- Explicit chat_id parsing (negative number).
- Lookback override propagated to _catchup_chat_history.
- Owner-only — non-owner gets 🔒 message.
- Summary text format includes caught_up/skipped_self/last_seen.
- Invalid args show usage hint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel
from src.handlers.commands.system_commands import (
    _parse_catchup_args,
    handle_replay,
)

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_bot(
    *,
    is_owner: bool = True,
    args: str = "",
    target_chats: list[int] | None = None,
    catchup_results: dict[int, dict[str, int]] | None = None,
    catchup_raises: dict[int, Exception] | None = None,
) -> MagicMock:
    """Создаёт mock bot с нужными атрибутами."""
    bot = MagicMock()
    profile = MagicMock()
    profile.level = AccessLevel.OWNER if is_owner else AccessLevel.GUEST
    bot._get_access_profile = MagicMock(return_value=profile)
    bot._get_command_args = MagicMock(return_value=args)
    bot._resolve_catchup_target_chats = MagicMock(return_value=list(target_chats or []))

    results = catchup_results or {}
    raises = catchup_raises or {}

    async def _ch(chat_id: int, *, max_lookback: int | None = None) -> dict[str, int]:
        if chat_id in raises:
            raise raises[chat_id]
        return results.get(
            chat_id,
            {
                "caught_up": 0,
                "skipped_self": 0,
                "history_size": 0,
                "last_seen_before": 0,
                "last_seen_after": 0,
            },
        )

    bot._catchup_chat_history = AsyncMock(side_effect=_ch)
    return bot


def _make_message(*, chat_id: int = 312322764, from_user_id: int = 1) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.from_user = MagicMock()
    msg.from_user.id = from_user_id
    msg.reply = AsyncMock()
    return msg


# ─── _parse_catchup_args ─────────────────────────────────────────────────────


def test_parse_args_empty_returns_default() -> None:
    assert _parse_catchup_args("") == ("default", None, None)


def test_parse_args_here_only() -> None:
    assert _parse_catchup_args("here") == ("here", None, None)


def test_parse_args_here_with_lookback() -> None:
    assert _parse_catchup_args("here 100") == ("here", None, 100)


def test_parse_args_numeric_small_is_lookback() -> None:
    assert _parse_catchup_args("50") == ("default", None, 50)


def test_parse_args_negative_is_chat_id() -> None:
    assert _parse_catchup_args("-1003703978531") == ("explicit", -1003703978531, None)


def test_parse_args_chat_id_with_lookback() -> None:
    assert _parse_catchup_args("-1003703978531 100") == ("explicit", -1003703978531, 100)


def test_parse_args_invalid_text_raises() -> None:
    with pytest.raises(ValueError):
        _parse_catchup_args("garbage")


def test_parse_args_negative_lookback_raises() -> None:
    with pytest.raises(ValueError):
        _parse_catchup_args("here -5")


# ─── handle_replay — happy paths ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_no_args_runs_default_chats() -> None:
    """!replay без args → invokes _resolve_catchup_target_chats для всех."""
    bot = _make_bot(
        target_chats=[111, 222],
        catchup_results={
            111: {
                "caught_up": 3,
                "skipped_self": 0,
                "history_size": 5,
                "last_seen_before": 0,
                "last_seen_after": 5,
            },
            222: {
                "caught_up": 1,
                "skipped_self": 2,
                "history_size": 3,
                "last_seen_before": 0,
                "last_seen_after": 7,
            },
        },
    )
    msg = _make_message()
    await handle_replay(bot, msg)

    bot._resolve_catchup_target_chats.assert_called_once()
    assert bot._catchup_chat_history.call_count == 2
    # Confirm both chats invoked
    invoked_ids = {c.args[0] for c in bot._catchup_chat_history.call_args_list}
    assert invoked_ids == {111, 222}
    # max_lookback is None (default)
    for c in bot._catchup_chat_history.call_args_list:
        assert c.kwargs.get("max_lookback") is None


@pytest.mark.asyncio
async def test_replay_here_runs_current_chat_only() -> None:
    """!replay here → используется message.chat.id, _resolve не вызван."""
    bot = _make_bot(args="here")
    msg = _make_message(chat_id=987654)
    await handle_replay(bot, msg)

    bot._resolve_catchup_target_chats.assert_not_called()
    bot._catchup_chat_history.assert_called_once()
    assert bot._catchup_chat_history.call_args.args[0] == 987654


@pytest.mark.asyncio
async def test_replay_with_explicit_chat_id() -> None:
    """!replay -1003703978531 → парсит negative chat_id."""
    bot = _make_bot(args="-1003703978531")
    msg = _make_message()
    await handle_replay(bot, msg)
    bot._catchup_chat_history.assert_called_once()
    assert bot._catchup_chat_history.call_args.args[0] == -1003703978531


@pytest.mark.asyncio
async def test_replay_with_lookback_override() -> None:
    """!replay here 100 → max_lookback=100 пробрасывается."""
    bot = _make_bot(args="here 100")
    msg = _make_message()
    await handle_replay(bot, msg)
    assert bot._catchup_chat_history.call_args.kwargs["max_lookback"] == 100


@pytest.mark.asyncio
async def test_replay_default_with_lookback_only() -> None:
    """!replay 50 → defaults + lookback=50."""
    bot = _make_bot(args="50", target_chats=[111])
    msg = _make_message()
    await handle_replay(bot, msg)
    assert bot._catchup_chat_history.call_args.kwargs["max_lookback"] == 50


# ─── handle_replay — guards/errors ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_owner_only_rejects_others() -> None:
    """Non-owner получает 🔒 без вызова catchup."""
    bot = _make_bot(is_owner=False)
    msg = _make_message()
    await handle_replay(bot, msg)

    bot._catchup_chat_history.assert_not_called()
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args.args[0]
    assert "🔒" in text
    assert "владельцу" in text


@pytest.mark.asyncio
async def test_replay_invalid_args_shows_usage_hint() -> None:
    """!replay garbage → usage hint, без catchup."""
    bot = _make_bot(args="garbage_input")
    msg = _make_message()
    await handle_replay(bot, msg)

    bot._catchup_chat_history.assert_not_called()
    msg.reply.assert_awaited_once()
    text = msg.reply.call_args.args[0]
    assert "Использование" in text or "Использование !replay" in text
    assert "!replay" in text


@pytest.mark.asyncio
async def test_replay_returns_summary_text() -> None:
    """Successful replay → структурированный summary с counts."""
    bot = _make_bot(
        args="here",
        catchup_results={
            312322764: {
                "caught_up": 5,
                "skipped_self": 2,
                "history_size": 8,
                "last_seen_before": 100,
                "last_seen_after": 108,
            }
        },
    )
    msg = _make_message(chat_id=312322764)
    await handle_replay(bot, msg)

    text = msg.reply.call_args.args[0]
    assert "Replay" in text
    assert "caught_up=5" in text
    assert "skipped_self=2" in text
    assert "last_seen=108" in text
    assert "312322764" in text


@pytest.mark.asyncio
async def test_replay_per_chat_failure_does_not_block_others() -> None:
    """Если один chat fails — остальные продолжают, текст содержит обе строки."""
    bot = _make_bot(
        target_chats=[111, 222],
        catchup_results={
            222: {
                "caught_up": 3,
                "skipped_self": 0,
                "history_size": 3,
                "last_seen_before": 0,
                "last_seen_after": 5,
            }
        },
        catchup_raises={111: RuntimeError("boom")},
    )
    msg = _make_message()
    await handle_replay(bot, msg)
    text = msg.reply.call_args.args[0]
    assert "111" in text
    assert "222" in text
    # Aggregate totals presented when len(targets) > 1
    assert "Итого" in text


@pytest.mark.asyncio
async def test_replay_no_default_targets() -> None:
    """!replay без default targets → warning без catchup."""
    bot = _make_bot(target_chats=[])
    msg = _make_message()
    await handle_replay(bot, msg)
    bot._catchup_chat_history.assert_not_called()
    text = msg.reply.call_args.args[0]
    assert "default target chats" in text or "target" in text
