# -*- coding: utf-8 -*-
"""Тесты runtime MCP-клиента на базе managed MCP registry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_client import MCPClientManager


@pytest.mark.asyncio
async def test_ensure_server_uses_managed_registry_launch() -> None:
    manager = MCPClientManager()
    manager.start_server = AsyncMock(return_value=True)

    with patch("src.mcp_client.get_managed_mcp_servers", return_value={"context7": {"name": "context7"}}):
        with patch(
            "src.mcp_client.resolve_managed_server_launch",
            return_value={
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "env": {"CONTEXT7_API_KEY": "ctx-demo"},
                "missing_env": [],
            },
        ):
            ok = await manager.ensure_server("context7")

    assert ok is True
    manager.start_server.assert_awaited_once_with(
        "context7",
        "npx",
        ["-y", "@upstash/context7-mcp"],
        env={"CONTEXT7_API_KEY": "ctx-demo"},
    )


@pytest.mark.asyncio
async def test_ensure_server_returns_false_when_required_env_missing() -> None:
    manager = MCPClientManager()
    manager.start_server = AsyncMock(return_value=True)

    with patch("src.mcp_client.get_managed_mcp_servers", return_value={"github": {"name": "github"}}):
        with patch(
            "src.mcp_client.resolve_managed_server_launch",
            return_value={
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {},
                "missing_env": ["GITHUB_TOKEN"],
            },
        ):
            ok = await manager.ensure_server("github")

    assert ok is False
    manager.start_server.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_web_falls_back_to_firecrawl_when_brave_unavailable() -> None:
    manager = MCPClientManager()
    manager.ensure_server = AsyncMock(side_effect=[False, True])
    manager.call_tool = AsyncMock(
        return_value=type(
            "ToolResult",
            (),
            {"content": [type("TextPart", (), {"text": "firecrawl ok"})()]},
        )()
    )

    result = await manager.search_web("krab mcp")

    assert result == "firecrawl ok"
    assert manager.call_tool.await_args.args[0] == "firecrawl"
    assert manager.call_tool.await_args.args[1] == "firecrawl_search"


# ---------------------------------------------------------------------------
# call_tool_unified
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_unified_unknown_format_returns_error() -> None:
    """call_tool_unified с именем без '__' и без нативных — возвращает ошибку."""
    manager = MCPClientManager()
    result = await manager.call_tool_unified("some_random_tool", {})
    assert "Неизвестный формат" in result


@pytest.mark.asyncio
async def test_call_tool_unified_routes_to_server_tool() -> None:
    """call_tool_unified с форматом server__tool вызывает call_tool и форматирует результат."""
    manager = MCPClientManager()

    fake_result = type(
        "ToolResult",
        (),
        {"content": [type("TextPart", (), {"text": "hello from tool"})()]},
    )()
    manager.call_tool = AsyncMock(return_value=fake_result)

    result = await manager.call_tool_unified("my_server__my_tool", {"arg": "val"})

    assert result == "hello from tool"
    manager.call_tool.assert_awaited_once_with("my_server", "my_tool", {"arg": "val"})


@pytest.mark.asyncio
async def test_call_tool_unified_web_search_dispatches() -> None:
    """call_tool_unified('web_search', ...) делегирует _web_search_impl."""
    manager = MCPClientManager()
    manager._web_search_impl = AsyncMock(return_value="search results")

    result = await manager.call_tool_unified("web_search", {"query": "test"})

    assert result == "search results"
    manager._web_search_impl.assert_awaited_once_with({"query": "test"})


@pytest.mark.asyncio
async def test_call_tool_unified_peekaboo_dispatches() -> None:
    """call_tool_unified('peekaboo', ...) делегирует _peekaboo_impl."""
    manager = MCPClientManager()
    manager._peekaboo_impl = AsyncMock(return_value="screenshot done")

    result = await manager.call_tool_unified("peekaboo", {"reason": "test"})

    assert result == "screenshot done"
    manager._peekaboo_impl.assert_awaited_once_with({"reason": "test"})


# ---------------------------------------------------------------------------
# get_tool_manifest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tool_manifest_no_sessions_has_native_tools() -> None:
    """Без активных сессий манифест содержит нативные инструменты (peekaboo, web_search)."""
    import src.config as _cfg_mod

    manager = MCPClientManager()
    # sessions пустые

    with patch.object(_cfg_mod.config, "TOR_ENABLED", False):
        manifest = await manager.get_tool_manifest()

    names = [entry["function"]["name"] for entry in manifest]
    assert "peekaboo" in names
    assert "web_search" in names


@pytest.mark.asyncio
async def test_get_tool_manifest_includes_tor_when_enabled() -> None:
    """tor_fetch появляется в манифесте, когда модуль config имеет TOR_ENABLED=True.

    Примечание: в get_tool_manifest используется getattr(модуль_config, "TOR_ENABLED"),
    поэтому патчим атрибут на уровне модуля src.config.
    """
    import src.config as _cfg_mod

    manager = MCPClientManager()

    # Ставим TOR_ENABLED на модуль (именно туда смотрит getattr в mcp_client)
    with patch.object(_cfg_mod, "TOR_ENABLED", True, create=True):
        manifest = await manager.get_tool_manifest()

    names = [entry["function"]["name"] for entry in manifest]
    assert "tor_fetch" in names


@pytest.mark.asyncio
async def test_get_tool_manifest_excludes_tor_when_disabled() -> None:
    """tor_fetch отсутствует в манифесте при TOR_ENABLED=False."""
    import src.config as _cfg_mod

    manager = MCPClientManager()

    with patch.object(_cfg_mod.config, "TOR_ENABLED", False):
        manifest = await manager.get_tool_manifest()

    names = [entry["function"]["name"] for entry in manifest]
    assert "tor_fetch" not in names


@pytest.mark.asyncio
async def test_get_tool_manifest_includes_session_tools() -> None:
    """Инструменты из активных сессий добавляются с префиксом server__."""
    import src.config as _cfg_mod

    manager = MCPClientManager()

    fake_tool = type(
        "FakeTool",
        (),
        {
            "name": "do_thing",
            "description": "Does a thing",
            "inputSchema": {"type": "object", "properties": {}},
        },
    )()
    fake_list_result = type("ListResult", (), {"tools": [fake_tool]})()

    fake_session = AsyncMock()
    fake_session.list_tools = AsyncMock(return_value=fake_list_result)
    manager.sessions["my_server"] = fake_session

    with patch.object(_cfg_mod.config, "TOR_ENABLED", False):
        manifest = await manager.get_tool_manifest()

    names = [entry["function"]["name"] for entry in manifest]
    assert "my_server__do_thing" in names


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_not_started() -> None:
    """health_check возвращает ok=False, если is_running=False."""
    manager = MCPClientManager()
    assert manager.is_running is False

    result = await manager.health_check()

    assert result["ok"] is False
    assert result["error"] == "not_started"


@pytest.mark.asyncio
async def test_health_check_running_no_sessions() -> None:
    """health_check с is_running=True, но без сессий → ok=False."""
    manager = MCPClientManager()
    manager.is_running = True

    result = await manager.health_check()

    assert result["ok"] is False
    assert result["error"] == "no_active_sessions"
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_health_check_running_with_sessions() -> None:
    """health_check с активными сессиями → ok=True, count корректен."""
    manager = MCPClientManager()
    manager.is_running = True
    manager.sessions["server_a"] = AsyncMock()
    manager.sessions["server_b"] = AsyncMock()

    result = await manager.health_check()

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["error"] == ""


# ---------------------------------------------------------------------------
# call_tool (error paths)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_no_session_returns_none() -> None:
    """call_tool без активной сессии возвращает None, не бросает исключение."""
    manager = MCPClientManager()
    result = await manager.call_tool("missing_server", "some_tool", {})
    assert result is None


@pytest.mark.asyncio
async def test_call_tool_session_raises_returns_none() -> None:
    """Если сессия.call_tool бросает ConnectionError — возвращаем None."""
    manager = MCPClientManager()
    fake_session = AsyncMock()
    fake_session.call_tool = AsyncMock(side_effect=ConnectionError("broken"))
    manager.sessions["s"] = fake_session

    result = await manager.call_tool("s", "some_tool", {})
    assert result is None
