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
    """Fake httpx.AsyncClient — отдаёт последовательность ответов (на каждый request).

    Принимает либо один _FakeResponse, либо список для эмуляции redirect chain.
    """

    def __init__(self, resp):
        self._responses = resp if isinstance(resp, list) else [resp]
        self._i = 0

    def __call__(self, *args, **kwargs):
        # httpx.AsyncClient(**kwargs) — возвращаем себя
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url):
        resp = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return resp


def _public_client(resp):
    """Оборачивает fake client + патчит _host_is_private → False (обходим DNS)."""
    return _FakeAsyncClient(resp)


@pytest.mark.asyncio
async def test_http_fetch_rejects_file_scheme(mcp_server):
    result = await mcp_server.http_fetch(mcp_server._HttpFetchInput(url="ftp://x.example/y"))
    # Pydantic уже отсекает большую часть, но наш extra check возвращает invalid_scheme
    # Если pydantic разрешил — наш guard сработает.
    data = json.loads(result)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_http_fetch_success(mcp_server):
    fake = _FakeResponse(status=200, content=b"hello world")
    with (
        patch.object(mcp_server, "_host_is_private", return_value=False),
        patch.object(httpx, "AsyncClient", return_value=_FakeAsyncClient(fake)),
    ):
        result = await mcp_server.http_fetch(mcp_server._HttpFetchInput(url="https://example.com"))
    data = json.loads(result)
    assert data["ok"] is True
    assert data["status"] == 200
    assert "hello" in data["body"]
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_http_fetch_truncates_large_body(mcp_server):
    big = b"x" * (200 * 1024)
    fake = _FakeResponse(content=big)
    with (
        patch.object(mcp_server, "_host_is_private", return_value=False),
        patch.object(httpx, "AsyncClient", return_value=_FakeAsyncClient(fake)),
    ):
        result = await mcp_server.http_fetch(mcp_server._HttpFetchInput(url="https://example.com"))
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

    with (
        patch.object(mcp_server, "_host_is_private", return_value=False),
        patch.object(httpx, "AsyncClient", return_value=_Timeout()),
    ):
        result = await mcp_server.http_fetch(
            mcp_server._HttpFetchInput(url="https://example.com", timeout=1.0)
        )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "timeout"


# ── SSRF guard: private / RFC1918 / link-local / redirect-to-private ─────────


@pytest.mark.asyncio
async def test_http_fetch_blocks_localhost(mcp_server):
    result = await mcp_server.http_fetch(
        mcp_server._HttpFetchInput(url="http://127.0.0.1:8080/api/health")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "private_host_blocked"
    assert data["host"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_http_fetch_blocks_rfc1918(mcp_server):
    result = await mcp_server.http_fetch(mcp_server._HttpFetchInput(url="http://192.168.1.1/"))
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "private_host_blocked"
    assert data["host"] == "192.168.1.1"


@pytest.mark.asyncio
async def test_http_fetch_blocks_link_local(mcp_server):
    # Cloud metadata endpoint — classic SSRF target
    result = await mcp_server.http_fetch(
        mcp_server._HttpFetchInput(url="http://169.254.169.254/latest/meta-data/")
    )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "private_host_blocked"


@pytest.mark.asyncio
async def test_http_fetch_blocks_ipv6_loopback(mcp_server):
    result = await mcp_server.http_fetch(mcp_server._HttpFetchInput(url="http://[::1]/"))
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "private_host_blocked"


@pytest.mark.asyncio
async def test_http_fetch_blocks_redirect_to_private(mcp_server):
    """302 → 127.0.0.1 должен быть заблокирован на втором hop'е."""
    redirect = _FakeResponse(
        status=302,
        content=b"",
        headers={"location": "http://127.0.0.1:8080/api/ops/cost-report"},
        url="https://evil.example/",
    )
    # Первый hop — публичный хост (mock); второй — 127.0.0.1 (реальный guard сработает)
    host_check_calls: list[str] = []
    real_check = mcp_server._host_is_private

    def _selective(host: str) -> bool:
        host_check_calls.append(host)
        if host == "evil.example":
            return False
        return real_check(host)

    with (
        patch.object(mcp_server, "_host_is_private", side_effect=_selective),
        patch.object(httpx, "AsyncClient", return_value=_FakeAsyncClient([redirect])),
    ):
        result = await mcp_server.http_fetch(
            mcp_server._HttpFetchInput(url="https://evil.example/")
        )
    data = json.loads(result)
    assert data["ok"] is False
    assert data["error"] == "private_host_blocked"
    assert data["host"] == "127.0.0.1"
    # Убеждаемся, что guard был вызван и на редиректе
    assert "127.0.0.1" in host_check_calls


@pytest.mark.asyncio
async def test_http_fetch_binary_response(mcp_server):
    """Content-type image/png → summary без decode."""
    fake = _FakeResponse(
        status=200,
        content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 500,
        headers={"content-type": "image/png"},
    )
    with (
        patch.object(mcp_server, "_host_is_private", return_value=False),
        patch.object(httpx, "AsyncClient", return_value=_FakeAsyncClient(fake)),
    ):
        result = await mcp_server.http_fetch(
            mcp_server._HttpFetchInput(url="https://example.com/logo.png")
        )
    data = json.loads(result)
    assert data["ok"] is True
    assert data["content_type"] == "image/png"
    assert data["body"].startswith("<binary:")
    assert "bytes>" in data["body"]
    assert data["truncated"] is True


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
    result = await mcp_server.time_now(mcp_server._TimeNowInput(timezone="Foo/Bar_Nonexistent"))
    data = json.loads(result)
    assert data["ok"] is False
    assert "unknown_timezone" in data["error"]


@pytest.mark.asyncio
async def test_time_parse_tomorrow(mcp_server):
    result = await mcp_server.time_parse(mcp_server._TimeParseInput(text="tomorrow 15:00"))
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
