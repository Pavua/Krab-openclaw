# -*- coding: utf-8 -*-
"""
Unit tests для src/mcp_tor_server.py (Wave 44-Z).

Мокаем tor_bridge — тесты не требуют запущенного Tor daemon.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import src.mcp_tor_server as _mod

# --- tor_status --------------------------------------------------------------


def test_tor_status_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """tor_status проксирует health_check → available + exit_ip."""
    monkeypatch.setattr(
        _mod.tor_bridge,
        "health_check",
        AsyncMock(return_value={"ok": True, "ip": "185.220.101.4", "error": ""}),
    )
    result = _mod.tor_status()
    assert result == {"available": True, "exit_ip": "185.220.101.4", "error": None}


def test_tor_status_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если daemon не отвечает — available=False + текст ошибки."""
    monkeypatch.setattr(
        _mod.tor_bridge,
        "health_check",
        AsyncMock(return_value={"ok": False, "error": "tor_daemon_not_running"}),
    )
    result = _mod.tor_status()
    assert result["available"] is False
    assert result["error"] == "tor_daemon_not_running"
    assert result["exit_ip"] is None


# --- tor_check_exit_ip -------------------------------------------------------


def test_tor_check_exit_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """tor_check_exit_ip возвращает строку IP."""
    monkeypatch.setattr(
        _mod.tor_bridge,
        "get_tor_ip",
        AsyncMock(return_value="185.220.101.4"),
    )
    result = _mod.tor_check_exit_ip()
    assert result == {"ip": "185.220.101.4"}


def test_tor_check_exit_ip_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tor_ip может вернуть None если нет exit-circuit."""
    monkeypatch.setattr(
        _mod.tor_bridge,
        "get_tor_ip",
        AsyncMock(return_value=None),
    )
    assert _mod.tor_check_exit_ip() == {"ip": None}


# --- tor_fetch ---------------------------------------------------------------


def test_tor_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """tor_fetch успешно проксирует параметры в tor_bridge.tor_fetch."""
    captured: dict = {}

    async def _fake(url, *, socks_port, timeout, method, headers):  # noqa: ANN001
        captured["url"] = url
        captured["socks_port"] = socks_port
        captured["timeout"] = timeout
        captured["method"] = method
        captured["headers"] = headers
        return {"ok": True, "status": 200, "text": "hello", "url": url}

    monkeypatch.setattr(_mod.tor_bridge, "tor_fetch", _fake)

    result = _mod.tor_fetch(
        "http://example.onion/", method="POST", headers={"X-Test": "1"}, timeout=10.0
    )
    assert result["ok"] is True
    assert result["status"] == 200
    assert captured["method"] == "POST"
    assert captured["headers"] == {"X-Test": "1"}
    assert captured["timeout"] == 10.0
    assert captured["socks_port"] == _mod._SOCKS_PORT


def test_tor_fetch_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daemon down → ok=False + error прокинут."""

    async def _fake(*_args, **_kw):  # noqa: ANN002,ANN003
        return {"ok": False, "error": "tor_not_running"}

    monkeypatch.setattr(_mod.tor_bridge, "tor_fetch", _fake)
    result = _mod.tor_fetch("https://example.com/")
    assert result == {"ok": False, "error": "tor_not_running"}


# --- main entrypoint ---------------------------------------------------------


def test_main_stdio_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию main запускает FastMCP без аргументов (stdio)."""
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    called = {}

    def _run(transport=None, **_kw):  # noqa: ANN001
        called["transport"] = transport

    monkeypatch.setattr(_mod.mcp, "run", _run)
    _mod.main()
    assert called == {"transport": None}


def test_main_sse_uses_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_TRANSPORT=sse передаёт host/port в settings и зовёт run(transport='sse')."""
    monkeypatch.setenv("MCP_TRANSPORT", "sse")
    monkeypatch.setenv("MCP_PORT", "8014")

    called = {}

    def _run(transport=None, **_kw):  # noqa: ANN001
        called["transport"] = transport
        called["host"] = _mod.mcp.settings.host
        called["port"] = _mod.mcp.settings.port

    monkeypatch.setattr(_mod.mcp, "run", _run)
    _mod.main()
    assert called["transport"] == "sse"
    assert called["port"] == 8014
