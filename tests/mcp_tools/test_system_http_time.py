"""Тесты system / http_fetch / time MCP tools."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

# ── system_info ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_info_has_platform(mcp_server):
    result = await mcp_server.system_info()
    data = json.loads(result)
    assert "platform" in data and "python" in data


@pytest.mark.asyncio
async def test_system_info_disk_shape(mcp_server):
    result = await mcp_server.system_info()
    data = json.loads(result)
    assert "disk" in data
    assert "free_gb" in data["disk"]
    assert data["disk"]["free_gb"] > 0


# ── http_fetch ───────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status=200, content=b"hello", headers=None, url="http://x/"):
        self.status_code = status
        self.content = content
        self.headers = headers or {"content-type": "text/plain"}
        self.url = url


class _FakeAsyncClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url):
        return self._resp


@pytest.mark.asyncio
async def test_http_fetch_rejects_file_scheme(mcp_server):
    result = await mcp_server.http_fetch(
        mcp_server._HttpFetchInput(url="ftp://x.example/y")
    )
    # Pydantic уже отсекает большую часть, но наш extra check возвращает invalid_scheme
    # Если pydantic разрешил — наш guard сработает.
    data = json.loads(result)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_http_fetch_success(mcp_server):
    fake = _FakeResponse(status=200, content=b"hello world")
    with patch.object(httpx, "AsyncClient", return_value=_FakeAsyncClient(fake)):
        result = await mcp_server.http_fetch(
            mcp_server._HttpFetchInput(url="https://example.com")
        )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["status"] == 200
    assert "hello" in data["body"]
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_http_fetch_truncates_large_body(mcp_server):
    big = b"x" * (200 * 1024)
    fake = _FakeResponse(content=big)
    with patch.object(httpx, "AsyncClient", return_value=_FakeAsyncClient(fake)):
        result = await mcp_server.http_fetch(
            mcp_server._HttpFetchInput(url="https://example.com")
        )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["truncated"] is True
    assert len(data["body"]) <= 100 * 1024 + 10


@pytest.mark.asyncio
async def test_http_fetch_timeout(mcp_server):
    class _Timeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            raise httpx.TimeoutException("nope")

    with patch.object(httpx, "AsyncClient", return_value=_Timeout()):
        result = await mcp_server.http_fetch(
            mcp_server._HttpFetchInput(url="https://example.com", timeout=1.0)
        )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "timeout"


# ── time_now / time_parse ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_time_now_default_tz(mcp_server):
    result = await mcp_server.time_now(mcp_server._TimeNowInput())
    data = json.loads(result)
    assert data["ok"] is True
    assert data["timezone"] == "Europe/Madrid"
    assert "iso" in data and "unix" in data


@pytest.mark.asyncio
async def test_time_now_unknown_tz(mcp_server):
    result = await mcp_server.time_now(
        mcp_server._TimeNowInput(timezone="Foo/Bar_Nonexistent")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert "unknown_timezone" in data["error"]


@pytest.mark.asyncio
async def test_time_parse_tomorrow(mcp_server):
    result = await mcp_server.time_parse(
        mcp_server._TimeParseInput(text="tomorrow 15:00")
    )
    data = json.loads(result)
    assert data["ok"] is True
    assert "iso" in data
    assert data["unix"] > 0


@pytest.mark.asyncio
async def test_time_parse_garbage(mcp_server):
    result = await mcp_server.time_parse(
        mcp_server._TimeParseInput(text="qwertyuiop_nonsense_xyz_9999")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "parse_failed"
