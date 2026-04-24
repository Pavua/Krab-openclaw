# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_tool_allowlist.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit-тесты per-team tool allowlist.

Покрывает:
- фильтрация manifest'а под команду (whitelist + base);
- backward-compat: неизвестная команда / пустой team → passthrough;
- resolve алиасов (русские имена команд);
- ContextVar set/reset;
- silent-strip guard is_tool_allowed.
"""

from __future__ import annotations

from src.core import swarm_tool_allowlist as stl


def _mk_tool(name: str) -> dict:
    """Хелпер — сформировать минимальный OpenAI-совместимый tool-entry."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"fake {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_filter_keeps_only_allowed_tools() -> None:
    """traders видят web_search / krab_memory_search / tor_fetch, но не coder-tools."""
    manifest = [
        _mk_tool("web_search"),
        _mk_tool("krab-yung-nagato__krab_memory_search"),
        _mk_tool("krab-yung-nagato__krab_run_tests"),  # coders only
        _mk_tool("telegram_send_message"),  # creative only
        _mk_tool("peekaboo"),  # analysts only
    ]

    filtered = stl.filter_tools_for_team(manifest, "traders")
    names = {t["function"]["name"] for t in filtered}

    assert "web_search" in names
    assert "krab-yung-nagato__krab_memory_search" in names
    assert "krab-yung-nagato__krab_run_tests" not in names
    assert "telegram_send_message" not in names
    assert "peekaboo" not in names


def test_unknown_team_returns_full_manifest() -> None:
    """Неизвестная / пустая команда → manifest не меняется (backward-compat)."""
    manifest = [_mk_tool("web_search"), _mk_tool("krab-yung-nagato__krab_run_tests")]

    # None — нет свёрм-контекста.
    assert stl.filter_tools_for_team(manifest, None) == manifest
    # "" — то же самое.
    assert stl.filter_tools_for_team(manifest, "") == manifest
    # Незнакомая команда → passthrough.
    assert stl.filter_tools_for_team(manifest, "unknown_team_xyz") == manifest


def test_alias_resolution() -> None:
    """Русский алиас `трейдеры` фильтруется как канонический `traders`."""
    manifest = [
        _mk_tool("web_search"),
        _mk_tool("krab-yung-nagato__krab_run_tests"),
    ]

    filtered_alias = stl.filter_tools_for_team(manifest, "трейдеры")
    filtered_canon = stl.filter_tools_for_team(manifest, "traders")

    # Оба результата эквивалентны по набору tool-имён.
    names_alias = {t["function"]["name"] for t in filtered_alias}
    names_canon = {t["function"]["name"] for t in filtered_canon}
    assert names_alias == names_canon
    assert "web_search" in names_alias
    assert "krab-yung-nagato__krab_run_tests" not in names_alias


def test_coders_allow_run_tests_and_tail_logs() -> None:
    """coders видят свои dev-tools, но не telegram_send_message."""
    manifest = [
        _mk_tool("krab-yung-nagato__krab_run_tests"),
        _mk_tool("krab-yung-nagato__krab_tail_logs"),
        _mk_tool("web_search"),  # base
        _mk_tool("telegram_send_message"),  # creative only
    ]
    filtered = stl.filter_tools_for_team(manifest, "coders")
    names = {t["function"]["name"] for t in filtered}

    assert "krab-yung-nagato__krab_run_tests" in names
    assert "krab-yung-nagato__krab_tail_logs" in names
    assert "web_search" in names
    assert "telegram_send_message" not in names


def test_context_var_set_and_reset() -> None:
    """ContextVar выставляется и корректно сбрасывается."""
    assert stl.get_current_team() is None
    token = stl.set_current_team("analysts")
    try:
        assert stl.get_current_team() == "analysts"
    finally:
        stl.reset_current_team(token)
    assert stl.get_current_team() is None


def test_is_tool_allowed_silent_guard() -> None:
    """is_tool_allowed — fast-path для mcp_client guard'а."""
    # traders: web_search разрешён, krab_run_tests нет.
    assert stl.is_tool_allowed("web_search", "traders") is True
    assert stl.is_tool_allowed("krab-yung-nagato__krab_run_tests", "traders") is False
    # coders: разрешён.
    assert stl.is_tool_allowed("krab-yung-nagato__krab_run_tests", "coders") is True
    # Без команды — всё разрешено (backward-compat).
    assert stl.is_tool_allowed("whatever_tool", None) is True
    # Незнакомая команда — всё разрешено.
    assert stl.is_tool_allowed("krab_run_tests", "unknown_team") is True


def test_blocked_counter_increments() -> None:
    """record_blocked_tool накапливает счётчик per (team, tool)."""
    before = stl.get_blocked_tool_stats().get(("traders", "fake_tool"), 0)
    stl.record_blocked_tool("traders", "fake_tool")
    stl.record_blocked_tool("traders", "fake_tool")
    after = stl.get_blocked_tool_stats().get(("traders", "fake_tool"), 0)
    assert after == before + 2
