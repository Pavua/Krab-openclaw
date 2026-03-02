# -*- coding: utf-8 -*-
"""Unit tests для интеграционных клиентов Voice Gateway и Krab Ear."""

from __future__ import annotations

import pytest

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
