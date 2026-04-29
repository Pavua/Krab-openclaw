# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.health_router`` — Phase 2 Wave X (Session 25).

Покрывают factory-pattern: build_health_router(ctx) работает stand-alone
с mocked RouterContext. Контракт endpoint'ов сохранён 1:1 с inline
definitions из web_app.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.health_router import build_health_router


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _FakeRouter:
    """Минимальный stub для RouterContext.deps['router']."""


class _FakeHealthSvc:
    """Stub EcosystemHealthService с детерминированным collect()."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._init_kwargs = kwargs

    async def collect(self) -> dict:
        return {
            "checks": {
                "openclaw": {"ok": True},
                "voice_gateway": {"ok": False},
                "krab_ear": {"ok": True},
            },
            "degradation": "minor",
            "risk_level": "low",
            "chain": ["openclaw", "krab_ear"],
            "session_12": {"foo": 1},
        }

    def _collect_session_12_stats(self) -> dict:
        return {"direct_session_12": True}


def _make_client(
    *,
    runtime_lite: dict[str, Any] | None = None,
    deps_overrides: dict[str, Any] | None = None,
    runtime_lite_raises: Exception | None = None,
) -> TestClient:
    snapshot = runtime_lite or {
        "lmstudio_model_state": "loaded",
        "telegram_session_state": "active",
        "telegram_userbot": {
            "startup_state": "ready",
            "client_connected": True,
            "startup_error_code": None,
        },
        "openclaw_auth_state": "ok",
        "last_runtime_route": {"channel": "cloud"},
        "scheduler_enabled": True,
        "inbox_summary": {"open": 2},
        "voice_gateway_configured": True,
        "status": "up",
    }

    async def _runtime_lite(*, force_refresh: bool = False) -> dict[str, Any]:
        if runtime_lite_raises:
            raise runtime_lite_raises
        return dict(snapshot)

    deps: dict[str, Any] = {
        "router": _FakeRouter(),
        "openclaw_client": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "health_service": _FakeHealthSvc(),
    }
    if deps_overrides:
        deps.update(deps_overrides)

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
        runtime_lite_provider=_runtime_lite,
    )

    app = FastAPI()
    app.include_router(build_health_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


def test_get_health_combines_runtime_lite_and_ecosystem() -> None:
    client = _make_client()
    # health_service не используется этим endpoint'ом (fresh EcosystemHealthService),
    # поэтому патчим импорт в health_router.
    with patch(
        "src.core.ecosystem_health.EcosystemHealthService",
        _FakeHealthSvc,
    ):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["openclaw"] is True
    assert body["checks"]["local_lm"] is True  # lm_state="loaded"
    assert body["checks"]["voice_gateway"] is False
    assert body["risk_level"] == "low"


def test_get_health_local_lm_down_when_lm_state_unknown() -> None:
    client = _make_client(runtime_lite={"lmstudio_model_state": "unknown"})
    with patch(
        "src.core.ecosystem_health.EcosystemHealthService",
        _FakeHealthSvc,
    ):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["checks"]["local_lm"] is False


# ---------------------------------------------------------------------------
# /api/health/lite
# ---------------------------------------------------------------------------


def test_get_health_lite_returns_runtime_fields() -> None:
    client = _make_client()
    with patch(
        "src.modules.web_app._resolve_memory_indexer_state",
        return_value="running",
    ), patch(
        "src.modules.web_app._resolve_memory_indexer_queue_size",
        return_value=42,
    ):
        resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "up"
    assert body["telegram_session_state"] == "active"
    assert body["telegram_userbot_state"] == "ready"
    assert body["telegram_userbot_client_connected"] is True
    assert body["lmstudio_model_state"] == "loaded"
    assert body["memory_indexer_state"] == "running"
    assert body["memory_indexer_queue_size"] == 42


# ---------------------------------------------------------------------------
# /api/v1/health
# ---------------------------------------------------------------------------


def test_v1_health_happy_path() -> None:
    client = _make_client()
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["version"] == "1"
    assert body["uptime_probe"] == "pass"
    assert body["telegram"] == "ready" or body["telegram"] == "unknown"


def test_v1_health_returns_error_envelope_on_exception() -> None:
    client = _make_client(runtime_lite_raises=RuntimeError("boom"))
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["version"] == "1"
    assert "boom" in body["error"]


# ---------------------------------------------------------------------------
# /api/ecosystem/health
# ---------------------------------------------------------------------------


def test_ecosystem_health_uses_injected_service() -> None:
    client = _make_client()
    resp = client.get("/api/ecosystem/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["report"]["risk_level"] == "low"
    assert body["report"]["session_12"] == {"foo": 1}


def test_ecosystem_health_fallback_when_service_missing() -> None:
    client = _make_client(deps_overrides={"health_service": None})
    with patch(
        "src.core.ecosystem_health.EcosystemHealthService",
        _FakeHealthSvc,
    ):
        resp = client.get("/api/ecosystem/health")
    assert resp.status_code == 200
    assert resp.json()["report"]["degradation"] == "minor"


# ---------------------------------------------------------------------------
# /api/ecosystem/health/debug
# ---------------------------------------------------------------------------


def test_ecosystem_health_debug_default_section() -> None:
    client = _make_client()
    resp = client.get("/api/ecosystem/health/debug")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direct"] == {"direct_session_12": True}
    assert body["full_has_session_12"] is True
    assert "session_12" in body["full_keys"]
    assert body["full_session_12"] == {"foo": 1}


def test_ecosystem_health_debug_section_filter() -> None:
    client = _make_client()
    resp = client.get("/api/ecosystem/health/debug?section=chain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["section_filter"] == "chain"
    assert body["full_section"] == ["openclaw", "krab_ear"]


# ---------------------------------------------------------------------------
# /api/ecosystem/health/export
# ---------------------------------------------------------------------------


def test_ecosystem_health_export_returns_file(tmp_path, monkeypatch) -> None:
    # Перенаправим artifacts/ops в tmp_path чтобы тест не мусорил в repo.
    monkeypatch.chdir(tmp_path)
    client = _make_client()
    with patch(
        "src.core.ecosystem_health.EcosystemHealthService",
        _FakeHealthSvc,
    ):
        resp = client.get("/api/ecosystem/health/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    # FileResponse stream — проверим, что файл создан в artifacts/ops
    ops_dir = tmp_path / "artifacts" / "ops"
    assert ops_dir.exists()
    files = list(ops_dir.glob("ecosystem_health_web_*.json"))
    assert len(files) == 1


# ---------------------------------------------------------------------------
# /api/health/deep — Phase 2 Wave CC (Session 25)
# ---------------------------------------------------------------------------


def test_get_health_deep_calls_collector_with_session_start() -> None:
    """Endpoint вызывает collect_health_deep с session_start_time из userbot."""
    from unittest.mock import AsyncMock

    class _Userbot:
        _session_start_time = 1234.5

    client = _make_client(deps_overrides={"userbot": _Userbot()})
    fake = AsyncMock(return_value={"krab": {"uptime_sec": 1}, "system": {}})
    with patch("src.core.health_deep_collector.collect_health_deep", new=fake):
        resp = client.get("/api/health/deep")
    assert resp.status_code == 200
    assert resp.json()["krab"]["uptime_sec"] == 1
    fake.assert_called_once_with(session_start_time=1234.5)


def test_get_health_deep_no_userbot_passes_none() -> None:
    """Без userbot endpoint всё равно работает (session_start=None)."""
    from unittest.mock import AsyncMock

    client = _make_client(deps_overrides={"userbot": None})
    fake = AsyncMock(return_value={"krab": {}, "system": {}})
    with patch("src.core.health_deep_collector.collect_health_deep", new=fake):
        resp = client.get("/api/health/deep")
    assert resp.status_code == 200
    fake.assert_called_once_with(session_start_time=None)
