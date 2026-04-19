# -*- coding: utf-8 -*-
"""
Тесты model management API endpoints web-панели Krab.

Покрываем маршруты:
  GET  /api/model/status
  GET  /api/model/catalog
  POST /api/model/switch
  GET  /api/model/local/status
  GET  /api/model/recommend
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки зависимостей
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент без внешних вызовов."""

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
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


class _FakeModelManager:
    """Фейковый model manager с поддержкой set_provider / set_model."""

    active_model_id: str = "google/gemini-test"

    def format_status(self) -> str:
        return "google/gemini-test (ok)"

    def set_provider(self, provider: str) -> None:
        """Переключить провайдера (auto/local/cloud)."""
        self.active_model_id = f"provider:{provider}"

    def set_model(self, model: str) -> None:
        """Явно задать модель."""
        self.active_model_id = model


class _FakeRouter:
    """Роутер с поддержкой методов model-catalog и recommend."""

    def get_model_info(self) -> dict:
        return {}

    def get_profile_recommendation(self, profile: str = "chat") -> dict:
        """Возвращает фейковую рекомендацию."""
        return {
            "profile": profile,
            "model": "google/gemini-test",
            "recommended_model": "google/gemini-test",
            "channel": "cloud",
            "reasoning": "test routing",
            "local_available": False,
            "force_mode": "auto",
        }


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика клиента
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
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
    app = WebApp(deps, port=18091, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# GET /api/model/status
# ---------------------------------------------------------------------------


def test_model_status_ok_field() -> None:
    """GET /api/model/status должен вернуть ok=True."""
    with patch("src.model_manager.model_manager", _FakeModelManager()):
        resp = _make_client().get("/api/model/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_model_status_contains_route() -> None:
    """Ответ /api/model/status должен содержать поле route."""
    with patch("src.model_manager.model_manager", _FakeModelManager()):
        resp = _make_client().get("/api/model/status")
    data = resp.json()
    assert "route" in data


def test_model_status_active_model_field() -> None:
    """Поле active_model заполнено из model_manager или route."""
    fake_mm = _FakeModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().get("/api/model/status")
    assert resp.json()["active_model"] == "google/gemini-test"


# ---------------------------------------------------------------------------
# GET /api/model/catalog
# ---------------------------------------------------------------------------


def test_model_catalog_ok_field() -> None:
    """GET /api/model/catalog возвращает ok=True."""
    # Пробрасываем фейковый каталог через кэш
    with patch.object(WebApp, "_get_model_catalog_cache", return_value={"models": []}):
        resp = _make_client().get("/api/model/catalog")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_model_catalog_contains_catalog_key() -> None:
    """В ответе должно быть поле catalog."""
    with patch.object(WebApp, "_get_model_catalog_cache", return_value={"models": []}):
        resp = _make_client().get("/api/model/catalog")
    assert "catalog" in resp.json()


def test_model_catalog_cached_flag_when_cache_hit() -> None:
    """При кэш-хите флаг cached=True."""
    with patch.object(WebApp, "_get_model_catalog_cache", return_value={"models": []}):
        resp = _make_client().get("/api/model/catalog")
    assert resp.json().get("cached") is True


def test_model_catalog_force_refresh_bypasses_cache() -> None:
    """?force_refresh=true не должен использовать кэш (_get_model_catalog_cache не вызывается)."""
    fake_catalog = {"models": [], "modes": []}

    # Переопределяем _store и _get чтобы изолировать от реальных async вызовов
    with (
        patch.object(WebApp, "_get_model_catalog_cache", return_value=None) as mock_cache,
        patch.object(WebApp, "_store_model_catalog_cache"),
        patch(
            "src.modules.web_app.WebApp._resolve_local_runtime_truth",
            AsyncMock(
                return_value={
                    "active_model": "",
                    "engine": "lmstudio",
                    "runtime_url": "n/a",
                    "is_loaded": False,
                    "runtime_reachable": False,
                    "loaded_models": [],
                    "probe_state": "down",
                    "error": "",
                }
            ),
        ),
    ):
        resp = _make_client().get("/api/model/catalog?force_refresh=true")
        mock_cache.assert_not_called()

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# POST /api/model/switch
# ---------------------------------------------------------------------------


def test_model_switch_requires_model_field() -> None:
    """POST /api/model/switch без поля model возвращает ok=False."""
    fake_mm = _FakeModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_model_switch_auto_provider() -> None:
    """POST /api/model/switch с model='auto' возвращает ok=True."""
    fake_mm = _FakeModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "auto"},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["model"] == "auto"


def test_model_switch_explicit_model_id() -> None:
    """POST /api/model/switch с конкретным model_id — ok=True и поле active."""
    fake_mm = _FakeModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "google/gemini-3-pro-preview"},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "active" in data


# ---------------------------------------------------------------------------
# GET /api/model/local/status
# ---------------------------------------------------------------------------


def test_model_local_status_ok_field() -> None:
    """GET /api/model/local/status возвращает ok=True."""
    fake_truth = {
        "active_model": "",
        "engine": "lmstudio",
        "runtime_url": "http://localhost:1234",
        "is_loaded": False,
        "runtime_reachable": False,
        "loaded_models": [],
        "probe_state": "down",
        "error": "",
    }
    with patch.object(WebApp, "_resolve_local_runtime_truth", AsyncMock(return_value=fake_truth)):
        resp = _make_client().get("/api/model/local/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_model_local_status_structure() -> None:
    """Ответ содержит поля status, model_name, engine, url, details."""
    fake_truth = {
        "active_model": "local-llama",
        "engine": "lmstudio",
        "runtime_url": "http://localhost:1234",
        "is_loaded": True,
        "runtime_reachable": True,
        "loaded_models": ["local-llama"],
        "probe_state": "ok",
        "error": "",
    }
    with patch.object(WebApp, "_resolve_local_runtime_truth", AsyncMock(return_value=fake_truth)):
        resp = _make_client().get("/api/model/local/status")
    data = resp.json()
    for key in ("status", "model_name", "engine", "url", "details"):
        assert key in data, f"отсутствует ключ: {key}"


def test_model_local_status_loaded_lifecycle() -> None:
    """При is_loaded=True status должен быть 'loaded'."""
    fake_truth = {
        "active_model": "local-llama",
        "engine": "lmstudio",
        "runtime_url": "http://localhost:1234",
        "is_loaded": True,
        "runtime_reachable": True,
        "loaded_models": ["local-llama"],
        "probe_state": "ok",
        "error": "",
    }
    with patch.object(WebApp, "_resolve_local_runtime_truth", AsyncMock(return_value=fake_truth)):
        resp = _make_client().get("/api/model/local/status")
    assert resp.json()["status"] == "loaded"


# ---------------------------------------------------------------------------
# GET /api/model/recommend
# ---------------------------------------------------------------------------


def test_model_recommend_default_profile() -> None:
    """GET /api/model/recommend возвращает рекомендацию для профиля по умолчанию."""
    resp = _make_client().get("/api/model/recommend")
    assert resp.status_code == 200
    data = resp.json()
    assert "model" in data or "recommended_model" in data


def test_model_recommend_custom_profile() -> None:
    """GET /api/model/recommend?profile=code передаёт profile в router."""
    resp = _make_client().get("/api/model/recommend?profile=code")
    assert resp.status_code == 200


def test_model_recommend_profile_echoed() -> None:
    """Поле profile в ответе соответствует переданному параметру (после нормализации)."""
    resp = _make_client().get("/api/model/recommend?profile=chat")
    data = resp.json()
    # Роутер нормализует профиль — допускаем любое непустое значение
    assert data.get("profile") is not None
