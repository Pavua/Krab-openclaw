"""Тесты git MCP tools (git_status / git_log / git_diff)."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_git_status_returns_branch_header(mcp_server):
    result = await mcp_server.git_status()
    data = json.loads(result)
    assert data["ok"] is True
    # --branch даёт строку вида "## branch-name..."
    assert data["output"].startswith("##") or data["output"] == ""


@pytest.mark.asyncio
async def test_git_status_shape(mcp_server):
    result = await mcp_server.git_status()
    data = json.loads(result)
    assert set(data.keys()) >= {"ok", "exit_code", "output"}


@pytest.mark.asyncio
async def test_git_log_returns_commits(mcp_server):
    result = await mcp_server.git_log(mcp_server._GitLogInput(limit=3))
    data = json.loads(result)
    assert data["ok"] is True
    lines = [ln for ln in data["output"].splitlines() if ln.strip()]
    assert 1 <= len(lines) <= 3


@pytest.mark.asyncio
async def test_git_log_rejects_escape_file(mcp_server):
    result = await mcp_server.git_log(mcp_server._GitLogInput(limit=5, file="/etc/passwd"))
    data = json.loads(result)
    assert data["ok"] is False
    assert "path_escape" in data["error"]


@pytest.mark.asyncio
async def test_git_diff_basic(mcp_server):
    result = await mcp_server.git_diff(mcp_server._GitDiffInput())
    data = json.loads(result)
    assert "ok" in data and "output" in data and "truncated" in data


@pytest.mark.asyncio
async def test_git_diff_staged_flag(mcp_server):
    result = await mcp_server.git_diff(mcp_server._GitDiffInput(staged=True))
    data = json.loads(result)
    # Не важно, есть ли diff — важно что команда отработала
    assert "exit_code" in data
