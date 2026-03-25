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
