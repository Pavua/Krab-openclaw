# -*- coding: utf-8 -*-
"""Unit tests для интеграционных клиентов Voice Gateway и Krab Ear."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.integrations.browser_bridge import BrowserBridge
from src.integrations.krab_ear_client import KrabEarClient
from src.integrations.voice_gateway_client import VoiceGatewayClient


@pytest.mark.asyncio
async def test_voice_gateway_health_ok_from_ok_flag(monkeypatch) -> None:
    """Voice Gateway считается healthy, если payload вернул ok=true."""
    client = VoiceGatewayClient(base_url="http://127.0.0.1:8090")

    async def _fake_fetch() -> tuple[int, dict]:
        return 200, {"ok": True, "service": "krab-voice-gateway"}

    monkeypatch.setattr(client, "_fetch_health_payload", _fake_fetch)
    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_voice_gateway_health_fail_on_http_error(monkeypatch) -> None:
    """При не-200 ответе health_check возвращает False."""
    client = VoiceGatewayClient(base_url="http://127.0.0.1:8090")

    async def _fake_fetch() -> tuple[int, dict]:
        return 503, {"ok": False}

    monkeypatch.setattr(client, "_fetch_health_payload", _fake_fetch)
    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_voice_gateway_capabilities_report_returns_detail(monkeypatch) -> None:
    """Capabilities report должен ходить в contract-first endpoint и возвращать detail."""
    client = VoiceGatewayClient(base_url="http://127.0.0.1:8090")

    async def _fake_request(method: str, path: str, **kwargs) -> tuple[int | None, dict, str]:
        del kwargs
        assert method == "GET"
        assert path == "/v1/capabilities"
        return 200, {"service": "krab-voice-gateway", "contract_version": "voice-gateway.v1"}, ""

    monkeypatch.setattr(client, "_request_json", _fake_request)
    payload = await client.capabilities_report()

    assert payload["ok"] is True
    assert payload["detail"]["contract_version"] == "voice-gateway.v1"


@pytest.mark.asyncio
async def test_voice_gateway_list_sessions_normalizes_items(monkeypatch) -> None:
    """list_sessions должен возвращать нормализованный список items."""
    client = VoiceGatewayClient(base_url="http://127.0.0.1:8090")

    async def _fake_request(method: str, path: str, **kwargs) -> tuple[int | None, dict, str]:
        assert method == "GET"
        assert path == "/v1/sessions"
        assert kwargs["params"]["status"] == "running"
        return 200, {"count": 1, "items": [{"id": "sess-1", "status": "running"}]}, ""

    monkeypatch.setattr(client, "_request_json", _fake_request)
    payload = await client.list_sessions(status="running")

    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "sess-1"


@pytest.mark.asyncio
async def test_krab_ear_health_ok_from_status(monkeypatch) -> None:
    """Krab Ear считается healthy, если payload вернул status=ok."""
    client = KrabEarClient(base_url="http://127.0.0.1:5005")

    async def _fake_fetch() -> tuple[int, dict]:
        return 200, {"status": "ok", "service": "krab-ear"}

    monkeypatch.setattr(client, "_fetch_health_payload", _fake_fetch)
    monkeypatch.setattr(client, "_ping_ipc_health", lambda: _fake_ipc_down())
    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_krab_ear_health_report_contains_source(monkeypatch) -> None:
    """health_report должен содержать source и корректный статус."""
    client = KrabEarClient(base_url="http://127.0.0.1:5005")

    async def _fake_fetch() -> tuple[int, dict]:
        return 500, {"status": "error"}

    monkeypatch.setattr(client, "_fetch_health_payload", _fake_fetch)
    monkeypatch.setattr(client, "_ping_ipc_health", lambda: _fake_ipc_down())
    report = await client.health_report()

    assert report["ok"] is False
    assert report["status"] == "http_500"
    assert report["source"].endswith("/health")


async def _fake_ipc_down() -> tuple[bool, str]:
    return False, "socket_missing"


@pytest.mark.asyncio
async def test_krab_ear_health_prefers_ipc(monkeypatch) -> None:
    """Если IPC ping успешен, HTTP fallback не должен быть нужен."""
    client = KrabEarClient(base_url="")

    async def _fake_ipc_ok() -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr(client, "_ping_ipc_health", _fake_ipc_ok)
    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_browser_bridge_reads_ws_endpoint_from_devtools_active_port(monkeypatch, tmp_path: Path) -> None:
    """Browser bridge должен уметь собирать wsEndpoint из `DevToolsActivePort`."""
    bridge = BrowserBridge()
    active_port = tmp_path / "DevToolsActivePort"
    active_port.write_text("9222\n/devtools/browser/test-browser-id\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "_devtools_active_port_candidates", lambda: [active_port])

    assert bridge._read_devtools_ws_endpoint() == "ws://127.0.0.1:9222/devtools/browser/test-browser-id"


@pytest.mark.asyncio
async def test_browser_bridge_falls_back_to_ws_endpoint_when_http_cdp_returns_404(monkeypatch) -> None:
    """При 404 на `json/version` bridge должен пробовать wsEndpoint из профиля Chrome."""
    bridge = BrowserBridge()
    bridge._playwright = None
    seen_endpoints: list[str] = []
    expected_browser = object()

    class _FakeChromium:
        async def connect_over_cdp(self, endpoint: str):
            seen_endpoints.append(endpoint)
            if endpoint == bridge.CDP_URL:
                raise RuntimeError("Unexpected status 404 when connecting to http://127.0.0.1:9222/json/version")
            if endpoint == "ws://127.0.0.1:9222/devtools/browser/test-browser-id":
                return expected_browser
            raise AssertionError(f"Неожиданный endpoint: {endpoint}")

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

    class _FakeAsyncPlaywrightFactory:
        async def start(self):
            return _FakePlaywright()

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _FakeAsyncPlaywrightFactory())
    monkeypatch.setattr(bridge, "_read_devtools_ws_endpoint", lambda: "ws://127.0.0.1:9222/devtools/browser/test-browser-id")

    browser = await bridge._get_browser()

    assert browser is expected_browser
    assert seen_endpoints == [
        "http://127.0.0.1:9222",
        "ws://127.0.0.1:9222/devtools/browser/test-browser-id",
    ]


@pytest.mark.asyncio
async def test_browser_bridge_candidate_paths_include_operator_home(monkeypatch) -> None:
    """Bridge должен учитывать `KRAB_OPERATOR_HOME`, если runtime запущен под другой shell-home."""
    bridge = BrowserBridge()
    monkeypatch.setenv("KRAB_OPERATOR_HOME", "/Users/pablito")
    monkeypatch.setenv("HOME", "/Users/USER2")

    candidates = [str(path) for path in bridge._devtools_active_port_candidates()]

    assert candidates[0].startswith("/Users/pablito/")
    assert any(path.startswith("/Users/USER2/") for path in candidates)


@pytest.mark.asyncio
async def test_browser_bridge_connect_uses_timeout(monkeypatch) -> None:
    """Connect loop не должен виснуть бесконечно на одном endpoint."""
    bridge = BrowserBridge()
    bridge._playwright = None
    bridge._connect_timeout_sec = 0.01

    class _FakeChromium:
        async def connect_over_cdp(self, endpoint: str):
            await asyncio.sleep(1)
            raise AssertionError(f"Не должен дождаться реального завершения: {endpoint}")

    class _FakePlaywright:
        def __init__(self) -> None:
            self.chromium = _FakeChromium()

    class _Factory:
        async def start(self):
            return _FakePlaywright()

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _Factory())
    monkeypatch.setattr(bridge, "_read_devtools_ws_endpoint", lambda: None)

    with pytest.raises(Exception):
        await bridge._get_browser()


@pytest.mark.asyncio
async def test_browser_bridge_is_attached_falls_back_to_raw_cdp(monkeypatch) -> None:
    """Если Playwright attach ломается, bridge должен подтвердить attach через raw CDP."""
    bridge = BrowserBridge()

    async def _boom():
        raise RuntimeError("playwright_cdp_failed")

    async def _raw_tabs(ws_endpoint: str) -> list[dict]:
        assert ws_endpoint == "ws://127.0.0.1:9222/devtools/browser/test-browser-id"
        return []

    monkeypatch.setattr(bridge, "_get_browser", _boom)
    monkeypatch.setattr(bridge, "_read_devtools_ws_endpoint", lambda: "ws://127.0.0.1:9222/devtools/browser/test-browser-id")
    monkeypatch.setattr(bridge, "_list_tabs_via_raw_cdp", _raw_tabs)

    assert await bridge.is_attached() is True
    assert bridge._prefer_raw_cdp is True


@pytest.mark.asyncio
async def test_browser_bridge_action_probe_falls_back_to_raw_cdp(monkeypatch) -> None:
    """Action probe должен уметь переключаться на raw websocket CDP fallback."""
    bridge = BrowserBridge()

    async def _boom():
        raise RuntimeError("playwright_cdp_failed")

    async def _raw_probe(ws_endpoint: str, url: str) -> dict[str, str | bool]:
        assert ws_endpoint == "ws://127.0.0.1:9222/devtools/browser/test-browser-id"
        assert url == "https://example.com"
        return {
            "ok": True,
            "state": "action_probe_ok",
            "final_url": "https://example.com/",
            "title": "Example Domain",
        }

    monkeypatch.setattr(bridge, "_get_browser", _boom)
    monkeypatch.setattr(bridge, "_read_devtools_ws_endpoint", lambda: "ws://127.0.0.1:9222/devtools/browser/test-browser-id")
    monkeypatch.setattr(bridge, "_action_probe_via_raw_cdp", _raw_probe)

    result = await bridge.action_probe("https://example.com")

    assert result["ok"] is True
    assert result["state"] == "action_probe_ok"
    assert result["final_url"] == "https://example.com/"
    assert bridge._prefer_raw_cdp is True
