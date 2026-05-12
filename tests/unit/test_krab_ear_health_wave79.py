# -*- coding: utf-8 -*-
"""Wave 79: тесты KrabEarHealthProbe + Prometheus integration."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import httpx
import pytest

from src.core import krab_ear_health_probe as ke_module
from src.core.krab_ear_health_probe import (
    KrabEarHealthProbe,
    get_snapshot,
    reset_snapshot_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_snapshot():
    reset_snapshot_for_tests()
    yield
    reset_snapshot_for_tests()


def _make_probe(handler, monkeypatch=None):
    """Probe с MockTransport, который вызывает handler(request) -> Response.

    Wave 180: устанавливает KRAB_EAR_BACKEND_URL чтобы probe пошёл по HTTP fallback
    (а не IPC). Без explicit env probe считает HTTP unconfigured и не вызывает
    handler — это новое корректное поведение для KE = IPC-only.
    Также форсит installed=True и socket_path в несуществующее место для IPC fail.
    """
    if monkeypatch is not None:
        monkeypatch.setenv("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:5005")
    else:
        os.environ["KRAB_EAR_BACKEND_URL"] = "http://127.0.0.1:5005"
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=1.0)

    clock = [1_000_000.0]
    probe = KrabEarHealthProbe(
        backend_url="http://127.0.0.1:5005",
        socket_path="/tmp/_wave180_nonexistent_socket_for_tests",
        interval_sec=60,
        http_client_factory=factory,
        now_fn=lambda: clock[0],
    )
    # Wave 180: форсим installed=True (тесты не зависят от наличия KE на диске).
    probe._installed = True
    # Wave 180: HTTP fallback вызывается только если URL explicitly set.
    probe._http_explicit = True
    return probe, clock


def test_probe_success_resets_consecutive_failures():
    """200 OK → last_probe_ok=True, consecutive_failures=0, last_success_ts обновлён."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    probe, clock = _make_probe(handler)
    # Сначала имитируем накопленный streak.
    ke_module._SNAPSHOT["consecutive_failures"] = 3
    ok = asyncio.run(probe.probe_once())
    assert ok is True
    snap = get_snapshot()
    assert snap["last_probe_ok"] is True
    assert snap["consecutive_failures"] == 0
    assert snap["last_success_ts"] == 1_000_000.0


def test_probe_5xx_classified_as_5xx_reason():
    """HTTP 503 → reason=5xx, total_failures=1, consecutive_failures=1."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="bad gateway")

    probe, _ = _make_probe(handler)
    ok = asyncio.run(probe.probe_once())
    assert ok is False
    snap = get_snapshot()
    assert snap["last_probe_ok"] is False
    assert snap["consecutive_failures"] == 1
    assert snap["total_failures"] == 1
    assert snap["failures_by_reason"].get("5xx") == 1


def test_probe_timeout_classified_as_timeout():
    """httpx.TimeoutException → reason=timeout."""

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    probe, _ = _make_probe(handler)
    ok = asyncio.run(probe.probe_once())
    assert ok is False
    snap = get_snapshot()
    assert snap["failures_by_reason"].get("timeout") == 1
    assert snap["consecutive_failures"] == 1


def test_probe_connection_error_classified():
    """httpx.ConnectError → reason=connection_error."""

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conn refused")

    probe, _ = _make_probe(handler)
    asyncio.run(probe.probe_once())
    snap = get_snapshot()
    assert snap["failures_by_reason"].get("connection_error") == 1


def test_consecutive_failures_accumulate_then_recover():
    """3 fails → consecutive=3; затем success → consecutive=0."""
    state = {"fail": True}

    def handler(_req: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("nope")
        return httpx.Response(200)

    probe, _ = _make_probe(handler)
    for _ in range(3):
        asyncio.run(probe.probe_once())
    snap = get_snapshot()
    assert snap["consecutive_failures"] == 3
    assert snap["total_failures"] == 3

    state["fail"] = False
    asyncio.run(probe.probe_once())
    snap = get_snapshot()
    assert snap["consecutive_failures"] == 0
    assert snap["total_failures"] == 3  # total не сбрасывается
    assert snap["last_probe_ok"] is True


def test_health_url_constructed_from_env(monkeypatch):
    """KRAB_EAR_BACKEND_URL подхватывается, trailing slash убирается."""
    monkeypatch.setenv("KRAB_EAR_BACKEND_URL", "http://10.0.0.1:9999/")
    probe = KrabEarHealthProbe()
    assert probe.health_url == "http://10.0.0.1:9999/health"


def test_prometheus_metrics_export_with_failures():
    """collect_metrics() экспортит ago_seconds, consecutive, failures_total{reason}."""
    from src.core.prometheus_metrics import collect_metrics

    # Симулируем: 1 success + 2 failures (timeout, 5xx).
    ke_module._SNAPSHOT["last_success_ts"] = 1.0  # любое значение >0 → ago экспортится не -1
    ke_module._SNAPSHOT["last_probe_ts"] = 2.0
    ke_module._SNAPSHOT["last_probe_ok"] = False
    ke_module._SNAPSHOT["consecutive_failures"] = 2
    ke_module._SNAPSHOT["total_failures"] = 2
    ke_module._SNAPSHOT["failures_by_reason"] = {"timeout": 1, "5xx": 1}

    text = collect_metrics()
    assert "krab_ear_probe_last_ago_seconds" in text
    assert "krab_ear_consecutive_failures 2" in text
    assert 'krab_ear_probe_failures_total{reason="timeout"} 1' in text
    assert 'krab_ear_probe_failures_total{reason="5xx"} 1' in text


def test_prometheus_metrics_cold_boot_placeholders():
    """Когда probe ни разу не отрабатывал — ago_seconds=-1 и placeholder reason=none."""
    from src.core.prometheus_metrics import collect_metrics

    # snapshot уже обнулён auto-fixture.
    text = collect_metrics()
    assert "krab_ear_probe_last_ago_seconds -1" in text
    assert "krab_ear_consecutive_failures 0" in text
    assert 'krab_ear_probe_failures_total{reason="none"} 0' in text
