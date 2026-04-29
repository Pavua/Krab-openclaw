"""Тесты для /api/dashboard/summary и src.core.dashboard_summary."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.core.dashboard_summary import (
    collect_alerts_block,
    collect_dashboard_summary,
    collect_services_status,
)

# ---------------------------------------------------------------------------
# Unit-тесты на collect_dashboard_summary — полная инъекция пробов.
# ---------------------------------------------------------------------------


def _fake_services() -> tuple[dict[str, str], int | None]:
    return (
        {
            "krab": "running",
            "openclaw_gateway": "running",
            "mcp_yung_nagato": "running",
            "mcp_p0lrd": "down",
            "mcp_hammerspoon": "running",
            "inbox_watcher": "running",
            "lm_studio": "down",
        },
        12345,
    )


def _fake_archive() -> dict[str, Any]:
    return {"size_mb": 42.0, "message_count": 42000, "encoded_chunks": 9000}


def _fake_memory() -> dict[str, Any]:
    return {"total_chunks": 10000, "encoded_chunks": 9000, "coverage_pct": 90.0}


def _fake_activity() -> dict[str, Any]:
    return {"commands_today": 150, "llm_calls_today": None, "errors_today": None}


class _FakeRouterWithAlerts:
    def get_ops_alerts(self) -> list[dict[str, Any]]:
        return [
            {"severity": "warning", "code": "COST_BUDGET", "msg": "70% бюджета"},
            {"severity": "critical", "code": "ROUTE_FAIL", "message": "provider down"},
        ]


def test_summary_response_shape_full() -> None:
    result = collect_dashboard_summary(
        boot_ts=time.time() - 100,
        router=_FakeRouterWithAlerts(),
        services_probe=_fake_services,
        archive_probe=_fake_archive,
        memory_probe=_fake_memory,
        activity_probe=_fake_activity,
    )
    assert result["ok"] is True
    # Top-level keys
    for key in [
        "uptime",
        "version",
        "krab_pid",
        "services",
        "archive",
        "memory_layer",
        "activity",
        "alerts",
        "_meta",
    ]:
        assert key in result, f"missing key: {key}"

    assert result["uptime"]["sec"] >= 100
    assert result["krab_pid"] == 12345
    assert result["services"]["krab"] == "running"
    assert result["services"]["mcp_p0lrd"] == "down"
    assert result["archive"]["size_mb"] == 42.0
    assert result["memory_layer"]["coverage_pct"] == 90.0
    assert result["activity"]["commands_today"] == 150
    assert len(result["alerts"]) == 2
    # Алерты нормализованы: оба имеют msg.
    assert result["alerts"][0]["msg"] == "70% бюджета"
    assert result["alerts"][1]["msg"] == "provider down"


def test_summary_archive_missing_returns_null_not_500() -> None:
    """archive.db не найдена → archive=null, endpoint живой."""

    def archive_missing() -> None:
        return None

    result = collect_dashboard_summary(
        boot_ts=time.time(),
        router=None,
        services_probe=_fake_services,
        archive_probe=archive_missing,
        memory_probe=archive_missing,
        activity_probe=_fake_activity,
    )
    assert result["ok"] is True
    assert result["archive"] is None
    assert result["memory_layer"] is None


def test_summary_services_probe_failure_degrades_gracefully() -> None:
    """Сбой services_probe → services={}, krab_pid=None, без 500."""

    def broken_services() -> tuple[dict[str, str], int | None]:
        raise RuntimeError("launchctl unavailable")

    result = collect_dashboard_summary(
        boot_ts=time.time(),
        router=None,
        services_probe=broken_services,
        archive_probe=_fake_archive,
        memory_probe=_fake_memory,
        activity_probe=_fake_activity,
    )
    assert result["ok"] is True
    assert result["services"] == {}
    assert result["krab_pid"] is None


def test_summary_alerts_empty_when_no_router() -> None:
    result = collect_dashboard_summary(
        boot_ts=time.time(),
        router=None,
        services_probe=_fake_services,
        archive_probe=_fake_archive,
        memory_probe=_fake_memory,
        activity_probe=_fake_activity,
    )
    assert result["alerts"] == []


def test_summary_meta_block_present() -> None:
    result = collect_dashboard_summary(
        boot_ts=time.time(),
        router=None,
        services_probe=_fake_services,
        archive_probe=_fake_archive,
        memory_probe=_fake_memory,
        activity_probe=_fake_activity,
    )
    meta = result["_meta"]
    assert "updated_at" in meta
    assert "sources_queried" in meta
    assert "elapsed_ms" in meta
    assert isinstance(meta["elapsed_ms"], float)
    assert meta["elapsed_ms"] >= 0.0
    assert "services" in meta["sources_queried"]
    assert "alerts" in meta["sources_queried"]


def test_summary_elapsed_ms_under_100ms_typical() -> None:
    """Типичный вызов с in-memory пробами должен укладываться в 100мс."""

    result = collect_dashboard_summary(
        boot_ts=time.time(),
        router=None,
        services_probe=_fake_services,
        archive_probe=_fake_archive,
        memory_probe=_fake_memory,
        activity_probe=_fake_activity,
    )
    assert result["_meta"]["elapsed_ms"] < 100.0


# ---------------------------------------------------------------------------
# collect_services_status — проверка fallback при ошибочных пробах.
# ---------------------------------------------------------------------------


def test_services_status_all_probes_injected() -> None:
    def fake_launchctl(label: str) -> str:
        return "running" if "yung" in label else "down"

    def fake_krab() -> tuple[str, int | None]:
        return ("running", 99)

    def fake_lm() -> str:
        return "down"

    services, pid = collect_services_status(
        launchctl_check=fake_launchctl,
        krab_probe=fake_krab,
        lm_studio_probe=fake_lm,
    )
    assert pid == 99
    assert services["krab"] == "running"
    assert services["mcp_yung_nagato"] == "running"
    assert services["mcp_p0lrd"] == "down"
    assert services["lm_studio"] == "down"


def test_services_status_launchctl_exception_yields_unknown() -> None:
    def broken(label: str) -> str:
        raise RuntimeError("boom")

    services, pid = collect_services_status(
        launchctl_check=broken,
        krab_probe=lambda: ("down", None),
        lm_studio_probe=lambda: "down",
    )
    assert pid is None
    assert services["openclaw_gateway"] == "unknown"


# ---------------------------------------------------------------------------
# collect_alerts_block — нормализация.
# ---------------------------------------------------------------------------


def test_alerts_block_skips_non_dict_entries() -> None:
    class _R:
        def get_ops_alerts(self) -> list[Any]:
            return [{"severity": "warning", "code": "X", "msg": "m"}, "not-a-dict", 123]

    alerts = collect_alerts_block(_R())
    assert len(alerts) == 1
    assert alerts[0]["code"] == "X"


def test_alerts_block_router_without_method() -> None:
    class _Empty:
        pass

    assert collect_alerts_block(_Empty()) == []


def test_alerts_block_exception_returns_empty() -> None:
    class _Broken:
        def get_ops_alerts(self) -> list[Any]:
            raise RuntimeError("db unavailable")

    assert collect_alerts_block(_Broken()) == []


# ---------------------------------------------------------------------------
# Async-аггрегатор: не блокирует event loop, параллелит subprocess-пробы.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_summary_response_shape_full() -> None:
    from src.core.dashboard_summary import collect_dashboard_summary_async

    async def fake_services_async() -> tuple[dict[str, str], int | None]:
        return (
            {"krab": "running", "openclaw_gateway": "running", "lm_studio": "down"},
            777,
        )

    result = await collect_dashboard_summary_async(
        boot_ts=time.time() - 50,
        router=_FakeRouterWithAlerts(),
        services_probe=fake_services_async,
        archive_probe=_fake_archive,
        memory_probe=_fake_memory,
        activity_probe=_fake_activity,
    )
    assert result["ok"] is True
    assert result["krab_pid"] == 777
    assert result["services"]["krab"] == "running"
    assert len(result["alerts"]) == 2


@pytest.mark.asyncio
async def test_async_summary_parallel_services_probes() -> None:
    """Subprocess-пробы должны выполняться параллельно, не сериально.

    Имитируем 6 проб по 50ms каждая. Сериальное выполнение = 300ms,
    параллельное = ~50ms. Порог 200ms ловит регрессию без ложных срабатываний.
    """

    import asyncio as _asyncio

    from src.core.dashboard_summary import (
        _SERVICE_LABELS,
        collect_dashboard_summary_async,
    )

    call_count = {"n": 0}

    async def slow_launchctl(label: str, *, timeout: float = 1.5) -> str:
        call_count["n"] += 1
        await _asyncio.sleep(0.05)
        return "running"

    async def slow_krab() -> tuple[str, int | None]:
        await _asyncio.sleep(0.05)
        return ("running", 1)

    async def slow_lm() -> str:
        await _asyncio.sleep(0.05)
        return "down"

    async def parallel_services() -> tuple[dict[str, str], int | None]:
        krab_t = _asyncio.create_task(slow_krab())
        launchctl_tasks = {
            name: _asyncio.create_task(slow_launchctl(label))
            for name, label in _SERVICE_LABELS.items()
        }
        lm_t = _asyncio.create_task(slow_lm())
        services: dict[str, str] = {}
        krab_status, krab_pid = await krab_t
        services["krab"] = krab_status
        for name, t in launchctl_tasks.items():
            services[name] = await t
        services["lm_studio"] = await lm_t
        return services, krab_pid

    started = time.perf_counter()
    result = await collect_dashboard_summary_async(
        boot_ts=time.time(),
        router=None,
        services_probe=parallel_services,
        archive_probe=lambda: None,
        memory_probe=lambda: None,
        activity_probe=lambda: None,
    )
    elapsed = time.perf_counter() - started
    assert result["ok"] is True
    # Параллельное: ~50ms. Если сериально — было бы ≥ 350ms (7 проб x 50ms).
    assert elapsed < 0.2, f"услуги не параллелятся: elapsed={elapsed:.3f}s"


# ---------------------------------------------------------------------------
# E2E через FastAPI TestClient.
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    async def health_check(self) -> bool:
        return True


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}

    def get_ops_alerts(self) -> list[dict[str, Any]]:
        return [{"severity": "warning", "code": "TEST", "msg": "test"}]


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {}

    def get_translator_session_state(self) -> dict:
        return {}

    def get_voice_runtime_profile(self) -> dict:
        return {}

    def get_runtime_state(self) -> dict:
        return {}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True}

    async def capabilities_report(self) -> dict:
        return {"ok": True}


@pytest.fixture
def client_with_dashboard() -> TestClient:
    from src.modules.web_app import WebApp

    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18093, host="127.0.0.1")
    return TestClient(app.app)


def test_api_dashboard_summary_returns_200_with_shape(
    client_with_dashboard: TestClient,
) -> None:
    response = client_with_dashboard.get("/api/dashboard/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    for key in [
        "uptime",
        "version",
        "krab_pid",
        "services",
        "archive",
        "memory_layer",
        "activity",
        "alerts",
        "_meta",
    ]:
        assert key in data
    # Alerts из _DummyRouter должны попасть в ответ.
    assert any(a["code"] == "TEST" for a in data["alerts"])
    # Meta содержит elapsed_ms.
    assert "elapsed_ms" in data["_meta"]
