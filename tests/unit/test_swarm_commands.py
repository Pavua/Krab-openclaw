# -*- coding: utf-8 -*-
"""
Тесты для commands/swarm_commands.py (Phase 2 Wave 8, Session 27).

Покрытие:
- TestReExports: handle_swarm и _AgentRoomRouterAdapter сохраняют API через
  src.handlers.command_handlers (важно для userbot_bridge cron, ai_commands
  fallback и существующих monkeypatch'ей в test_swarm_status_deep /
  test_cron_prompt_context).
- handle_swarm smoke: пустой args показывает inline-кнопки, "teams" зовёт
  list_teams, "task board" — task_board.get_board_summary.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers import command_handlers as ch
from src.handlers.commands import swarm_commands as sw


def _make_message(args_text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        text=f"!swarm {args_text}".strip(),
        chat=SimpleNamespace(id=-1001000000001),
        from_user=SimpleNamespace(id=42, first_name="Pablo", username="pablo"),
        reply=AsyncMock(),
        id=1,
    )


def _make_bot(args: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        _get_command_args=lambda _msg: args,
        _get_access_profile=lambda _user: SimpleNamespace(level=None),
        _is_allowed_sender=lambda _user: True,
        _build_system_prompt_for_sender=lambda **_kw: "system",
    )


class TestReExports:
    """Re-exports через src.handlers.command_handlers — preserve API."""

    def test_handle_swarm_re_exported(self) -> None:
        assert ch.handle_swarm is sw.handle_swarm

    def test_agent_room_router_adapter_re_exported(self) -> None:
        assert ch._AgentRoomRouterAdapter is sw._AgentRoomRouterAdapter

    def test_handle_swarm_module_origin(self) -> None:
        # handle_swarm физически живёт в swarm_commands
        assert sw.handle_swarm.__module__ == "src.handlers.commands.swarm_commands"

    def test_adapter_module_origin(self) -> None:
        assert sw._AgentRoomRouterAdapter.__module__ == "src.handlers.commands.swarm_commands"


class TestAdapterShape:
    """Проверка контракта _AgentRoomRouterAdapter."""

    def test_init_defaults_team_to_none(self) -> None:
        adapter = sw._AgentRoomRouterAdapter(chat_id="chat:1", system_prompt="sys")
        assert adapter.chat_id == "chat:1"
        assert adapter.system_prompt == "sys"
        assert adapter.team_name is None

    def test_init_empty_team_normalized_to_none(self) -> None:
        adapter = sw._AgentRoomRouterAdapter(chat_id="c", system_prompt="s", team_name="")
        assert adapter.team_name is None

    def test_init_named_team_preserved(self) -> None:
        adapter = sw._AgentRoomRouterAdapter(chat_id="c", system_prompt="s", team_name="traders")
        assert adapter.team_name == "traders"

    def test_route_query_is_async(self) -> None:
        import inspect

        assert inspect.iscoroutinefunction(sw._AgentRoomRouterAdapter.route_query)


class TestHandleSwarmSmoke:
    """Smoke-тесты для основных subcommands handle_swarm."""

    @pytest.mark.asyncio
    async def test_empty_args_shows_buttons(self) -> None:
        bot = _make_bot(args="")
        msg = _make_message("")
        await sw.handle_swarm(bot, msg)
        msg.reply.assert_awaited_once()
        # inline-кнопки переданы через reply_markup
        kwargs = msg.reply.await_args.kwargs
        assert "reply_markup" in kwargs

    @pytest.mark.asyncio
    async def test_teams_subcommand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bot = _make_bot(args="teams")
        msg = _make_message("teams")

        from src.core import swarm_bus

        monkeypatch.setattr(swarm_bus, "list_teams", lambda: "teams_listing_stub")
        await sw.handle_swarm(bot, msg)
        msg.reply.assert_awaited_once_with("teams_listing_stub")

    @pytest.mark.asyncio
    async def test_task_board_subcommand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bot = _make_bot(args="task board")
        msg = _make_message("task board")

        from src.core import swarm_task_board as stb_mod

        monkeypatch.setattr(
            stb_mod.swarm_task_board,
            "get_board_summary",
            lambda: {"by_status": {"done": 3, "pending": 1}, "by_team": {"traders": 2}, "total": 4},
        )
        await sw.handle_swarm(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Swarm Task Board" in text
        assert "done: 3" in text
        assert "pending: 1" in text
        assert "Всего: 4" in text
