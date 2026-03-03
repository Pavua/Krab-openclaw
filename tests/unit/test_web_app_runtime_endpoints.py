# -*- coding: utf-8 -*-
"""
Тесты runtime endpoint'ов web-панели.

Покрываем:
1) расширенный `/api/health/lite`;
2) `GET /api/runtime/handoff`;
3) `POST /api/runtime/recover` (guard + успешный dry-like запуск).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


class _DummyRouter:
    """Минимальный роутер-заглушка для инициализации WebApp."""

    def get_model_info(self):
        return {}


class _FakeOpenClaw:
    """Фейковый OpenClaw клиент для детерминированных тестов runtime endpoint'ов."""

    async def health_check(self) -> bool:
        return True

    def get_last_runtime_route(self):
        return {
            "channel": "local_direct",
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self):
        return {
            "active_tier": "free",
            "last_error_code": None,
            "last_provider_status": "ok",
            "last_recovery_action": "none",
        }

    async def get_cloud_runtime_check(self):
        return {"ok": True, "provider": "google", "active_tier": "free"}

    async def switch_cloud_tier(self, tier: str):
        return {"ok": True, "new_tier": tier}


class _FakeHealthClient:
    """Фейковый клиент сервиса с `health_check`."""

    def __init__(self, ok: bool = True):
        self._ok = ok

    async def health_check(self) -> bool:
        return self._ok


def _make_client() -> TestClient:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(ok=True),
        "krab_ear_client": _FakeHealthClient(ok=True),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def test_health_lite_contains_runtime_fields(monkeypatch):
    """
    `/api/health/lite` должен содержать новые runtime-поля,
    даже если внешний контур (LM Studio) в тесте недоступен.
    """
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "up"
    assert "telegram_session_state" in data
    assert "lmstudio_model_state" in data
    assert "openclaw_auth_state" in data
    assert "last_runtime_route" in data


def test_runtime_handoff_returns_machine_readable_snapshot(monkeypatch):
    """`/api/runtime/handoff` должен отдавать единый JSON-снимок для anti-413 handoff."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.get("/api/runtime/handoff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "generated_at_utc" in data
    assert "git" in data
    assert "runtime" in data
    assert "services" in data
    assert "artifacts" in data
    assert data["health_lite"]["last_runtime_route"]["model"] == "nvidia/nemotron-3-nano"


def test_runtime_recover_requires_web_api_key(monkeypatch):
    """Write endpoint `/api/runtime/recover` должен быть закрыт WEB_API_KEY при включенной защите."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    client = _make_client()

    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 403


def test_runtime_recover_minimal_flow(monkeypatch):
    """
    Минимальный recovery flow без запуска скриптов:
    endpoint должен отработать и вернуть post-check runtime.
    """
    monkeypatch.setenv("WEB_API_KEY", "secret")
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.post(
        "/api/runtime/recover",
        json={
            "run_openclaw_runtime_repair": False,
            "run_sync_openclaw_models": False,
            "force_tier": "free",
            "probe_cloud_runtime": True,
        },
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["steps"], list)
    assert data["runtime_after"]["last_runtime_route"]["model"] == "nvidia/nemotron-3-nano"
    assert data["cloud_runtime"]["available"] is True

