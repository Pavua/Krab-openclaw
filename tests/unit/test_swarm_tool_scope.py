# -*- coding: utf-8 -*-
"""Тесты для src/core/swarm_tool_scope.py."""

import pytest

from src.core.swarm_tool_scope import (
    TEAM_TOOL_SETS,
    format_tool_hint,
    get_team_tools,
)


class TestGetTeamTools:
    def test_known_team_has_peekaboo(self):
        tools = get_team_tools("traders")
        assert any("peekaboo" in t for t in tools)

    def test_traders_has_coingecko_reference(self):
        tools = get_team_tools("traders")
        combined = " ".join(tools)
        assert "Coingecko" in combined or "coingecko" in combined.lower() or "DeFi" in combined

    def test_coders_has_run_tests(self):
        tools = get_team_tools("coders")
        assert any("krab_run_tests" in t for t in tools)

    def test_analysts_has_telegram_search(self):
        tools = get_team_tools("analysts")
        assert any("telegram_search" in t for t in tools)

    def test_creative_has_telegram_send(self):
        tools = get_team_tools("creative")
        assert any("telegram_send_message" in t for t in tools)

    def test_unknown_team_returns_base(self):
        tools = get_team_tools("unknown_team")
        assert any("web_search" in t for t in tools)
        assert any("peekaboo" in t for t in tools)

    def test_tor_tool_added_when_enabled(self):
        tools = get_team_tools("coders", tor_enabled=True)
        assert any("tor_fetch" in t for t in tools)

    def test_tor_tool_absent_when_disabled(self):
        tools = get_team_tools("traders", tor_enabled=False)
        assert not any("tor_fetch" in t for t in tools)

    def test_case_insensitive(self):
        tools_lower = get_team_tools("traders")
        tools_upper = get_team_tools("TRADERS")
        assert tools_lower == tools_upper


class TestFormatToolHint:
    def test_first_role_has_mandatory_language(self):
        hint = format_tool_hint("analysts", role_idx=0)
        assert "ОБЯЗАН" in hint

    def test_later_role_has_softer_language(self):
        hint = format_tool_hint("analysts", role_idx=1)
        assert "ОБЯЗАН" not in hint
        assert "web_search" in hint

    def test_hint_contains_team_tools(self):
        hint = format_tool_hint("coders", role_idx=0)
        assert "krab_run_tests" in hint

    def test_hint_is_nonempty_for_all_teams(self):
        for team in TEAM_TOOL_SETS:
            hint = format_tool_hint(team, role_idx=0)
            assert len(hint) > 20, f"hint too short for team {team}"

    def test_tor_in_hint_when_enabled(self):
        hint = format_tool_hint("traders", tor_enabled=True, role_idx=0)
        assert "tor_fetch" in hint

    def test_default_team_fallback(self):
        # default / неизвестная команда не должна упасть
        hint = format_tool_hint("default", role_idx=0)
        assert "web_search" in hint
