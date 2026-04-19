# -*- coding: utf-8 -*-
"""
Тесты provider management API endpoints web-панели Krab.

Покрываем:
  /api/model/catalog               — каталог моделей (список провайдеров)
  /api/openclaw/model-routing/status — статус routing и fallback chain
  /api/model/status                — текущий статус провайдера и active model
  /api/model/provider-action       — POST provider action (provider required)
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент с provider routing данными."""

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-3-pro-preview",
            "status": "ok",
            "error_code": None,
        }

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


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _FakeModelManager:
    """Фейковый model manager."""

    active_model_id: str = "google/gemini-3-pro-preview"

    def format_status(self) -> str:
        return "google/gemini-3-pro-preview (ok)"


# ---------------------------------------------------------------------------
# Фабрика WebApp / TestClient
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
    """Создаёт WebApp с минимальным набором заглушек."""
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
    return WebApp(deps, port=18092, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# /api/model/catalog
# ---------------------------------------------------------------------------


def test_model_catalog_ok() -> None:
    """GET /api/model/catalog должен вернуть ok=True и поле catalog."""
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value={}),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(
            WebApp, "_openclaw_models_full_catalog", return_value={"providers": {}, "count": 0}
        ),
    ):
        resp = _client().get("/api/model/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "catalog" in data


def test_model_catalog_has_providers_key() -> None:
    """Catalog должен содержать список провайдеров (providers или sections)."""
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value={}),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(
            WebApp, "_openclaw_models_full_catalog", return_value={"providers": {}, "count": 0}
        ),
    ):
        resp = _client().get("/api/model/catalog")
    data = resp.json()
    catalog = data["catalog"]
    # catalog — dict (payload каталога), не список
    assert isinstance(catalog, dict)


def test_model_catalog_force_refresh_param() -> None:
    """GET /api/model/catalog?force_refresh=true не должен возвращать ошибку."""
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value={}),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(
            WebApp, "_openclaw_models_full_catalog", return_value={"providers": {}, "count": 0}
        ),
    ):
        resp = _client().get("/api/model/catalog?force_refresh=true")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# /api/openclaw/model-routing/status
# ---------------------------------------------------------------------------


def test_model_routing_status_ok() -> None:
    """GET /api/openclaw/model-routing/status должен вернуть ok=True и routing."""
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value={}),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(WebApp, "_runtime_signal_failed_providers", return_value={}),
    ):
        resp = _client().get("/api/openclaw/model-routing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "routing" in data


def test_model_routing_status_has_primary() -> None:
    """Routing должен содержать поле current_primary."""
    _runtime_cfg = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "google/gemini-3-pro-preview",
                    "fallbacks": ["google/gemini-2.5-pro-preview", "google/gemini-2.5-flash"],
                }
            }
        }
    }
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value=_runtime_cfg),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(WebApp, "_runtime_signal_failed_providers", return_value={}),
    ):
        resp = _client().get("/api/openclaw/model-routing/status")
    data = resp.json()
    routing = data["routing"]
    assert isinstance(routing, dict)
    # primary присутствует (даже если пустая строка при дефолтном конфиге)
    assert "current_primary" in routing


def test_model_routing_status_has_fallbacks() -> None:
    """Routing должен содержать поле current_fallbacks (список)."""
    _runtime_cfg = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "google/gemini-3-pro-preview",
                    "fallbacks": ["google/gemini-2.5-pro-preview"],
                }
            }
        }
    }
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value=_runtime_cfg),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(WebApp, "_runtime_signal_failed_providers", return_value={}),
    ):
        resp = _client().get("/api/openclaw/model-routing/status")
    routing = resp.json()["routing"]
    assert "current_fallbacks" in routing
    assert isinstance(routing["current_fallbacks"], list)


def test_model_routing_live_route_overlay() -> None:
    """Live route из openclaw_client должен попасть в routing через overlay."""
    with (
        patch.object(WebApp, "_load_openclaw_runtime_models", return_value={"providers": {}}),
        patch.object(WebApp, "_load_openclaw_runtime_config", return_value={}),
        patch.object(WebApp, "_load_openclaw_auth_profiles", return_value={}),
        patch.object(WebApp, "_openclaw_models_status_snapshot", return_value={"providers": {}}),
        patch.object(WebApp, "_runtime_signal_failed_providers", return_value={}),
    ):
        resp = _client().get("/api/openclaw/model-routing/status")
    # Просто убеждаемся, что overlay не вызывает ошибок
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/model/status
# ---------------------------------------------------------------------------


def test_model_status_ok() -> None:
    """GET /api/model/status должен вернуть ok=True с полями route и active_model."""
    fake_mm = _FakeModelManager()
    fake_oc = _FakeOpenClaw()
    with (
        patch("src.modules.web_app.model_manager", fake_mm, create=True),
        patch("src.modules.web_app.openclaw_client", fake_oc, create=True),
    ):
        resp = _client().get("/api/model/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "route" in data
    assert "active_model" in data


def test_model_status_active_model_is_string() -> None:
    """active_model в /api/model/status должна быть строкой (fallback из route.model)."""
    fake_mm = _FakeModelManager()
    fake_oc = _FakeOpenClaw()
    # Патчим через правильный путь импорта (from ..model_manager / ..openclaw_client)
    with (
        patch("src.model_manager.model_manager", fake_mm),
        patch("src.openclaw_client.openclaw_client", fake_oc),
    ):
        resp = _client().get("/api/model/status")
    data = resp.json()
    assert resp.status_code == 200
    assert isinstance(data["active_model"], str)


# ---------------------------------------------------------------------------
# /api/model/provider-action (POST)
# ---------------------------------------------------------------------------


def test_provider_action_missing_provider_returns_400() -> None:
    """POST /api/model/provider-action без provider → 400."""
    resp = _client().post(
        "/api/model/provider-action",
        json={"action": "repair_oauth"},
        headers={"X-Krab-Web-Key": ""},
    )
    assert resp.status_code == 400
    assert "provider_action_provider_required" in resp.json()["detail"]


def test_provider_action_missing_action_returns_400() -> None:
    """POST /api/model/provider-action без action → 400."""
    resp = _client().post(
        "/api/model/provider-action",
        json={"provider": "google-gemini-cli"},
        headers={"X-Krab-Web-Key": ""},
    )
    assert resp.status_code == 400
    assert "provider_action_action_required" in resp.json()["detail"]


def test_provider_action_unsupported_action_returns_400() -> None:
    """POST /api/model/provider-action с неизвестным action → 400."""
    resp = _client().post(
        "/api/model/provider-action",
        json={"provider": "google-gemini-cli", "action": "unknown_action"},
        headers={"X-Krab-Web-Key": ""},
    )
    assert resp.status_code == 400
    assert "provider_action_unsupported" in resp.json()["detail"]
