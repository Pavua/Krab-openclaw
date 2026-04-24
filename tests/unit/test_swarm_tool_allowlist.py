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

import asyncio
import contextvars

import pytest

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


@pytest.mark.asyncio
async def test_context_var_isolated_in_concurrent_tasks() -> None:
    """ContextVar изолирован между конкурирующими корутинами.

    Каждая задача запускается в собственной copy_context() — иначе все они
    делят ContextVar главной корутины, и последний set() перетрёт все остальные.
    Такой паттерн обязателен для свёрм-rounds, где несколько команд выполняются
    параллельно (asyncio.gather).
    """

    async def team_task(team_name: str, expected: str) -> str:
        stl.set_current_team(team_name)
        # yield control — даём другим задачам шанс переписать ContextVar,
        # если вдруг они делят один и тот же context.
        await asyncio.sleep(0.01)
        got = stl.get_current_team()
        assert got == expected, f"expected={expected!r} got={got!r}"
        return got or ""

    # Python 3.11+: asyncio.create_task поддерживает context=.
    # Каждая задача получает свою копию Context — set_current_team внутри
    # одной не перетирает state других.
    tasks = [
        asyncio.create_task(
            team_task(name, name),
            context=contextvars.copy_context(),
        )
        for name in ("traders", "coders", "analysts", "creative")
    ]
    results = await asyncio.gather(*tasks)
    assert sorted(results) == ["analysts", "coders", "creative", "traders"]
    # За пределами всех задач — ContextVar главной корутины не задет.
    assert stl.get_current_team() is None


@pytest.mark.asyncio
async def test_reset_token_from_wrong_context_safe() -> None:
    """reset_current_team с token из другого Context не ломает state.

    Token, полученный в одной копии Context, не валиден в другой. Вызов
    reset_current_team() должен молча проглотить ValueError/LookupError
    (см. implementation в swarm_tool_allowlist.py).
    """

    # Получаем token в "чужом" контексте (копия текущего).
    foreign_ctx = contextvars.copy_context()
    stolen_token: list[contextvars.Token] = []

    def grab_token() -> None:
        stolen_token.append(stl.set_current_team("foreign"))

    foreign_ctx.run(grab_token)
    assert stolen_token, "token was not captured"

    # В «своём» контексте пытаемся сбросить чужой token — не должно упасть.
    stl.set_current_team("local")
    # Должно молча проглотиться, без исключения.
    stl.reset_current_team(stolen_token[0])
    # State не обязан быть восстановлен в None, но и не должен упасть.
    # Главное — модуль жив и ContextVar не в broken-state.
    assert stl.get_current_team() in ("local", None, "foreign")

    # Cleanup — нормальный reset последующим set/None.
    stl.set_current_team(None)
    assert stl.get_current_team() is None


def test_blocked_counter_increments() -> None:
    """record_blocked_tool накапливает счётчик per (team, tool)."""
    before = stl.get_blocked_tool_stats().get(("traders", "fake_tool"), 0)
    stl.record_blocked_tool("traders", "fake_tool")
    stl.record_blocked_tool("traders", "fake_tool")
    after = stl.get_blocked_tool_stats().get(("traders", "fake_tool"), 0)
    assert after == before + 2
