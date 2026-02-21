# -*- coding: utf-8 -*-
"""Тесты EcosystemHealthService: деградация и сводный статус."""

from __future__ import annotations

import pytest

from src.core.ecosystem_health import EcosystemHealthService


class _Router:
    def __init__(self, local_ok: bool):
        self._local_ok = local_ok

    async def check_local_health(self):
        return self._local_ok


class _Client:
    def __init__(self, ok: bool):
        self._ok = ok

    async def health_check(self):
        return self._ok


@pytest.mark.asyncio
async def test_ecosystem_health_normal_cloud() -> None:
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=True),
        voice_gateway_client=_Client(ok=True),
        krab_ear_client=_Client(ok=True),
    )
    payload = await service.collect()
    assert payload["degradation"] == "normal"
    assert payload["chain"]["active_ai_channel"] == "cloud"
    assert payload["status"] == "ok"
    assert payload["checks"]["openclaw"]["ok"] is True


@pytest.mark.asyncio
async def test_ecosystem_health_fallback_when_cloud_offline() -> None:
    service = EcosystemHealthService(
        router=_Router(local_ok=True),
        openclaw_client=_Client(ok=False),
        voice_gateway_client=_Client(ok=False),
        krab_ear_client=_Client(ok=False),
    )
    payload = await service.collect()
    assert payload["degradation"] == "degraded_to_local_fallback"
    assert payload["chain"]["active_ai_channel"] == "local_fallback"
    assert payload["status"] == "degraded"
    assert payload["risk_level"] in {"medium", "high"}


@pytest.mark.asyncio
async def test_ecosystem_health_critical_when_all_ai_offline() -> None:
    service = EcosystemHealthService(
        router=_Router(local_ok=False),
        openclaw_client=_Client(ok=False),
        voice_gateway_client=_Client(ok=False),
        krab_ear_client=_Client(ok=False),
    )
    payload = await service.collect()
    assert payload["degradation"] == "critical_no_ai_backend"
    assert payload["chain"]["active_ai_channel"] == "none"
    assert payload["status"] == "critical"
    assert payload["risk_level"] == "high"
