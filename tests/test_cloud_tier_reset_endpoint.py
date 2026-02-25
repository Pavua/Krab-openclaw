# -*- coding: utf-8 -*-
"""
unit-тесты web endpoints для Cloud Tier Reset/State (Sprint R23).

Проверяет:
- GET /api/openclaw/cloud/tier/state → возвращает tier_state и метрики.
- POST /api/openclaw/cloud/tier/reset без ключа → 403.
- POST /api/openclaw/cloud/tier/reset с ключом → ok, new_tier=free.
- Когда openclaw_client не имеет метода → graceful degradation.

Связанные файлы: src/modules/web_app.py, tests/test_web_app.py
"""

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


# ─── Stub зависимостей ────────────────────────────────────────────────────────

class _DummyRouter:
    """Минимальный stub ModelRouter для тестов web_app."""
    def __init__(self):
        self.rag = None
        self.force_mode = "auto"
        self.models = {"chat": "google/gemini-2.5-flash"}
        self.is_local_available = False

    async def check_local_health(self):
        return False

    def get_model_info(self):
        return {"force_mode": "auto", "local_available": False}


class _DummyOpenClawWithTier:
    """Stub openclaw_client с поддержкой tier state (R23 методы)."""

    def __init__(self, initial_tier: str = "free"):
        self._active_tier = initial_tier
        self._metrics = {
            "cloud_attempts_total": 5,
            "cloud_failures_total": 1,
            "tier_switch_total": 1,
            "force_cloud_failfast_total": 0,
        }

    async def health_check(self):
        return True

    async def get_health_report(self):
        return {"gateway": True, "auth": {"available": True}}

    def get_tier_state_export(self):
        """Возвращает tier state без секретов."""
        return {
            "active_tier": self._active_tier,
            "last_switch_at": 1.0,
            "switch_reason": "init",
            "sticky_paid": False,
            "switch_count": 1,
            "available_tiers": ["free", "paid"],
            "autoswitch_cooldown_sec": 60,
            "sticky_on_paid_config": True,
            "metrics": dict(self._metrics),
        }

    async def reset_cloud_tier(self):
        """Имитирует сброс на free tier."""
        prev = self._active_tier
        self._active_tier = "free"
        return {
            "ok": True,
            "previous_tier": prev,
            "new_tier": "free",
            "reset_at": 999999.0,
        }


class _DummyOpenClawNoTier:
    """Stub openclaw_client БЕЗ tier state методов (старая версия)."""
    async def health_check(self):
        return True

    async def get_health_report(self):
        return {"gateway": True}


# ─── Фикстура клиента ─────────────────────────────────────────────────────────

def _build_client(openclaw=None, web_api_key: str = "") -> tuple[TestClient, dict]:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": openclaw or _DummyOpenClawWithTier(),
        "black_box": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
    }
    app = WebApp(deps=deps, port=8080)
    client = TestClient(app.app, raise_server_exceptions=False)
    return client, deps


# ─── Тесты GET /api/openclaw/cloud/tier/state ─────────────────────────────────

def test_tier_state_endpoint_returns_tier_info():
    """GET /api/openclaw/cloud/tier/state возвращает active_tier и метрики."""
    client, _ = _build_client()
    response = client.get("/api/openclaw/cloud/tier/state")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    tier = payload["data"]["tier_state"]
    assert "active_tier" in tier
    assert "metrics" in tier
    assert tier["active_tier"] in {"free", "paid", "default"}


def test_tier_state_endpoint_metrics_structure():
    """tier_state содержит все ожидаемые метрики."""
    client, _ = _build_client()
    response = client.get("/api/openclaw/cloud/tier/state")
    assert response.status_code == 200
    metrics = response.json()["data"]["tier_state"]["metrics"]
    expected_keys = {
        "cloud_attempts_total",
        "cloud_failures_total",
        "tier_switch_total",
        "force_cloud_failfast_total",
    }
    assert expected_keys.issubset(set(metrics.keys()))


def test_tier_state_endpoint_graceful_when_no_method():
    """Если openclaw_client не поддерживает tier state — возвращает graceful ответ."""
    client, _ = _build_client(openclaw=_DummyOpenClawNoTier())
    response = client.get("/api/openclaw/cloud/tier/state")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert "tier_state_not_supported" in payload["error_code"]


def test_tier_state_endpoint_graceful_when_no_client():
    """Если openclaw_client отсутствует — возвращает available=False (status=failed)."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": None,
    }
    app = WebApp(deps=deps, port=8080)
    client = TestClient(app.app, raise_server_exceptions=False)
    response = client.get("/api/openclaw/cloud/tier/state")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


# ─── Тесты POST /api/openclaw/cloud/tier/reset ────────────────────────────────

def test_tier_reset_endpoint_requires_auth(monkeypatch):
    """POST /api/openclaw/cloud/tier/reset без ключа → failed status с 403 fallback."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key-123")
    client, _ = _build_client()
    response = client.post("/api/openclaw/cloud/tier/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "forbidden" in response.json()["error_code"]
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_tier_reset_endpoint_accepts_valid_key(monkeypatch):
    """POST /api/openclaw/cloud/tier/reset с X-Krab-Web-Key → status=ok."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key-123")
    client, _ = _build_client(openclaw=_DummyOpenClawWithTier(initial_tier="paid"))
    response = client.post(
        "/api/openclaw/cloud/tier/reset",
        headers={"X-Krab-Web-Key": "secret-key-123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    result = payload["data"]["result"]
    assert result["new_tier"] == "free"
    assert result["previous_tier"] == "paid"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_tier_reset_endpoint_accepts_token_query(monkeypatch):
    """POST /api/openclaw/cloud/tier/reset с token query → status=ok."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key-123")
    client, _ = _build_client()
    response = client.post(
        "/api/openclaw/cloud/tier/reset",
        params={"token": "secret-key-123"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_tier_reset_endpoint_returns_new_tier_free(monkeypatch):
    """После reset new_tier = 'free'."""
    monkeypatch.setenv("WEB_API_KEY", "my-key")
    client, _ = _build_client(openclaw=_DummyOpenClawWithTier(initial_tier="paid"))
    response = client.post(
        "/api/openclaw/cloud/tier/reset",
        headers={"X-Krab-Web-Key": "my-key"},
    )
    assert response.status_code == 200
    result = response.json()["data"]["result"]
    assert result["new_tier"] == "free"
    assert "reset_at" in result
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_tier_reset_endpoint_graceful_when_no_client(monkeypatch):
    """POST /api/openclaw/cloud/tier/reset без openclaw_client → failed status."""
    monkeypatch.setenv("WEB_API_KEY", "my-key")
    deps = {"router": _DummyRouter(), "openclaw_client": None}
    app = WebApp(deps=deps, port=8080)
    client = TestClient(app.app, raise_server_exceptions=False)
    response = client.post(
        "/api/openclaw/cloud/tier/reset",
        headers={"X-Krab-Web-Key": "my-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "openclaw_client_not_configured"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_tier_reset_endpoint_no_auth_when_no_web_api_key():
    """Если WEB_API_KEY не задан — reset доступен без ключа (open mode)."""
    import os
    os.environ.pop("WEB_API_KEY", None)
    client, _ = _build_client()
    response = client.post("/api/openclaw/cloud/tier/reset")
    # В open mode любой может сбросить — 200 OK
    assert response.status_code == 200
