# -*- coding: utf-8 -*-
"""
Тесты диагностических API endpoints web-панели Krab.

Покрываем:
  /api/system/diagnostics   — глубокая диагностика сервера
  /api/ops/diagnostics      — алиас system/diagnostics
  /api/ops/metrics          — срез внутренних метрик
  /api/ops/timeline         — лента событий
  /api/timeline             — алиас /api/ops/timeline
  /api/sla                  — latency p50/p95 + success rate
  /api/ops/runtime_snapshot — deep observability snapshot
  /api/diagnostics/smoke    — агрегированный owner-smoke (POST)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент без сетевых вызовов."""

    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "test", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _FakeRouter:
    """Роутер с минимальным набором атрибутов для diagnostics-endpoints."""

    active_tier: str = "default"
    local_engine: str = "lmstudio"
    _stats: dict = {"local_failures": 0, "cloud_failures": 0}
    _preflight_cache: dict = {}

    def get_model_info(self) -> dict:
        return {}

    # Атрибут openclaw_client нужен ops/runtime_snapshot
    @property
    def openclaw_client(self):
        return _FakeOpenClaw()


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
    """Создаёт WebApp со всеми заглушками в deps."""
    deps = {
        "router": _FakeRouter(),
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
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# /api/system/diagnostics
# ---------------------------------------------------------------------------


def test_system_diagnostics_returns_ok() -> None:
    """GET /api/system/diagnostics — структура ответа содержит ok и timestamp."""
    resp = _client().get("/api/system/diagnostics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "timestamp" in data


def test_system_diagnostics_has_local_ai_field() -> None:
    """Ответ /api/system/diagnostics содержит блок local_ai."""
    data = _client().get("/api/system/diagnostics").json()
    assert "local_ai" in data
    assert "available" in data["local_ai"]


def test_system_diagnostics_status_field() -> None:
    """Поле status присутствует и является строкой."""
    data = _client().get("/api/system/diagnostics").json()
    assert isinstance(data.get("status"), str)


# ---------------------------------------------------------------------------
# /api/ops/diagnostics — алиас
# ---------------------------------------------------------------------------


def test_ops_diagnostics_alias_matches_system() -> None:
    """/api/ops/diagnostics должен вернуть тот же контракт, что и /api/system/diagnostics."""
    client = _client()
    sys_data = client.get("/api/system/diagnostics").json()
    ops_data = client.get("/api/ops/diagnostics").json()
    # Оба возвращают ok=True и одинаковые ключи верхнего уровня
    assert ops_data["ok"] is True
    assert set(ops_data.keys()) == set(sys_data.keys())


# ---------------------------------------------------------------------------
# /api/ops/metrics
# ---------------------------------------------------------------------------


def test_ops_metrics_ok_field() -> None:
    """GET /api/ops/metrics — ok=True и наличие поля metrics."""
    resp = _client().get("/api/ops/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "metrics" in data


def test_ops_metrics_snapshot_is_dict() -> None:
    """Поле metrics содержит словарь (snapshot объекта метрик)."""
    data = _client().get("/api/ops/metrics").json()
    assert isinstance(data["metrics"], dict)


# ---------------------------------------------------------------------------
# /api/ops/timeline и /api/timeline
# ---------------------------------------------------------------------------


def test_ops_timeline_ok_and_events() -> None:
    """GET /api/ops/timeline — ok=True и поле events является списком."""
    resp = _client().get("/api/ops/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["events"], list)


def test_timeline_alias_returns_same_contract() -> None:
    """GET /api/timeline — алиас /api/ops/timeline, тот же контракт."""
    resp = _client().get("/api/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "events" in data


def test_ops_timeline_limit_param() -> None:
    """Параметр limit принимается без ошибки."""
    resp = _client().get("/api/ops/timeline?limit=5")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/sla
# ---------------------------------------------------------------------------


def test_sla_ok_field() -> None:
    """GET /api/sla — ok=True."""
    resp = _client().get("/api/sla")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_sla_has_latency_and_success_rate() -> None:
    """Ответ /api/sla содержит поля latency и success_rate_pct."""
    data = _client().get("/api/sla").json()
    assert "latency_p50_ms" in data
    assert "latency_p95_ms" in data
    assert "success_rate_pct" in data


def test_sla_success_rate_default_100() -> None:
    """При отсутствии счётчиков success_rate_pct должен быть 100.0 (нет отказов)."""
    data = _client().get("/api/sla").json()
    assert data["success_rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# /api/ops/runtime_snapshot
# ---------------------------------------------------------------------------


def test_ops_runtime_snapshot_ok() -> None:
    """GET /api/ops/runtime_snapshot — ok=True и ключевые поля присутствуют."""
    resp = _client().get("/api/ops/runtime_snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


def test_ops_runtime_snapshot_has_router_state() -> None:
    """Ответ содержит блок router_state."""
    data = _client().get("/api/ops/runtime_snapshot").json()
    assert "router_state" in data
    rs = data["router_state"]
    assert "active_tier" in rs


# ---------------------------------------------------------------------------
# /api/diagnostics/smoke  (POST)
# ---------------------------------------------------------------------------


def test_diagnostics_smoke_returns_ok_field() -> None:
    """POST /api/diagnostics/smoke — возвращает поле ok и checks."""
    with (
        patch(
            "src.modules.web_app.WebApp._collect_openclaw_browser_smoke_report",
            new=AsyncMock(return_value={"browser_smoke": {"ok": True, "detail": "smoke ok"}}),
        ),
        patch(
            "src.modules.web_app.WebApp._collect_openclaw_photo_smoke_payload",
            new=AsyncMock(
                return_value={
                    "available": True,
                    "report": {"photo_smoke": {"ok": True, "detail": "photo ok"}},
                }
            ),
        ),
    ):
        resp = _client().post("/api/diagnostics/smoke")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert isinstance(data.get("checks"), list)


def test_diagnostics_smoke_checks_names() -> None:
    """Список checks содержит записи browser_smoke и photo_smoke."""
    with (
        patch(
            "src.modules.web_app.WebApp._collect_openclaw_browser_smoke_report",
            new=AsyncMock(return_value={"browser_smoke": {"ok": True, "detail": "ok"}}),
        ),
        patch(
            "src.modules.web_app.WebApp._collect_openclaw_photo_smoke_payload",
            new=AsyncMock(
                return_value={
                    "available": True,
                    "report": {"photo_smoke": {"ok": True, "detail": "ok"}},
                }
            ),
        ),
    ):
        data = _client().post("/api/diagnostics/smoke").json()
    check_names = {c["name"] for c in data["checks"]}
    assert "browser_smoke" in check_names
    assert "photo_smoke" in check_names


def test_diagnostics_smoke_ok_aggregation() -> None:
    """Если оба smoke-check успешны — ok=True на уровне ответа."""
    with (
        patch(
            "src.modules.web_app.WebApp._collect_openclaw_browser_smoke_report",
            new=AsyncMock(return_value={"browser_smoke": {"ok": True, "detail": "ok"}}),
        ),
        patch(
            "src.modules.web_app.WebApp._collect_openclaw_photo_smoke_payload",
            new=AsyncMock(
                return_value={
                    "available": True,
                    "report": {"photo_smoke": {"ok": True, "detail": "ok"}},
                }
            ),
        ),
    ):
        data = _client().post("/api/diagnostics/smoke").json()
    assert data["ok"] is True
