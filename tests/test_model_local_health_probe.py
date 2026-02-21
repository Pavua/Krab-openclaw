# -*- coding: utf-8 -*-
"""
Тесты режима light health-check для локальных моделей.

Цель:
- убедиться, что в light-режиме роутер не сканирует `/api/v1/models`
  на каждом проходе и использует лёгкий серверный probe.
"""

import time
from pathlib import Path

import pytest

from src.core.model_manager import ModelRouter


def _router(tmp_path: Path) -> ModelRouter:
    return ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "LOCAL_HEALTH_PROBE_MODE": "light",
            "LOCAL_HEALTH_CACHE_TTL_SEC": "0",
            "LOCAL_HEALTH_FULL_SCAN_SECONDS": "3600",
        }
    )


@pytest.mark.asyncio
async def test_check_local_health_light_mode_uses_ping_without_model_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _router(tmp_path)
    counters = {"scan": 0, "ping": 0}

    async def fake_scan_models():
        counters["scan"] += 1
        return [{"id": "zai-org/glm-4.6v-flash", "loaded": True, "type": "llm"}]

    async def fake_light_ping(_base_root: str) -> bool:
        counters["ping"] += 1
        return True

    monkeypatch.setattr(router, "_scan_local_models", fake_scan_models)
    monkeypatch.setattr(router, "_light_ping_local_server", fake_light_ping)

    ok_first = await router.check_local_health(force=True)
    assert ok_first is True
    assert counters["scan"] == 1

    async def fail_if_scanned_again():
        raise AssertionError("В light-режиме повторный scan моделей не должен вызываться в пределах full-scan окна")

    monkeypatch.setattr(router, "_scan_local_models", fail_if_scanned_again)

    router._health_cache_ts = 0
    router._health_cache_ttl = 0
    router._health_full_scan_ts = time.time()

    ok_second = await router.check_local_health(force=False)
    assert ok_second is True
    assert counters["ping"] >= 1
