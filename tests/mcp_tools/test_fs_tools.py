"""Тесты filesystem MCP tools (fs_read_file / fs_search / fs_list_dir + sanitize_path)."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_fs_read_file_reads_pyproject(mcp_server):
    result = await mcp_server.fs_read_file(
        mcp_server._FsReadInput(path="pyproject.toml", start_line=1, end_line=5)
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["path"].endswith("pyproject.toml")
    assert data["start_line"] == 1
    assert data["end_line"] <= 5
    assert "content" in data and len(data["content"]) > 0


@pytest.mark.asyncio
async def test_fs_read_file_rejects_escape(mcp_server):
    # Попытка выйти из sandbox — /etc/passwd
    result = await mcp_server.fs_read_file(
        mcp_server._FsReadInput(path="/etc/passwd")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert "path_escape" in data["error"]


@pytest.mark.asyncio
async def test_fs_read_file_rejects_dotdot_escape(mcp_server):
    result = await mcp_server.fs_read_file(
        mcp_server._FsReadInput(path="../../../etc/hosts")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert "path_escape" in data["error"]


@pytest.mark.asyncio
async def test_fs_search_finds_known_string(mcp_server):
    # Ищем уникальную сигнатуру из server.py
    result = await mcp_server.fs_search(
        mcp_server._FsSearchInput(pattern="Krab Telegram MCP Server", glob="*.py", max_results=10)
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["count"] >= 1
    assert all("path" in m and "line" in m for m in data["matches"])


@pytest.mark.asyncio
async def test_fs_search_respects_max_results(mcp_server):
    result = await mcp_server.fs_search(
        mcp_server._FsSearchInput(pattern="def ", glob="*.py", max_results=5)
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["count"] <= 5


@pytest.mark.asyncio
async def test_fs_list_dir_root(mcp_server):
    result = await mcp_server.fs_list_dir(mcp_server._FsListDirInput(path="."))
    data = json.loads(result)
    assert data["ok"] is True
    names = {e["name"] for e in data["entries"]}
    assert "pyproject.toml" in names
    # Минимум один entry имеет type/size/mtime
    sample = data["entries"][0]
    assert {"name", "type", "size", "mtime"}.issubset(sample)


@pytest.mark.asyncio
async def test_fs_list_dir_rejects_escape(mcp_server):
    result = await mcp_server.fs_list_dir(mcp_server._FsListDirInput(path="/tmp"))
    data = json.loads(result)
    assert data["ok"] is False
    assert "path_escape" in data["error"]


def test_sanitize_path_accepts_relative(mcp_server):
    p = mcp_server._sanitize_path("pyproject.toml")
    assert p.name == "pyproject.toml"


def test_sanitize_path_rejects_outside_root(mcp_server):
    with pytest.raises(ValueError):
        mcp_server._sanitize_path("/usr/bin/env")
