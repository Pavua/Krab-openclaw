# -*- coding: utf-8 -*-
"""
Тесты для commands/ai_commands.py (Phase 2 Wave 7, Session 27).

Покрытие:
- _parse_ask_memory_flags: парсинг --with-memory / --no-memory флагов;
- _rate_asset_label: алиасы криптотикеров и upper для акций;
- _build_rate_prompt: промпт для одного и нескольких активов;
- _format_chat_history_for_llm: форматирование истории Pyrogram;
- _render_daily_report: вывод markdown;
- handle_ask: UserInputError при отсутствии reply;
- handle_search: UserInputError при пустом запросе;
- handle_rate: UserInputError при пустом запросе;
- handle_explain: UserInputError при отсутствии кода;
- handle_fix: UserInputError при пустом тексте;
- handle_rewrite: UserInputError при пустом тексте;
- handle_report: 🔒 для не-owner;
- handle_agent: UserInputError на пустой ввод;
- TestReExports: API сохранён через src.handlers.command_handlers.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.commands import ai_commands as ai

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str = "", reply: object | None = None) -> SimpleNamespace:
    """Stub Pyrogram Message c text/reply/chat и async-методами."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=-1001000000001),
        from_user=SimpleNamespace(id=42, first_name="Pablo", last_name=None, username="pablo"),
        reply_to_message=reply,
        reply=AsyncMock(),
        delete=AsyncMock(),
        id=1,
    )


def _make_bot(args: str = "", *, level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    """Stub KraabUserbot с _get_command_args и _get_access_profile."""
    return SimpleNamespace(
        _get_command_args=lambda _msg: args,
        _get_access_profile=lambda _user: SimpleNamespace(level=level),
        client=SimpleNamespace(),
        current_role="default",
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParseAskMemoryFlags:
    def test_no_flags(self) -> None:
        q, force = ai._parse_ask_memory_flags("привет мир")
        assert q == "привет мир"
        assert force is None

    def test_with_memory(self) -> None:
        q, force = ai._parse_ask_memory_flags("--with-memory кратко")
        assert q == "кратко"
        assert force is True

    def test_no_memory_alias(self) -> None:
        q, force = ai._parse_ask_memory_flags("кратко --no-mem")
        assert q == "кратко"
        assert force is False

    def test_empty(self) -> None:
        q, force = ai._parse_ask_memory_flags("")
        assert q == ""
        assert force is None


class TestRateHelpers:
    def test_crypto_alias(self) -> None:
        assert ai._rate_asset_label("btc") == "Bitcoin (BTC)"
        assert ai._rate_asset_label("BTC") == "Bitcoin (BTC)"

    def test_unknown_ticker_uppercased(self) -> None:
        assert ai._rate_asset_label("aapl") == "AAPL"

    def test_build_rate_prompt_single(self) -> None:
        prompt = ai._build_rate_prompt(["btc"])
        assert "Bitcoin (BTC)" in prompt
        assert "цену в USD" in prompt

    def test_build_rate_prompt_multi(self) -> None:
        prompt = ai._build_rate_prompt(["btc", "eth"])
        assert "Bitcoin (BTC)" in prompt
        assert "Ethereum (ETH)" in prompt
        assert "сравнение" in prompt


class TestFormatChatHistory:
    def test_empty_list(self) -> None:
        assert ai._format_chat_history_for_llm([]) == ""

    def test_basic(self) -> None:
        msgs = [
            SimpleNamespace(
                from_user=SimpleNamespace(first_name="Anna", last_name=None, username=None, id=1),
                text="Привет!",
                caption=None,
                date=None,
                sender_chat=None,
            )
        ]
        out = ai._format_chat_history_for_llm(msgs)
        assert "Anna" in out
        assert "Привет" in out

    def test_media_fallback(self) -> None:
        msgs = [
            SimpleNamespace(
                from_user=SimpleNamespace(first_name=None, last_name=None, username="x", id=2),
                text=None,
                caption=None,
                photo=True,
                date=None,
                sender_chat=None,
            )
        ]
        out = ai._format_chat_history_for_llm(msgs)
        assert "[фото]" in out
        assert "@x" in out


class TestRenderDailyReport:
    def test_basic(self) -> None:
        data = {
            "cost_today_usd": 1.2345,
            "cost_month_usd": 12.0,
            "calls_today": 3,
            "tokens_today": 1000,
            "swarm_rounds_today": 0,
            "swarm_teams_today": [],
            "swarm_duration_today": 0,
            "inbox_open": 5,
            "inbox_errors": 1,
            "inbox_warnings": 2,
        }
        out = ai._render_daily_report(data)
        assert "Daily Report" in out
        assert "$1.2345" in out
        assert "Открытых: 5" in out


# ---------------------------------------------------------------------------
# Handler input-validation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_ask_no_reply_raises() -> None:
    msg = _make_message(reply=None)
    bot = _make_bot(args="кратко")
    with pytest.raises(UserInputError):
        await ai.handle_ask(bot, msg)


@pytest.mark.asyncio
async def test_handle_search_empty_raises() -> None:
    msg = _make_message()
    bot = _make_bot(args="")
    with pytest.raises(UserInputError):
        await ai.handle_search(bot, msg)


@pytest.mark.asyncio
async def test_handle_rate_empty_raises() -> None:
    msg = _make_message()
    bot = _make_bot(args="")
    with pytest.raises(UserInputError):
        await ai.handle_rate(bot, msg)


@pytest.mark.asyncio
async def test_handle_explain_empty_raises() -> None:
    msg = _make_message(reply=None)
    bot = _make_bot(args="")
    with pytest.raises(UserInputError):
        await ai.handle_explain(bot, msg)


@pytest.mark.asyncio
async def test_handle_fix_empty_no_reply_raises() -> None:
    msg = _make_message(reply=None)
    bot = _make_bot(args="")
    with pytest.raises(UserInputError):
        await ai.handle_fix(bot, msg)


@pytest.mark.asyncio
async def test_handle_rewrite_empty_no_reply_raises() -> None:
    msg = _make_message(reply=None)
    bot = _make_bot(args="")
    with pytest.raises(UserInputError):
        await ai.handle_rewrite(bot, msg)


@pytest.mark.asyncio
async def test_handle_report_non_owner_blocked() -> None:
    msg = _make_message()
    bot = _make_bot(args="daily", level=AccessLevel.GUEST)
    with pytest.raises(UserInputError):
        await ai.handle_report(bot, msg)


@pytest.mark.asyncio
async def test_handle_report_owner_help_raises() -> None:
    msg = _make_message()
    bot = _make_bot(args="")
    # Owner без аргументов — UserInputError со справкой
    with pytest.raises(UserInputError):
        await ai.handle_report(bot, msg)


@pytest.mark.asyncio
async def test_handle_agent_empty_raises() -> None:
    msg = _make_message()
    bot = _make_bot(args="")
    with pytest.raises(UserInputError):
        await ai.handle_agent(bot, msg)


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------


class TestReExports:
    """Re-exports через src.handlers.command_handlers — preserve API."""

    def test_handlers_re_exported(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch.handle_ask is ai.handle_ask
        assert ch.handle_search is ai.handle_search
        assert ch.handle_agent is ai.handle_agent
        assert ch.handle_rate is ai.handle_rate
        assert ch.handle_explain is ai.handle_explain
        assert ch.handle_fix is ai.handle_fix
        assert ch.handle_rewrite is ai.handle_rewrite
        assert ch.handle_summary is ai.handle_summary
        assert ch.handle_catchup is ai.handle_catchup
        assert ch.handle_report is ai.handle_report

    def test_helpers_and_state_re_exported(self) -> None:
        from src.handlers import command_handlers as ch

        assert ch._parse_ask_memory_flags is ai._parse_ask_memory_flags
        assert ch._rate_asset_label is ai._rate_asset_label
        assert ch._build_rate_prompt is ai._build_rate_prompt
        assert ch._format_chat_history_for_llm is ai._format_chat_history_for_llm
        assert ch._collect_daily_report_data is ai._collect_daily_report_data
        assert ch._render_daily_report is ai._render_daily_report
        assert ch._REWRITE_MODES is ai._REWRITE_MODES
        assert ch._RATE_CRYPTO_ALIASES is ai._RATE_CRYPTO_ALIASES
        assert ch._SUMMARY_DEFAULT_N == ai._SUMMARY_DEFAULT_N
        assert ch._SUMMARY_MAX_N == ai._SUMMARY_MAX_N
        assert ch._EXPLAIN_PROMPT == ai._EXPLAIN_PROMPT
